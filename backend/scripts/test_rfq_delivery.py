"""Controlled RFQ API -> durable outbox -> actual providers; no supplier data.

Default is read-only preparation. --approved requires explicit controlled targets.
The persistent isolated SQLite ledger prevents repeat submissions on reruns.
An authenticated synthetic buyer exists only in this local test application.
This does not establish deployment or real production buyer authorization.
"""
import argparse
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved',action='store_true')
    parser.add_argument('--email',action='append',default=[],help='Controlled recipient, maximum two')
    parser.add_argument('--phone',default='',help='Controlled test mobile; one SMS maximum')
    args=parser.parse_args()
    from app.config import get_settings
    settings=get_settings()
    if not args.approved:
        print(json.dumps({'mode':'prepare_only','sends':0,'smtp_configured':bool(settings.smtp_username and settings.smtp_password),
            'reply_to_configured':bool(settings.smtp_reply_to),'mock':settings.mock_notifications,
            'required':'Explicit controlled --email / --phone targets and --approved; never use supplier contacts'}))
        return 0
    from app.modules.notifications.recipients import normalize_destination
    emails=list(dict.fromkeys(normalize_destination('email',e) for e in args.email))
    phone=normalize_destination('sms',args.phone) if args.phone else None
    if not (emails or phone) or len(emails)>2:
        parser.error('Provide one or two controlled mailboxes and/or one test mobile')
    if settings.mock_notifications or (emails and not settings.smtp_reply_to):
        parser.error('Disable MOCK_NOTIFICATIONS and configure a real monitored SMTP_REPLY_TO for email')
    if phone and (not settings.sms_gateway_enabled or settings.sms_gateway_mode!='live'):
        parser.error('Enable the live SMS gateway for the controlled device')
    directory=ROOT/'data'/'private'/'rfq-delivery-tests';directory.mkdir(parents=True,exist_ok=True)
    fingerprint=sha256(json.dumps([emails,phone]).encode()).hexdigest()[:24]
    database=directory/(fingerprint+'.sqlite')
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.database import Base, get_db
    from app.models import (Capability, Category, Invitation, Member, Organization,
        Outbox, RFQ, RFQItem, User)
    from app.notification_worker import process_batch
    from app.modules.rfqs.router import router
    from app.security import current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    engine=create_engine('sqlite:///'+database.as_posix(),connect_args={'check_same_thread':False})
    # Only this new synthetic SQLite DB; never the configured PostgreSQL database.
    Base.metadata.create_all(engine)
    sessions=sessionmaker(engine,expire_on_commit=False)
    def session():
        with sessions() as db:yield db
    with sessions() as db:
        rfq=db.scalar(select(RFQ))
        if not rfq:
            buyer=Organization(name='Controlled RFQ Test Buyer',name_normalized='controlled rfq test buyer',is_supplier=False)
            user=User(username='controlled-rfq-test',phone=phone or '+966500000000',password_hash='no-login-isolated-test')
            category=Category(code='CONTROLLED-RFQ',name_en='Controlled test discipline')
            db.add_all([buyer,user,category]);db.flush()
            leaf=Category(code='CONTROLLED-RFQ-LEAF',name_en='Controlled test material',parent_id=category.id)
            db.add(leaf);db.flush()
            db.add(Member(user_id=user.id,organization_id=buyer.id,role='owner'))
            rfq=RFQ(reference='RFQ-CONTROLLED-'+fingerprint[:8],buyer_org_id=buyer.id,
                title='Controlled RFQ delivery test',status='draft',response_deadline=datetime.now(UTC)+timedelta(days=2))
            db.add(rfq);db.flush()
            db.add(RFQItem(rfq_id=rfq.id,main_category_id=category.id,subcategory_id=leaf.id,
                description='Controlled delivery test only; no purchase required',specifications='',unit='EA',quantity=1))
            for i,email in enumerate(emails or [None]):
                supplier=Organization(name='Controlled Test Recipient '+str(i+1),name_normalized='controlled test recipient '+str(i+1),
                    is_buyer=False,is_supplier=True,source='imported',verified_status='unverified',
                    business_email=email,business_phone=phone if i==0 else None)
                db.add(supplier);db.flush()
                db.add(Capability(org_id=supplier.id,category_id=leaf.id,city='',confirmed=True,accepts_rfqs=True))
            db.commit()
        user=db.scalar(select(User));rfq_id=rfq.id
    app=FastAPI();app.include_router(router,prefix='/api/v1')
    app.dependency_overrides[get_db]=session
    app.dependency_overrides[current_user]=lambda:user
    with TestClient(app) as client:
        response=client.post(f'/api/v1/rfqs/{rfq_id}/publish')
        response.raise_for_status()
    # Never drain SessionLocal or the production queue. Job identities survive reruns.
    for _ in range(4):
        with sessions() as db:
            ids=db.scalars(select(Outbox.id)).all()
            process_batch(db,event_ids=ids)
    with sessions() as db:
        jobs=db.scalars(select(Outbox).where(Outbox.event=='notification.delivery')).all()
        report={'test_id':fingerprint,'environment':'isolated SQLite API with actual configured providers',
            'production_database_touched':False,'rfq_id':rfq_id,
            'invitations':len(db.scalars(select(Invitation)).all()),
            'results':[{'job_id':job.id,'channel':job.payload['channel'],'status':job.status,
                'provider_status':job.delivery_status,'message_id':job.provider_message_id,
                'response_code':job.payload.get('provider_response_code'),
                'smtp_accepted':job.payload.get('smtp_accepted',False),'error':job.last_error} for job in jobs],
            'mailbox_placement':'Unconfirmed: record Inbox / Promotions / Spam and Authentication-Results separately',
            'device_receipt':'Confirm receipt on the controlled device; provider status may remain pending'}
    engine.dispose()
    evidence=directory/(fingerprint+'.json');evidence.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if jobs and all(job.status in {'processed','awaiting_status','skipped'} for job in jobs) else 1


if __name__=='__main__':raise SystemExit(main())
