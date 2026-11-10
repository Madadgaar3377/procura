from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import AuditEvent, Capability, Category, Contact, Material, Organization, User, Verification
from app.modules.organizations.router import router
from app.security import admin_user, current_user


def setup_client():
    engine=create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    with sessions() as db:
        admin=User(username='admin_user',phone='+966501234567',phone_e164='+966501234567',password_hash='hash',is_admin=True)
        not_admin=User(username='normal_user',phone='+966511234567',phone_e164='+966511234567',password_hash='hash')
        org=Organization(name='Incomplete Supplier',name_normalized='incomplete supplier',is_supplier=True,is_buyer=False,source='imported',profile_status='incomplete',verified_status='unverified',city=None,cr_number=None)
        db.add_all([admin,not_admin,org]);db.flush()
        contact=Contact(org_id=org.id,name='Contact One',email='valid@example.com',phone_normalized=None,permission_to_notify=False,role='Sales')
        main=Category(code='ELEC',name_en='Electrical',normalized_name='electrical',active=True)
        db.add_all([contact,main]);db.flush()
        material=Material(code='LAMP-1',category_id=main.id,main_category_id=main.id,name_en='Light fixture',unit='PCS',active=True)
        db.add(material);db.flush()
        db.add(Capability(org_id=org.id,category_id=main.id,city='Riyadh',material_ids=[material.id],trade_keywords='lighting',confirmed=False,eligibility_status='enabled'))
        db.commit(); ids={'admin':admin.id,'normal':not_admin.id,'org':org.id,'contact':contact.id}
    app=FastAPI();app.include_router(router)
    def get_session():
        with sessions() as db: yield db
    app.dependency_overrides[get_db]=get_session
    client=TestClient(app)
    return client,sessions,engine,ids,app


def set_actor(app,sessions,user_id):
    with sessions() as db:
        actor=db.get(User,user_id)
        app.dependency_overrides[current_user]=lambda:actor


def test_supplier_details_contacts_capabilities_and_explicit_verification_persist():
    client,sessions,engine,ids,app=setup_client()
    try:
        set_actor(app,sessions,ids['admin'])
        detail=client.get(f"/organizations/admin/suppliers/{ids['org']}")
        assert detail.status_code==200
        data=detail.json()
        assert data['company']['cr_number'] is None
        assert data['status']['profile_status']=='incomplete'
        assert len(data['contacts'])==1 and len(data['capabilities'])==1
        assert data['capabilities'][0]['materials'][0]['name']=='Light fixture'
        saved=client.post(f"/organizations/admin/suppliers/{ids['org']}/verify",json={})
        assert saved.status_code==200
        assert saved.json()['status']=='approved'
        refreshed=client.get(f"/organizations/admin/suppliers/{ids['org']}").json()
        assert refreshed['status']['verification_status']=='verified'
        assert refreshed['status']['profile_status']=='incomplete'
        assert refreshed['status']['verified_by']==ids['admin']
        assert refreshed['status']['verified_at']
        with sessions() as db:
            assert db.scalar(select(Verification).where(Verification.org_id==ids['org'])).reviewer_id==ids['admin']
            assert db.scalar(select(AuditEvent).where(AuditEvent.action=='supplier.verified',AuditEvent.org_id==ids['org']))
    finally:
        client.close();engine.dispose()


def test_admin_can_edit_profile_and_add_invalid_contact_for_review():
    client,sessions,engine,ids,app=setup_client()
    try:
        set_actor(app,sessions,ids['admin'])
        response=client.patch(f"/organizations/admin/suppliers/{ids['org']}",json={'city':'Riyadh','address':'Industrial Area','cr_number':'12345'})
        assert response.status_code==200
        contact=client.post(f"/organizations/admin/suppliers/{ids['org']}/contacts",json={'name':'New contact','role':'Procurement','email':'bad email','phone':'not a phone'})
        assert contact.status_code==201
        assert contact.json()['raw_email']=='bad email'
        assert contact.json()['raw_phone']=='not a phone'
        assert contact.json()['needs_review'] is True
        with sessions() as db:
            saved=db.get(Contact,contact.json()['id'])
            assert saved.email is None and saved.phone_normalized is None
            assert db.get(Organization,ids['org']).city=='Riyadh'
    finally:
        client.close();engine.dispose()


def test_rejection_requires_reason_and_keeps_supplier_records():
    client,sessions,engine,ids,app=setup_client()
    try:
        set_actor(app,sessions,ids['admin'])
        path=f"/organizations/admin/suppliers/{ids['org']}/reject"
        assert client.post(path,json={}).status_code==422
        response=client.post(path,json={'reason':'CR details could not be validated'})
        assert response.status_code==200
        with sessions() as db:
            org=db.get(Organization,ids['org'])
            assert org.verified_status=='rejected'
            assert db.scalar(select(Contact.id).where(Contact.org_id==org.id))
            assert db.scalar(select(Capability.id).where(Capability.org_id==org.id))
            assert db.scalar(select(AuditEvent).where(AuditEvent.action=='supplier.rejected',AuditEvent.org_id==org.id))
    finally:
        client.close();engine.dispose()


def test_supplier_admin_routes_reject_non_admin_users():
    client,sessions,engine,ids,app=setup_client()
    try:
        set_actor(app,sessions,ids['normal'])
        assert client.get(f"/organizations/admin/suppliers/{ids['org']}").status_code==403
        assert client.post(f"/organizations/admin/suppliers/{ids['org']}/verify",json={}).status_code==403
    finally:
        client.close();engine.dispose()
