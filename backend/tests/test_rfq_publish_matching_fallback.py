from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Capability, Category, Invitation, Member, Notification, Organization, Outbox, RFQ, RFQItem, RFQMatch, User
from app.modules.matching.router import router as matching_router
from app.modules.rfqs.router import router as rfq_router
from app.security import current_user


def setup_matching_app(delivery_city=''):
    engine=create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    with sessions() as db:
        buyer=Organization(name='Buyer Company',name_normalized='buyer company',is_buyer=True,is_supplier=False,city='Buyer Office')
        supplier=Organization(name='Supplier Tech',name_normalized='supplier tech',is_buyer=False,is_supplier=True,verified_status='verified',city='ABC')
        user=User(username='buyer-owner',phone='+966511111111',phone_e164='+966511111111',password_hash='x',is_active=True)
        db.add_all([buyer,supplier,user]);db.flush()
        db.add(Member(user_id=user.id,organization_id=buyer.id,role='owner'))
        db.add(Member(user_id=user.id,organization_id=supplier.id,role='owner'))
        main=Category(code='ARCH-SPEC',name_en='Architectural Specialties',normalized_name='architectural specialties',active=True)
        db.add(main);db.flush()
        sub=Category(code='TOILET-ACC',name_en='Toilet Partitions, Mirrors & Accessories',normalized_name='toilet partitions mirrors accessories',parent_id=main.id,active=True)
        db.add(sub);db.flush()
        db.add(Capability(org_id=supplier.id,category_id=sub.id,city='ABC',confirmed=True,accepts_rfqs=True,eligibility_status='enabled',material_ids=[]))
        rfq=RFQ(reference='RFQ-TEST-MATCH',buyer_org_id=buyer.id,title='Washroom Accessories',city=delivery_city,status='draft',response_deadline=datetime.now(UTC)+timedelta(days=7))
        db.add(rfq);db.flush()
        db.add(RFQItem(rfq_id=rfq.id,main_category_id=main.id,subcategory_id=sub.id,description='Non-plumbed Washroom Accessories',specifications='',unit='EA',quantity=Decimal('10000')))
        db.commit();ids={'buyer':buyer.id,'supplier':supplier.id,'user':user.id,'rfq':rfq.id}
    app=FastAPI();app.include_router(rfq_router,prefix='/api/v1');app.include_router(matching_router,prefix='/api/v1')
    def session_override():
        with sessions() as db:yield db
    app.dependency_overrides[get_db]=session_override
    with sessions() as db:actor=db.get(User,ids['user'])
    app.dependency_overrides[current_user]=lambda:actor
    return TestClient(app),sessions,engine,ids


def test_publish_runs_bounded_match_without_waiting_for_outbox_worker():
    client,sessions,engine,ids=setup_matching_app('')
    try:
        response=client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
        assert response.status_code==200,response.text
        assert response.json()['matching_status']=='processed'
        assert response.json()['matched_suppliers']==1
        assert response.json()['invited_suppliers']==1
        with sessions() as db:
            match=db.scalar(select(RFQMatch).where(RFQMatch.rfq_id==ids['rfq'],RFQMatch.supplier_org_id==ids['supplier']))
            invitation=db.scalar(select(Invitation).where(Invitation.rfq_id==ids['rfq'],Invitation.supplier_org_id==ids['supplier']))
            event=db.scalar(select(Outbox).where(Outbox.dedupe_key==f"rfq-published:{ids['rfq']}"))
            notice=db.scalar(select(Notification).where(Notification.event=='rfq.invitation'))
            assert match and match.status=='eligible'
            assert invitation and invitation.status=='invited'
            assert event and event.status=='processed'
            assert notice
        invitations=client.get('/api/v1/matching/invitations')
        assert invitations.status_code==200 and len(invitations.json())==1
    finally:engine.dispose()


def test_delivery_city_mismatch_is_reported_and_not_counted_as_match():
    client,sessions,engine,ids=setup_matching_app('RAS')
    try:
        response=client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
        assert response.status_code==200,response.text
        assert response.json()['matched_suppliers']==0
        assert response.json()['invited_suppliers']==0
        result=client.get(f"/api/v1/matching/rfqs/{ids['rfq']}/results")
        assert result.status_code==200,result.text
        data=result.json()
        assert data['progress']=='no_eligible_suppliers'
        assert data['suppliers_evaluated']==1
        assert data['matches'][0]['status']=='location_mismatch'
        assert data['matches'][0]['supplier_name']=='Supplier Tech'
        assert 'do not cover delivery city RAS' in data['explanation']
        with sessions() as db:
            assert db.scalar(select(Invitation.id).where(Invitation.rfq_id==ids['rfq'])) is None
    finally:engine.dispose()
