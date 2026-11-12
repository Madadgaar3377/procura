import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeoutError, DBAPIError
from app.config import get_settings
from app.cors import with_cors
from app.database import SessionLocal, engine
from app.rate_limit import enforce_rate_limit, ping_redis
from app.modules.auth.router import router as auth
from app.modules.auth.phone_change import router as phone_change
from app.modules.organizations.router import router as organizations
from app.modules.taxonomy.router import router as taxonomy
from app.modules.imports.router import process_queued_supplier_imports, router as imports
from app.modules.imports.materials import process_queued_material_imports
from app.modules.capabilities.router import router as capabilities
from app.modules.capabilities.directory import router as directory
from app.modules.rfqs.router import router as rfqs
from app.modules.matching.router import router as matching
from app.modules.pricebooks.router import router as pricebooks
from app.modules.quotations.router import router as quotations
from app.modules.notifications.router import router as notifications
from app.modules.memberships.router import router as memberships
from app.modules.admin.router import router as admin
from app.modules.admin import analytics as admin_analytics  # register read-only analytics
from app.modules.database.router import router as database_status
from app.modules.reports.router import router as reports
from app.modules.contact.router import router as contact
from app.modules.files.router import router as files

settings=get_settings()
settings.validate_r2()
logger=logging.getLogger(__name__)
if settings.is_production:
    if settings.jwt_secret in {'CHANGE_ME_FOR_LOCAL_ONLY',''} or len(settings.jwt_secret)<32:
        raise RuntimeError('Production requires a strong JWT_SECRET')
    if not settings.database_url.startswith('postgresql'):
        raise RuntimeError('Production must use PostgreSQL')
    if not settings.redis_url:
        raise RuntimeError('Production requires Redis-backed throttling and rate limits')
    if not settings.field_encryption_key:
        raise RuntimeError('Production requires FIELD_ENCRYPTION_KEY')
    if not (settings.sms_gate_username and settings.sms_gate_password):
        raise RuntimeError('Production requires configured SMS Gateway credentials')
    if not (settings.smtp_username and settings.smtp_password and settings.smtp_from_email):
        raise RuntimeError('Production requires configured Hostinger SMTP credentials')
    # Production remains blocked until independent testing/review covers storage,
    # scanning, admin MFA, legal controls, and multi-worker failure behavior.
    raise RuntimeError('Production readiness gate: private R2 storage, malware scanning, admin MFA, legal review and production concurrency testing remain incomplete')

def _process_import_queue_once() -> None:
    with SessionLocal() as db:
        process_queued_material_imports(db)
        process_queued_supplier_imports(db)
        from app.modules.imports.supplier_jobs import process_supplier_jobs
        process_supplier_jobs(db)


async def _development_import_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(_process_import_queue_once)
        except Exception:
            logger.exception('Development import worker poll failed')
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.redis_client import get_redis, close_redis
    # Handlers and rate limiting use synchronous SQLAlchemy/Redis in worker
    # threads; keep pool lifecycle operations off the event loop too.
    await asyncio.to_thread(get_redis)
    worker = None
    if settings.app_env.casefold() == 'development':
        worker = asyncio.create_task(_development_import_worker(), name='development-import-worker')
        logger.info('Development material and supplier import worker started with the API')
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await asyncio.to_thread(close_redis)


