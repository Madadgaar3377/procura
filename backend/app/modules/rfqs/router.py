from datetime import UTC, datetime
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError, OperationalError
import time

from app.database import get_db
from app.models import RFQ, Category, Invitation, Material, MeasurementUnit, Member, Organization, Outbox, RFQItem, User
from app.response_contracts import RFQAttachmentOut, RFQAttachmentRemovalOut, RFQDetail, RFQOut
from app.schemas import RFQIn, RFQReminderIn
from app.search import ListSearch
from app.security import as_utc, audit, current_user, org_member
from app.serializers import record

from pydantic import BaseModel, Field

router=APIRouter(prefix='/rfqs',tags=['Buyer RFQs'])
from .imports import router as material_imports
from .exports import router as rfq_exports
router.include_router(material_imports)
router.include_router(rfq_exports)
log=logging.getLogger(__name__)


class MaterialClassifyIn(BaseModel):
    description: str = Field(min_length=2, max_length=250)
    specifications: str = Field(default='', max_length=2000)
    unit: str | None = Field(default=None, max_length=40)
    ai_enabled: bool = True


@router.post('/classify-material')
def classify_material(
    payload: MaterialClassifyIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Hybrid material classification for buyer RFQ entry (deterministic first, Gemini only if unresolved/ambiguous)."""
    from app.rate_limit import enforce_rate_limit
    from .material_matching import classify_material_description

    enforce_rate_limit(request, scope='rfq-classify', identity=user.id, limit=120, window_seconds=60)
    return classify_material_description(
        db,
        payload.description,
        specifications=payload.specifications,
        unit=payload.unit,
        ai_enabled=payload.ai_enabled,
    )

def contents(db,rfq):
    data=record(rfq)
    attachments=data.pop('attachment_records', []) or []
    from app.models import StoredFile
    attachments += [{'id':f.id,'filename':f.safe_filename,'content_type':f.content_type,'size':f.size_bytes}
        for f in db.scalars(select(StoredFile).where(StoredFile.entity_type=='rfq',StoredFile.entity_id==rfq.id,
            StoredFile.status=='available',StoredFile.deleted_at.is_(None))).all()]
    return {'rfq':data,'items':[record(i) for i in db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id).order_by(RFQItem.created_at,RFQItem.id)).all()],
            'attachments':[{key:entry[key] for key in ('id','filename','content_type','size')} for entry in attachments]}

def prepare_line(db, entry, *, unit_cache=None, parents_with_children=None):
    """Snapshot classification; buyer text belongs only to the RFQ item."""
    from app.modules.materials.service import ensure_unit
    mat=db.get(Material,entry.material_id) if entry.material_id else None
    if entry.material_id and (not mat or not mat.active):raise HTTPException(422,'Unknown material')
    leaf=db.get(Category,mat.category_id) if mat else None
    main_id=entry.main_category_id or (mat.main_category_id or (leaf.parent_id if leaf else None) or mat.category_id if mat else None)
    sub_id=entry.subcategory_id or (mat.subcategory_id or (leaf.id if leaf and leaf.parent_id else None) if mat else None)
    main=db.get(Category,main_id) if main_id else None
    sub=db.get(Category,sub_id) if sub_id else None
    if not main or not main.active or main.parent_id:raise HTTPException(422,'Choose an active main category')
    has_children=(main.id in parents_with_children) if parents_with_children is not None else (not mat and not sub_id and db.scalar(select(Category.id).where(Category.parent_id==main.id,Category.active.is_(True)).limit(1)))
    if not mat and not sub_id and has_children:
        raise HTTPException(422,'Choose a subcategory for this main category')
    if sub_id and (not sub or not sub.active or sub.parent_id!=main.id):raise HTTPException(422,'Subcategory must belong to the main category')
    if mat:
        expected_main=mat.main_category_id or (leaf.parent_id if leaf else None) or mat.category_id
        expected_sub=mat.subcategory_id or (leaf.id if leaf and leaf.parent_id else None)
        if (main_id,sub_id)!=(expected_main,expected_sub):raise HTTPException(422,'Selected material does not belong to this category and subcategory')
    if not entry.description.strip():raise HTTPException(422,'Enter a description')
    ensure_unit(db,entry.unit,create_unknown=False,cache=unit_cache)
    # Existing clients may send legacy unit spelling; retain the RFQ snapshot.
    return {**entry.model_dump(),'main_category_id':main_id,'subcategory_id':sub_id}


def prepare_lines(db,entries):
    """Keep reference records in the identity map for large RFQ POST/PUT bodies."""
    from app.modules.materials.service import normalize_unit
    materials=list(db.scalars(select(Material).where(Material.id.in_({e.material_id for e in entries}-{None}))))
    ids={value for e in entries for value in (e.main_category_id,e.subcategory_id) if value}
    ids.update(value for m in materials for value in (m.category_id,m.main_category_id,m.subcategory_id) if value)
    categories=list(db.scalars(select(Category).where(Category.id.in_(ids))))
    parent_ids={c.parent_id for c in categories if c.parent_id}-ids
    if parent_ids:categories.extend(db.scalars(select(Category).where(Category.id.in_(parent_ids))))
    roots={c.id for c in categories if not c.parent_id}
    parents=set(db.scalars(select(Category.parent_id).where(Category.parent_id.in_(roots),Category.active.is_(True))))
    codes={normalize_unit(e.unit) for e in entries}
    units=list(db.scalars(select(MeasurementUnit).where(MeasurementUnit.code.in_(codes))))
    if any(not unit.active for unit in units):raise HTTPException(422,'Choose an active approved unit')
    cache={unit.code:unit.code for unit in units}
    return [prepare_line(db,entry,unit_cache=cache,parents_with_children=parents) for entry in entries]


@router.post('',status_code=201,response_model=RFQDetail)
def create(payload:RFQIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,payload.buyer_org_id,{'owner','manager','buyer'})
    org=db.get(Organization,payload.buyer_org_id)
    if not org or not org.is_buyer:raise HTTPException(422,'Organization cannot buy')
    if payload.client_request_id:
        existing=db.scalar(select(RFQ).where(RFQ.buyer_org_id==org.id,RFQ.client_request_id==payload.client_request_id))
        if existing:return contents(db,existing)
    if payload.response_deadline.astimezone(UTC)<datetime.now(UTC):raise HTTPException(422,'Deadline must be in the future')
    lines=prepare_lines(db,payload.items)
    rfq=RFQ(reference='RFQ-'+__import__('uuid').uuid4().hex[:12].upper(),buyer_org_id=org.id,client_request_id=payload.client_request_id,title=payload.title,city=payload.city,response_deadline=payload.response_deadline,required_qualifications=payload.required_qualifications,project_name=payload.project_name,required_delivery_date=payload.required_delivery_date,additional_instructions=payload.additional_instructions,delivery_address=payload.delivery_address)
    try:
        db.add(rfq);db.flush()
        for entry in lines:db.add(RFQItem(rfq_id=rfq.id,**entry))
        audit(db,user,'rfq.created',org.id,rfq.id);db.commit()
    except IntegrityError:
        db.rollback()
        if not payload.client_request_id:raise
        existing=db.scalar(select(RFQ).where(RFQ.buyer_org_id==org.id,RFQ.client_request_id==payload.client_request_id))
        if not existing:raise
        rfq=existing
    return contents(db,rfq)

@router.get('',response_model=list[RFQOut])
def mine(status:str|None=None,search:ListSearch=Depends(),user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_ids=select(Member.organization_id).join(Organization,Organization.id==Member.organization_id).where(Member.user_id==user.id,Organization.is_active.is_(True))
    q=select(RFQ)
    if not user.is_admin:q=q.where(RFQ.buyer_org_id.in_(org_ids))
    if search.org_id:
        org_member(db,user,search.org_id)
        q=q.where(RFQ.buyer_org_id==search.org_id)
    if status:
        if status not in {'draft','published','collecting_quotes','review','closed','cancelled','expired'}:
            raise HTTPException(422,'Invalid RFQ status')
        q=q.where(RFQ.status==status)
    return [record(r) for r in db.scalars(search.apply(q,RFQ.reference,RFQ.title,RFQ.city,RFQ.status,order=(RFQ.created_at.desc(),RFQ.id))).all()]

@router.get('/{rfq_id}',response_model=RFQDetail)
def details(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    # Buyer or invited supplier may read; denied callers get 404.
    allowed=user.is_admin or db.scalar(select(Member.id).join(Organization,Organization.id==Member.organization_id).where(Member.user_id==user.id,Member.organization_id==rfq.buyer_org_id,Organization.is_active.is_(True)))
    if not allowed:
        sub=select(Member.organization_id).join(Organization,Organization.id==Member.organization_id).where(Member.user_id==user.id,Organization.is_active.is_(True))
        allowed=db.scalar(select(Invitation.id).where(Invitation.rfq_id==rfq_id,Invitation.supplier_org_id.in_(sub)))
    if not allowed:raise HTTPException(404,'RFQ not found')
    return contents(db,rfq)

@router.post('/{rfq_id}/publish')
def publish(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status in {'published','collecting_quotes'}:
        return {'rfq_id':rfq.id,'status':rfq.status,'next':'Supplier matching queued'}
    if rfq.status!='draft':raise HTTPException(409,'RFQ is not a draft')
    if as_utc(rfq.response_deadline)<=datetime.now(UTC):raise HTTPException(422,'Deadline passed')
    # Drafts can outlive reference-data changes. Recheck their classification at
    # publication without rewriting the buyer's immutable material snapshots.
    items=db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id)).all()
    if not items:raise HTTPException(422,'Add RFQ items before publishing')
    category_ids={value for item in items for value in (item.main_category_id,item.subcategory_id) if value}
    categories={category.id:category for category in db.scalars(select(Category).where(Category.id.in_(category_ids))).all()}
    for item in items:
        # Legacy items without snapshot IDs retain their existing material path.
        if not item.main_category_id:continue
        main=categories.get(item.main_category_id)
        sub=categories.get(item.subcategory_id) if item.subcategory_id else None
        if not main or not main.active or main.parent_id or (item.subcategory_id and (not sub or not sub.active or sub.parent_id!=main.id)):
            raise HTTPException(422,'An RFQ category is inactive or changed. Review the draft before publishing.')
    rfq.status='published';rfq.published_at=datetime.now(UTC)
    db.add(Outbox(event='rfq.published',payload={'rfq_id':rfq.id},dedupe_key=f'rfq-published:{rfq.id}'))
    audit(db,user,'rfq.published',rfq.buyer_org_id,rfq.id)
    db.commit()

    # Matching runs immediately after publication commits, page by page, until the
    # configured time budget is spent. Any remainder continues in the durable outbox.
    # Notifications and quotation drafts are queued jobs and never block this step.
    initial_matching=None
    from app.config import get_settings
    from app.modules.matching.service import run_matching_job
    job_id=db.scalar(select(Outbox.id).where(Outbox.dedupe_key==f'rfq-published:{rfq_id}'))
    for attempt in range(3):
        try:
            # A leased job belongs to the worker; the helper skips it instead of
            # overwriting the worker's cursor.
            initial_matching=run_matching_job(db.get_bind(),job_id,
                time_budget=get_settings().matching_publish_time_budget_seconds) if job_id else None
            break
        except OperationalError as exc:
            if getattr(getattr(exc,'orig',None),'sqlstate',None) not in {'55P03','40P01','40001'} or attempt==2:
                log.exception('Initial RFQ matching failed rfq_id=%s',rfq_id)
                break
        except Exception:
            log.exception('Initial RFQ matching failed rfq_id=%s',rfq_id)
            break
        time.sleep(0.05*(attempt+1))

    return {'rfq_id':rfq_id,'status':'published','next':'Supplier matching queued',
            'matching_status':('processing' if initial_matching and initial_matching.get('next_cursor') else 'completed') if initial_matching is not None else 'queued',
            **({'matched_suppliers':initial_matching['eligible_suppliers'],'invited_suppliers':initial_matching['new_invitations']} if initial_matching is not None else {})}

@router.post('/{rfq_id}/close',response_model=RFQOut)
def close(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status not in {'published','collecting_quotes','review'}:raise HTTPException(409,'Cannot close RFQ')
    rfq.status='closed'
    db.add(Outbox(event='rfq.status_updated',payload={'rfq_id':rfq.id,'status':'closed'},dedupe_key=f'rfq-status:{rfq.id}:closed'))
    audit(db,user,'rfq.closed',rfq.buyer_org_id,rfq.id);db.commit();return record(rfq)

@router.put('/{rfq_id}', response_model=RFQDetail)
def update_draft(rfq_id:str,payload:RFQIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status!='draft':raise HTTPException(409,'Only drafts may be edited')
    if payload.buyer_org_id!=rfq.buyer_org_id:raise HTTPException(422,'Cannot change RFQ organization')
    if payload.response_deadline.astimezone(UTC)<=datetime.now(UTC):raise HTTPException(422,'Deadline must be in the future')
    lines=prepare_lines(db,payload.items)
    for item in db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id)).all():db.delete(item)
    db.flush()
    for line in lines:db.add(RFQItem(rfq_id=rfq.id,**line))
    rfq.title=payload.title;rfq.city=payload.city;rfq.response_deadline=payload.response_deadline
    rfq.required_qualifications=payload.required_qualifications
    rfq.project_name=payload.project_name;rfq.required_delivery_date=payload.required_delivery_date
    rfq.additional_instructions=payload.additional_instructions
    rfq.delivery_address=payload.delivery_address
    audit(db,user,'rfq.draft_updated',rfq.buyer_org_id,rfq.id);db.commit()
    return contents(db,rfq)

@router.post('/{rfq_id}/cancel',response_model=RFQOut)
def cancel(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status in {'closed','cancelled'}:raise HTTPException(409,'RFQ already finished')
    rfq.status='cancelled'
    db.add(Outbox(event='rfq.status_updated',payload={'rfq_id':rfq.id,'status':'cancelled'},dedupe_key=f'rfq-status:{rfq.id}:cancelled'))
    audit(db,user,'rfq.cancelled',rfq.buyer_org_id,rfq.id);db.commit()
    return record(rfq)


@router.post('/{rfq_id}/reminders')
def remind_pending(rfq_id:str,request:Request,payload:RFQReminderIn|None=None,user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Queue pending supplier reminders with a shared rolling 24-hour cooldown."""
    from datetime import timedelta

    from app.modules.notifications.procurement import pending_orgs
    from app.rate_limit import enforce_rate_limit
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    now=datetime.now(UTC)
    deadline=rfq.response_deadline
    if deadline.tzinfo is None:deadline=deadline.replace(tzinfo=UTC)
    if rfq.status not in {'published','collecting_quotes'} or deadline<=now:
        raise HTTPException(409,'RFQ is closed or past its deadline')
    enforce_rate_limit(request,scope='rfq-reminders',identity=user.id,limit=10,window_seconds=3600)
    recent=db.scalar(select(Outbox.id).where(Outbox.event=='rfq.deadline.reminder',
        Outbox.payload['rfq_id'].as_string()==rfq.id,Outbox.created_at>now-timedelta(hours=24)).limit(1))
    if recent:raise HTTPException(409,'A reminder was already queued within the last 24 hours')
    pending=set(pending_orgs(db,rfq))
    selected=None
    if payload and payload.invitation_ids is not None:
        selected=set(payload.invitation_ids)
        invitations=db.scalars(select(Invitation).where(Invitation.rfq_id==rfq.id,Invitation.id.in_(selected))).all()
        if len(invitations)!=len(selected) or any(inv.supplier_org_id not in pending for inv in invitations):
            raise HTTPException(422,'Select only eligible pending invitations belonging to this RFQ')
        pending={inv.supplier_org_id for inv in invitations}
    count=len(pending)
    if not count:return {'queued':False,'pending_suppliers':0}
    context:dict[str,object]={'rfq_id':rfq.id}
    if selected is not None:context['invitation_ids']=sorted(selected)
    job=Outbox(event='rfq.deadline.reminder',payload=context)
    db.add(job);audit(db,user,'rfq.reminder_queued',rfq.buyer_org_id,rfq.id,count=count)
    db.commit()
    return {'queued':True,'pending_suppliers':count}


@router.post('/{rfq_id}/attachments', status_code=201, response_model=RFQAttachmentOut)
def upload_attachment(rfq_id:str, request:Request, file:UploadFile=File(...), user:User=Depends(current_user), db:Session=Depends(get_db)):
    """Private development attachments; production waits for storage/scanning integration."""
    import io
    from pathlib import Path
    from uuid import uuid4

    from app.config import get_settings
    from app.import_storage import LocalImportStorage

    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status!='draft':raise HTTPException(409,'Only draft attachments may be changed')
    settings=get_settings()
    if settings.is_production and not settings.r2_enabled:raise HTTPException(503,'Private attachment storage is not configured')
    from app.models import StoredFile
    from app.storage import (ALLOWED_DOCUMENT_TYPES, StorageUnavailable, StorageValidationError,
        cleanup_object_best_effort, safe_filename, upload_file as r2_upload, validate_file_content)
    stored_count=db.scalar(select(func.count(StoredFile.id)).where(StoredFile.entity_type=='rfq',
        StoredFile.entity_id==rfq.id,StoredFile.status.in_(['pending_upload','available']),StoredFile.deleted_at.is_(None))) or 0
    if len(rfq.attachment_records or [])+stored_count>=10:raise HTTPException(422,'At most 10 RFQ attachments are allowed')
    max_bytes=min(settings.max_upload_bytes,settings.r2_max_document_bytes)
    content=file.file.read(max_bytes+1)
    try:
        suffix,ct=validate_file_content(file.filename,file.content_type,content,ALLOWED_DOCUMENT_TYPES,max_bytes)
    except StorageValidationError as exc:
        raise HTTPException(exc.status_code,exc.message) from exc
    filename=safe_filename(file.filename,suffix)
    if settings.r2_enabled:
        from app.rate_limit import enforce_rate_limit
        enforce_rate_limit(request,scope='api-r2-upload',identity=user.id,limit=10,window_seconds=60)
        object_key=f'private/rfq/{rfq.id}/{uuid4()}{suffix}'
        buyer_org_id=rfq.buyer_org_id
        # Release any read transaction before network I/O to avoid idle-in-transaction / 55P03 locks.
        db.rollback()
        uploaded=False
        try:
            result=r2_upload('private',object_key,io.BytesIO(content),ct,max_bytes=max_bytes)
            uploaded=True
        except StorageUnavailable as exc:
            raise HTTPException(503,'File storage is temporarily unavailable') from exc
        try:
            locked_rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
            if not locked_rfq or locked_rfq.status!='draft':
                raise HTTPException(409,'Only draft attachments may be changed')
            stored=StoredFile(bucket_type='private',object_key=object_key,
                original_filename=filename,safe_filename=filename,content_type=ct,file_extension=suffix,
                size_bytes=result['size_bytes'],etag=result['etag'],checksum_sha256=result['checksum_sha256'],
                visibility='private',purpose='rfq_attachment',status='available',
                owner_user_id=user.id,uploaded_by_user_id=user.id,organization_id=buyer_org_id,
                entity_type='rfq',entity_id=locked_rfq.id)
            db.add(stored)
            db.flush()
            audit(db,user,'rfq.attachment_uploaded',buyer_org_id,locked_rfq.id,file_id=stored.id)
            db.commit()
        except Exception:
            db.rollback()
            if uploaded:cleanup_object_best_effort('private',object_key)
            raise
        return {'id':stored.id,'filename':stored.safe_filename,'content_type':stored.content_type,'size':stored.size_bytes}
    attachment_id=uuid4().hex
    storage=LocalImportStorage(str(Path(settings.upload_dir)/'rfq-attachments'))
    key=rfq.id+'/'+attachment_id
    entry={'id':attachment_id,'filename':filename,'content_type':ct,'size':len(content),'storage_key':key}
    storage.write(key,io.BytesIO(content),settings.max_upload_bytes)
    try:
        locked_rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
        if not locked_rfq or locked_rfq.status!='draft':
            raise HTTPException(409,'Only draft attachments may be changed')
        locked_rfq.attachment_records=[*(locked_rfq.attachment_records or []),entry]
        audit(db,user,'rfq.attachment_uploaded',locked_rfq.buyer_org_id,locked_rfq.id,attachment_id=attachment_id)
        db.commit()
    except Exception:
        db.rollback();storage.delete(key);raise
    return {key:entry[key] for key in ('id','filename','content_type','size')}


@router.get('/{rfq_id}/attachments/{attachment_id}', response_class=FileResponse, responses={
    200: {'description':'Authorized private RFQ attachment', 'content':{
        kind:{'schema':{'type':'string','format':'binary'}} for kind in ('application/pdf','image/png','image/jpeg')}}})
def download_attachment(rfq_id:str,attachment_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from pathlib import Path

    from app.config import get_settings
    from app.import_storage import LocalImportStorage

    details(rfq_id,user,db)  # Same buyer/invited-supplier authorization as RFQ details.
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    from app.models import StoredFile
    stored=db.scalar(select(StoredFile).where(StoredFile.id==attachment_id,StoredFile.entity_type=='rfq',
        StoredFile.entity_id==rfq.id,StoredFile.status=='available',StoredFile.deleted_at.is_(None)))
    if stored:
        from fastapi.responses import RedirectResponse
        from app.storage import StorageUnavailable,generate_download_url
        try:url=generate_download_url('private',stored.object_key,stored.safe_filename,stored.content_type)
        except StorageUnavailable as exc:raise HTTPException(503,'File storage is temporarily unavailable') from exc
        audit(db,user,'file.download_authorized',rfq.buyer_org_id,stored.id);db.commit()
        return RedirectResponse(url,status_code=307,headers={'Cache-Control':'private, no-store','X-Robots-Tag':'noindex, nofollow'})
    entry=next((entry for entry in (rfq.attachment_records or []) if entry['id']==attachment_id),None)
    if not entry:raise HTTPException(404,'Attachment not found')
    settings=get_settings()
    if settings.is_production:raise HTTPException(503,'Attachment malware scanning is not configured')
    storage=LocalImportStorage(str(Path(settings.upload_dir)/'rfq-attachments'))
    target=storage._target(entry['storage_key'])
    if not target.is_file():raise HTTPException(404,'Attachment not found')
    return FileResponse(target,media_type=entry['content_type'],filename=entry['filename'],
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','X-Robots-Tag':'noindex, nofollow'})


@router.delete('/{rfq_id}/attachments/{attachment_id}', response_model=RFQAttachmentRemovalOut)
def remove_attachment(rfq_id:str,attachment_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from pathlib import Path

    from app.config import get_settings
    from app.import_storage import LocalImportStorage

    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status!='draft':raise HTTPException(409,'Only draft attachments may be changed')
    from app.models import StoredFile
    stored=db.scalar(select(StoredFile).where(StoredFile.id==attachment_id,StoredFile.entity_type=='rfq',StoredFile.entity_id==rfq.id))
    if stored and stored.deleted_at is None:
        from datetime import UTC,datetime
        from app.storage import StorageUnavailable,delete_object
        stored.status='deleted';stored.deleted_at=datetime.now(UTC)
        audit(db,user,'rfq.attachment_removed',rfq.buyer_org_id,rfq.id,attachment_id=stored.id);db.commit()
        try:delete_object('private',stored.object_key)
        except StorageUnavailable:pass
        return {'removed':True}
    entry=next((entry for entry in (rfq.attachment_records or []) if entry['id']==attachment_id),None)
    if not entry:raise HTTPException(404,'Attachment not found')
    rfq.attachment_records=[item for item in rfq.attachment_records if item['id']!=attachment_id]
    audit(db,user,'rfq.attachment_removed',rfq.buyer_org_id,rfq.id,attachment_id=attachment_id)
    db.commit()
    LocalImportStorage(str(Path(get_settings().upload_dir)/'rfq-attachments')).delete(entry['storage_key'])
    return {'removed':True}
