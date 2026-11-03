"""One approved recovery SMS and email through API -> isolated outbox -> worker.
Default is prepare-only. Requires explicit test recipients in the ignored .env.
Never processes the configured database queue or supplier contacts.
"""
from pathlib import Path
import sys,os,json,argparse,hashlib,time
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--approved',action='store_true',help='Use only after approval for the configured single SMS and email')
    args=parser.parse_args()
    from app.config import get_settings
    settings=get_settings()
    summary={'mode':'prepare','sms_live':not settings.mock_notifications and settings.sms_gateway_enabled and settings.sms_gateway_mode=='live',
        'sms_credentials_present':bool(settings.sms_gate_username and settings.sms_gate_password),
        'smtp_credentials_present':bool(settings.smtp_username and settings.smtp_password),
        'test_phone_configured':bool(settings.notification_test_phone),'test_email_configured':bool(settings.notification_test_email)}
    if not args.approved:print(json.dumps(summary));return 0
    if not all(summary[key] for key in ('sms_live','sms_credentials_present','smtp_credentials_present','test_phone_configured','test_email_configured')):
        print(json.dumps({**summary,'result':'blocked','reason':'Configure explicit NOTIFICATION_TEST_PHONE and NOTIFICATION_TEST_EMAIL and live credentials before approval'}));return 1
    from app.modules.notifications.delivery import normalize_sms_phone,get_sms_status,DeliveryError
    from app.modules.auth.router import norm_email
    phone=normalize_sms_phone(settings.notification_test_phone);email=norm_email(settings.notification_test_email)
    directory=ROOT/'data'/'private'/'controlled-delivery-tests';directory.mkdir(parents=True,exist_ok=True)
    fingerprint=hashlib.sha256((phone+'|'+email).encode()).hexdigest()[:24]
    ledger=directory/(fingerprint+'.json')
    try:
        descriptor=os.open(ledger,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        print(json.dumps({'result':'blocked','reason':'This recipient pair already has an attempted test. No repeat sends are permitted without additional approval.'}));return 1
    with os.fdopen(descriptor,'w') as f:json.dump({'started':True,'test_id':fingerprint},f)
    from sqlalchemy.orm import sessionmaker
    from app.database import Base,build_engine,get_db
    from app.main import app
    from app.models import Outbox,User
    from app.modules.notifications.crypto import decrypt_delivery_secret
    from app.notification_worker import process_batch
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    database=directory/(fingerprint+'.sqlite')
    assert not database.exists(),'Refusing to reuse a test database'
    engine=build_engine('sqlite:///'+database.as_posix());Base.metadata.create_all(engine)
    session=sessionmaker(engine,expire_on_commit=False,autoflush=False)
    def local_db():
        with session() as db:yield db
    app.dependency_overrides[get_db]=local_db
    # Dispatch only through the isolated session below, never SessionLocal.
    previous_dispatch=settings.auth_delivery_auto_dispatch
    settings.auth_delivery_auto_dispatch=False
    report={'test_id':fingerprint,'database':'isolated SQLite; configured database untouched','jobs':[]}
    try:
        with TestClient(app) as client:
            old_password=uuid4().hex;new_password=uuid4().hex
            # Synthetic isolated account; these destinations require explicit owner approval.
            from app.security import password_hash
            with session() as db:
                db.add(User(username='delivery_test_'+uuid4().hex[:10],phone=phone,email=email,password_hash=password_hash(old_password),phone_verified=True,email_verified=True))
                db.commit()
            login=client.post('/api/v1/auth/login',json={'identifier':email,'password':old_password})
            assert login.status_code==200
            requested=client.post('/api/v1/auth/password/forgot',json={'phone':phone})
            report['recovery_request_http_status']=requested.status_code
            with session() as db:
                jobs=db.scalars(select(Outbox).where(Outbox.event=='notification.delivery')).all()
                assert len(jobs)==2 and {j.payload['channel'] for j in jobs}=={'sms','email'}
                code=decrypt_delivery_secret(jobs[0].secret_ciphertext)
                ids=[j.id for j in jobs]
            for job_id in ids:
                with session() as db:process_batch(db,event_ids=[job_id])
            with session() as db:
                for job_id in ids:
                    job=db.get(Outbox,job_id)
                    if job.payload['channel']=='sms' and job.status=='awaiting_status':
                        for _ in range(3):
                            try:
                                status=get_sms_status(job.provider_message_id)
                                from app.notification_worker import apply_sms
                                from datetime import datetime,timezone
                                job.status_checks+=1;apply_sms(db,job,status,datetime.now(timezone.utc));db.commit()
                                if job.status!='awaiting_status':break
                            except DeliveryError:break
                            time.sleep(1)
                    report['jobs'].append({'outbox_job_id':job.id,'channel':job.payload['channel'],'worker_status':job.status,
                        'provider_response_code':job.payload.get('provider_response_code'),
                        'provider_message_id':job.provider_message_id if job.delivery_status not in {'submitting',None} else None,
                        'provider_status':job.delivery_status,'error':job.last_error,
                        'smtp_result':'accepted; final inbox delivery unconfirmed' if job.payload['channel']=='email' and job.delivery_status=='accepted' else None})
            report['actual_receipt']='Pending separate inbox and device confirmation by the approved recipient'
            verified=client.post('/api/v1/auth/password/verify',json={'identifier':email,'code':code})
            report['otp_verification_http_status']=verified.status_code
            if verified.status_code==200:
                reset=client.post('/api/v1/auth/password/reset',json={'reset_token':verified.json()['reset_token'],'new_password':new_password})
                report['password_reset_http_status']=reset.status_code
                report['old_password_rejected']=client.post('/api/v1/auth/login',json={'identifier':email,'password':old_password}).status_code==401
                report['new_password_accepted']=client.post('/api/v1/auth/login',json={'identifier':email,'password':new_password}).status_code==200
                report['refresh_session_revoked']=client.post('/api/v1/auth/refresh',json={'refresh_token':login.json()['refresh_token']}).status_code==401
    except Exception as exc:report['error_type']=type(exc).__name__
    finally:
        settings.auth_delivery_auto_dispatch=previous_dispatch
        app.dependency_overrides.pop(get_db,None);engine.dispose()
        ledger.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if report.get('password_reset_http_status')==200 and all(report.get(key) for key in ('old_password_rejected','new_password_accepted','refresh_session_revoked')) and len(report['jobs'])==2 and all(j['worker_status'] in {'processed','awaiting_status'} for j in report['jobs']) else 1

if __name__=='__main__':raise SystemExit(main())