app=FastAPI(title='Saudi B2B Procurement RFQ API',version='0.1.0',description='Working development MVP: buyer, supplier, RFQ, matching, quotes, admin. No production guarantees.',lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Pydantic's default input/context can echo passwords, codes and tokens.
    errors=[{'loc':error['loc'],'msg':error['msg'],'type':error['type']} for error in exc.errors()]
    return JSONResponse(status_code=422,content={'detail':errors})


@app.middleware('http')
async def headers(request:Request,call_next):
    supplied=request.headers.get('X-Request-ID','')
    request_id=supplied if re.fullmatch(r'[A-Za-z0-9._-]{1,80}',supplied) else str(uuid.uuid4())
    request.state.correlation_id=request_id
    from time import perf_counter
    started=perf_counter()
    try:
        if request.url.path == '/api/v1/contact':
            if int(request.headers.get('content-length') or 0) > 16_384:
                return JSONResponse(status_code=413,content={'detail':'Contact request is too large'})
            original_receive=request._receive
            received=0
            async def bounded_contact_receive():
                nonlocal received
                message=await original_receive()
                if message['type']=='http.request':
                    received+=len(message.get('body',b''))
                    if received>16_384:
                        raise HTTPException(413,'Contact request is too large')
                return message
            request._receive=bounded_contact_receive
        if request.url.path.startswith('/api/'):
            cookie_mutation=request.method not in {'GET','HEAD','OPTIONS'} and (request.cookies.get('procura_access') or request.cookies.get('procura_refresh') or request.url.path.startswith('/api/v1/auth/browser/'))
            allowed_origins=set(settings.cors_origin_list)
            if cookie_mutation and request.headers.get('origin') not in allowed_origins:
                raise HTTPException(403,'Request origin rejected')
            await asyncio.to_thread(enforce_rate_limit,request,scope='api-ip',identity='all',limit=1200,window_seconds=60)
            authorization=request.headers.get('Authorization','')
            principal=authorization if authorization.startswith('Bearer ') else request.cookies.get('procura_access','anonymous')
            await asyncio.to_thread(enforce_rate_limit,
                request,
                scope='api-principal',
                identity=principal,
                limit=600 if principal!='anonymous' else 300,
                window_seconds=60,
            )
            if request.method in {'POST','PUT'} and (
                request.url.path.startswith('/api/v1/imports/')
                or request.url.path.endswith('/bulk-import')
            ):
                await asyncio.to_thread(enforce_rate_limit,
                    request,
                    scope='api-file-import',
                    identity=principal,
                    limit=10,
                    window_seconds=60,
                )
        response=await call_next(request)
    except HTTPException as exc:
        response=JSONResponse(status_code=exc.status_code,content={'detail':exc.detail},headers=exc.headers)
    except (OperationalError,PoolTimeoutError) as exc:
        from app.database_diagnostics import classify, error_metadata
        safe=error_metadata(exc)
        logger.warning('Database unavailable request_id=%s type=%s category=%s sqlstate=%s signals=%s',
            request_id,type(exc).__name__,classify(exc)['category'],safe['sqlstate'],','.join(safe['error_signals']))
        response=JSONResponse(status_code=503,content={'detail':'Database is busy or unavailable. Please try again shortly.'},headers={'Retry-After':'3'})
    except DBAPIError as exc:
        if getattr(exc.orig,'sqlstate',None) in {'55P03','57014'}:
            response=JSONResponse(status_code=503,content={'detail':'Database lock or query timed out. Please try again shortly.'},headers={'Retry-After':'3'})
        else:
            logging.warning('Database request failed request_id=%s type=%s',request_id,type(exc).__name__)
            response=JSONResponse(status_code=500,content={'detail':'The database could not complete this request'})
    except Exception as exc:
        # Driver exceptions may include SQL parameters, secrets or connection URLs.
        logger.error('Unhandled API error request_id=%s type=%s',request_id,type(exc).__name__)
        response=JSONResponse(status_code=500,content={'detail':'Internal server error'})
    response.headers['Server-Timing']=f'backend;dur={(perf_counter()-started)*1000:.1f}'
    response.headers['X-Request-ID']=request_id
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Cache-Control']='no-store'
    return response

for r in (auth,phone_change,organizations,taxonomy,imports,capabilities,directory,rfqs,matching,pricebooks,quotations,notifications,memberships,admin,database_status,reports,contact,files):
    app.include_router(r,prefix='/api/v1')

@app.get('/health/live',tags=['Health'])
def live():return {'status':'alive'}

@app.get('/health/ready',tags=['Health'])
def ready():
    try:
        with engine.connect() as conn:conn.execute(text('SELECT 1'))
        redis_ready=None
        if settings.redis_url:
            ping_redis()
            redis_ready=True
        return {'status':'ready','database':True,'redis':redis_ready}
    except Exception:return JSONResponse(status_code=503,content={'status':'not_ready'})


# Wrap the complete ASGI app so CORS headers are also added to error responses
# generated by FastAPI/Starlette's outer exception handlers.
app = with_cors(app, settings.cors_origin_list)
