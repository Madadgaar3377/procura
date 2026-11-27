"""Real database/API workflow; provider transport is explicitly simulated separately."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from sqlalchemy import select,func,create_engine,text
from sqlalchemy.engine import make_url

from app.models import Category, Invitation, Material, Outbox, PriceBookItem, Quotation, QuoteLine, RFQ, RFQItem, Organization
from app.modules.quotations.router import router as quotation_router, invoice
from app.modules.matching.guest import invitation_token
from app.notification_worker import process_batch
from test_rfq_publish_matching_fallback import setup_matching_app


@pytest.fixture(params=['sqlite','postgresql'])
def flow(request):
    engine=None;admin=None;schema=None
    if request.param=='postgresql':
        url=os.environ.get('TEST_PROCUREMENT_POSTGRES_URL')
        if not url:pytest.skip('Set TEST_PROCUREMENT_POSTGRES_URL to an isolated PostgreSQL test database')
        from alembic.config import Config
        from alembic import command
        schema='procurement_test_'+uuid4().hex
        admin=create_engine(url,isolation_level='AUTOCOMMIT')
        with admin.connect() as conn:conn.execute(text(f'CREATE DATABASE "{schema}"'))
        engine=create_engine(make_url(url).set(database=schema),connect_args={'options':'-c lock_timeout=100'})
        with engine.connect() as conn:
            config=Config('alembic.ini');config.attributes['connection']=conn
            command.upgrade(config,'head')
    client,sessions,engine,ids=setup_matching_app('',engine=engine)
    client.app.include_router(quotation_router,prefix='/api/v1')
    with sessions() as db:
        leaf=db.scalar(select(Category).where(Category.parent_id.is_not(None)))
        item=db.scalar(select(RFQItem).where(RFQItem.rfq_id==ids['rfq']))
        material=Material(code='AUTO-001',name_en=item.description,category_id=leaf.id,main_category_id=leaf.parent_id,subcategory_id=leaf.id,unit='EA')
        db.add(material);db.flush();item.material_id=material.id;item.quantity=Decimal('10')
        rate=PriceBookItem(org_id=ids['supplier'],material_id=material.id,unit='EA',unit_price=Decimal('12.50'),currency='SAR',price_basis='supply',valid_until=datetime.now(UTC)+timedelta(days=30),technical_confirmed=True,vat_rate=Decimal('.05'))
        db.add(rate);db.flush();ids.update(material=material.id,item=item.id,rate=rate.id)
        org=db.get(Organization,ids['supplier']);org.business_email='sales@example.invalid'
        db.commit()
    yield client,sessions,ids
    engine.dispose()
    if admin:
        with admin.connect() as conn:conn.execute(text(f'DROP DATABASE "{schema}"'))
        admin.dispose()


def publish_and_prepare(client,sessions,ids):
    result=client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
    assert result.status_code==200,result.text
    with sessions() as db:
        jobs=db.scalars(select(Outbox.id).where(Outbox.event=='rfq.invitation')).all()
        assert len(jobs)==1
        assert process_batch(db,event_ids=jobs)==jobs
        # Process a replay, even after it had been marked complete.
        job=db.get(Outbox,jobs[0]);job.status='pending';db.commit()
        assert process_batch(db,event_ids=jobs)==jobs


@pytest.mark.parametrize('case,expected_lines',[('complete',1),('partial',1),('missing',0),('expired',0),('specification',0),('minimum',0),('unit',0),('stock',0)])
def test_publish_prepares_only_eligible_prices_and_is_idempotent(flow,case,expected_lines):
    client,sessions,ids=flow
    with sessions() as db:
        rate=db.get(PriceBookItem,ids['rate']);item=db.get(RFQItem,ids['item'])
        if case=='partial':db.add(RFQItem(rfq_id=ids['rfq'],main_category_id=item.main_category_id,subcategory_id=item.subcategory_id,description='Different requested item',specifications='',unit='EA',quantity=2))
        if case=='missing':rate.active=False
        if case=='expired':rate.valid_until=datetime.now(UTC)-timedelta(days=1)
        if case=='specification':item.specifications='Different voltage, 480V'
        if case=='minimum':rate.min_order_quantity=11
        if case=='unit':item.unit='M'
        if case=='stock':rate.stock_quantity=Decimal('9')
        db.commit()
    publish_and_prepare(client,sessions,ids)
    with sessions() as db:
        quotes=db.scalars(select(Quotation)).all()
        assert len(quotes)==(1 if expected_lines else 0)
        assert db.scalar(select(func.count()).select_from(Invitation))==1
        if quotes:
            quote=quotes[0];assert quote.status=='draft' and quote.submission_source=='automatic_draft'
            lines=db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()
            assert len(lines)==expected_lines
            assert lines[0].source_rate_snapshot['unit_price']=='12.50'
            assert invoice(db,quote)=={'subtotal':'125.00','vat':'6.25','total':'131.25','currency':'SAR'}
            rate=db.get(PriceBookItem,ids['rate']);rate.unit_price=999;db.commit()
            assert lines[0].source_rate_snapshot['unit_price']=='12.50'
            if case=='partial':assert any('Missing active price' in reason for reason in quote.review_reasons)


def test_guest_explicit_submission_reaches_buyer_comparison_and_revokes(flow):
    client,sessions,ids=flow
    publish_and_prepare(client,sessions,ids)
    with sessions() as db:
        inv=db.scalar(select(Invitation));rfq=db.get(RFQ,ids['rfq'])
        token=invitation_token(inv,rfq,{'channel':'email','destination':'sales@example.invalid','template':'rfq_invitation'})
    opened=client.post('/api/v1/matching/guest-invitations/open',json={'token':token})
    assert opened.status_code==200,opened.text
    assert 'source_rate_snapshot' not in str(opened.json())
    body={'token':token,'quotation':{'lines':[{'rfq_item_id':ids['item'],'quantity':'10','unit_price':'12.50','lead_days':4,'compliance':'compliant'}],'commercial_terms':{'payment_terms':'30 days'}},'confirm_submission':False}
    saved=client.post('/api/v1/matching/guest-invitations/quotation',json=body)
    assert saved.status_code==200,saved.text
    comparison=f"/api/v1/quotations/rfqs/{ids['rfq']}/comparison"
    assert client.get(comparison).json()['quotations']==[]
    body['confirm_submission']=True
    submitted=client.post('/api/v1/matching/guest-invitations/quotation',json=body)
    assert submitted.status_code==200,submitted.text
    assert client.post('/api/v1/matching/guest-invitations/quotation',json=body).status_code==409
    result=client.get(comparison);assert result.status_code==200,result.text
    assert len(result.json()['quotations'])==1
    assert result.json()['quotations'][0]['quotation']['commercial_terms']['payment_terms']=='30 days'
    with sessions() as db:
        events=db.scalars(select(Outbox.id).where(Outbox.event=='quotation.submitted')).all()
        assert len(events)==1
        assert process_batch(db,event_ids=events)==events
        db.get(Organization,ids['supplier']).is_active=False;db.commit()
    assert client.post('/api/v1/matching/guest-invitations/open',json={'token':token}).status_code==403
    assert client.post('/api/v1/matching/guest-invitations/open',json={'token':token+'invalid'}).status_code==401


def test_quotation_failure_rolls_back_notifications_and_retries(flow,monkeypatch):
    client,sessions,ids=flow
    assert client.post(f"/api/v1/rfqs/{ids['rfq']}/publish").status_code==200
    from app.modules.quotations import automation
    original=automation.prepare_invitation_quote
    def fail(*args):raise RuntimeError('temporary preparation failure')
    monkeypatch.setattr(automation,'prepare_invitation_quote',fail)
    with sessions() as db:
        job=db.scalar(select(Outbox).where(Outbox.event=='rfq.invitation'));key=job.id
        assert process_batch(db,event_ids=[key])==[]
        assert db.get(Outbox,key).status=='retry'
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event=='notification.delivery'))==0
    monkeypatch.setattr(automation,'prepare_invitation_quote',original)
    with sessions() as db:
        assert process_batch(db,event_ids=[key],now=datetime.now(UTC)+timedelta(minutes=1))==[key]
        assert db.scalar(select(func.count()).select_from(Quotation))==1


def test_postgresql_actual_lock_timeout_recovers_without_duplicate_matches(flow):
    client,sessions,ids=flow
    with sessions() as db:
        if db.bind.dialect.name!='postgresql':pytest.skip('PostgreSQL row locks required')
    publish_and_prepare(client,sessions,ids)
    with sessions() as db:
        job=db.scalar(select(Outbox).where(Outbox.event=='rfq.published'))
        job.status='pending';key=job.id;db.commit()
    with sessions() as blocker:
        blocker.scalar(select(RFQ).where(RFQ.id==ids['rfq']).with_for_update())
        with sessions() as db:
            assert process_batch(db,event_ids=[key])==[]
            assert db.get(Outbox,key).status=='retry'
            assert db.get(Outbox,key).last_error=='OperationalError'
        blocker.rollback()
    with sessions() as db:
        assert process_batch(db,event_ids=[key],now=datetime.now(UTC)+timedelta(minutes=1))==[key]
        assert db.scalar(select(func.count()).select_from(Invitation))==1
        assert db.scalar(select(func.count()).select_from(Quotation))==1


@pytest.mark.parametrize('authorized',['complete','exceeds_limit'])
def test_automatic_preparation_requires_supplier_approval(flow,authorized):
    from app.models import User
    client,sessions,ids=flow
    with sessions() as db:
        db.get(RFQ,ids['rfq']).city='ABC'
        db.get(User,ids['user']).phone_verified=True
        db.get(Organization,ids['supplier']).quote_automation={
            'enabled':True,'authorized_by':ids['user'],
            'authorized_at':(datetime.now(UTC)-timedelta(hours=1)).isoformat(),
            'valid_until':(datetime.now(UTC)+timedelta(days=1)).isoformat(),
            'confirm_terms':True,'service_cities':['ABC'],'material_ids':[ids['material']],
            'max_quantity':100,'max_total':1000 if authorized=='complete' else 1,
            'price_basis':'supply','vat_rate':'0.15','revision':1}
        db.commit()
    publish_and_prepare(client,sessions,ids)
    with sessions() as db:
        quote=db.scalar(select(Quotation))
        assert quote.status=='draft'
        assert quote.review_required
        assert quote.submission_source=='automatic_draft'
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event=='quotation.submitted'))==0


def test_material_responsibility_does_not_notify_unrelated_employee(flow):
    client,sessions,ids=flow
    from app.models import Capability,Contact,ContactCapability,Member,User,Notification
    with sessions() as db:
        cap=db.scalar(select(Capability).where(Capability.org_id==ids['supplier'],Capability.category_id==db.get(Material,ids['material']).subcategory_id))
        for index,name in enumerate(('responsible','unrelated')):
            user=User(username=name,phone=f'+96650000000{index}',phone_e164=f'+96650000000{index}',email=f'{name}@example.invalid',email_notifications_enabled=True,password_hash='x')
            contact=Contact(org_id=ids['supplier'],name=name,email=user.email)
            db.add_all([user,contact]);db.flush()
            db.add(Member(organization_id=ids['supplier'],user_id=user.id,role='supplier'))
            db.add(ContactCapability(contact_id=contact.id,capability_id=cap.id,material_ids=[ids['material']] if index==0 else ['another-material']))
        db.commit()
    publish_and_prepare(client,sessions,ids)
    with sessions() as db:
        jobs=db.scalars(select(Outbox).where(Outbox.event=='notification.delivery')).all()
        emails=[j.payload.get('destination') for j in jobs if j.payload.get('channel')=='email']
        assert emails==['responsible@example.invalid']
        notified=set(db.scalars(select(User.username).join(Notification,Notification.recipient_user_id==User.id).where(Notification.event=='rfq.invitation')))
        assert notified=={'responsible'}
