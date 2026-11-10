from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.models import Contact, ContactChannel, Organization, User, AuditEvent
from app.modules.organizations.contact_import import import_contact_row, parse_phones
from app.modules.organizations.router import router
from app.security import current_user


def make_db():
    engine=create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine,sessionmaker(bind=engine,expire_on_commit=False)


def test_repeated_import_adds_numbers_to_one_person_and_is_idempotent():
    engine,sessions=make_db()
    try:
        with sessions() as db:
            org=Organization(name='ABB Test',name_normalized='abb test',is_supplier=True,is_buyer=False)
            db.add(org);db.flush()
            created,_=import_contact_row(db,org.id,'Eng Abdullah','abdullah@sa.abb.com','M: 0530301302','Saudi Arabia','batch:2')
            assert created==1
            created,_=import_contact_row(db,org.id,'Eng Abdullah','abdullah@sa.abb.com','T: 0114845647','Saudi Arabia','batch:3')
            assert created==0
            created,_=import_contact_row(db,org.id,'Eng Abdullah','abdullah@sa.abb.com','M: 0530301302; T: 0114845647','Saudi Arabia','batch:2')
            assert created==0
            db.commit()
            assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id==org.id))==1
            assert db.scalar(select(func.count()).select_from(ContactChannel).where(ContactChannel.contact_id==db.scalar(select(Contact.id).where(Contact.org_id==org.id))))==3
    finally: engine.dispose()


def test_multiple_people_are_aligned_only_when_name_and_email_counts_match():
    engine,sessions=make_db()
    try:
        with sessions() as db:
            org=Organization(name='ABB Test',name_normalized='abb test',is_supplier=True,is_buyer=False);db.add(org);db.flush()
            created,review=import_contact_row(db,org.id,'Abdulhakim Abdo / Abdulkarim Al-Muhaimid',
                'Abdulhakim.Abdo@sa.abb.com / Abdulkarim.Al-Muhaimid@sa.abb.com','', 'SA','batch:34')
            db.commit()
            contacts=db.scalars(select(Contact).where(Contact.org_id==org.id)).all()
            assert created==2 and not review and len(contacts)==2
            assert {c.name for c in contacts}=={'Abdulhakim Abdo','Abdulkarim Al-Muhaimid'}
    finally: engine.dispose()


def test_ambiguous_rows_are_preserved_for_review_and_office_phones_do_not_merge_people():
    engine,sessions=make_db()
    try:
        with sessions() as db:
            org=Organization(name='ABB Test',name_normalized='abb test',is_supplier=True,is_buyer=False);db.add(org);db.flush()
            _,review=import_contact_row(db,org.id,'Person A / Person B','a@abb.com / b@abb.com / c@abb.com','T: 0114845647','SA','batch:1')
            assert review
            _,_=import_contact_row(db,org.id,'Firas Badad','firas@abb.com','T: 0114845647','SA','batch:2')
            _,_=import_contact_row(db,org.id,'Zaher Najim','zaher@abb.com','T: 0114845647','SA','batch:3')
            db.commit()
            contacts=db.scalars(select(Contact).where(Contact.org_id==org.id)).all()
            assert sum(c.needs_review for c in contacts)>=1
            assert any(c.name=='Firas Badad' for c in contacts) and any(c.name=='Zaher Najim' for c in contacts)
            assert len([c for c in contacts if c.name in {'Firas Badad','Zaher Najim'}])==2
    finally: engine.dispose()


def test_labeled_phone_parser_keeps_office_extension_mobile_and_fax_distinct():
    phones,review=parse_phones('T: 013-3407036/3638 M: 0508135934 F: 013-3407038','SA')
    assert not review
    assert [(p['kind'],p['label'],p['extension']) for p in phones]==[('phone','office','3638'),('phone','mobile',None),('fax','fax',None)]
    assert phones[0]['normalized']=='+966133407036'


def test_admin_duplicate_cleanup_is_previewed_audited_and_confirmation_gated():
    engine,sessions=make_db()
    try:
        with sessions() as db:
            admin=User(username='contact-cleanup-admin',phone='+966500000011',phone_e164='+966500000011',password_hash='x',is_admin=True)
            org=Organization(name='ABB',name_normalized='abb',is_supplier=True,is_buyer=False)
            db.add_all([admin,org]);db.flush()
            left=Contact(org_id=org.id,name='Mohd. M. Rashed',email='mohd@abb.com',raw_phone='M: 0501112222',permission_to_notify=False)
            right=Contact(org_id=org.id,name='Mohd. M. Rashed',email='mohd@abb.com',raw_phone='T: 0114845647',permission_to_notify=False)
            db.add_all([left,right]);db.commit(); admin_id=admin.id;org_id=org.id
        app=FastAPI();app.include_router(router)
        def get_session():
            with sessions() as db: yield db
        app.dependency_overrides[get_db]=get_session
        with sessions() as db: actor=db.get(User,admin_id)
        app.dependency_overrides[current_user]=lambda:actor
        client=TestClient(app)
        try:
            path=f'/organizations/admin/suppliers/{org_id}/contacts'
            preview=client.get(path+'/deduplication-preview')
            assert preview.status_code==200 and preview.json()['expected_contact_count_after_cleanup']==1
            assert client.post(path+'/deduplication',json={'confirm':False}).status_code==422
            merged=client.post(path+'/deduplication',json={'confirm':True})
            assert merged.status_code==200 and merged.json()['contacts_merged']==1
            with sessions() as db:
                active=db.scalars(select(Contact).where(Contact.org_id==org_id,Contact.merged_into_contact_id.is_(None))).all()
                assert len(active)==1
                channels=db.scalars(select(ContactChannel).where(ContactChannel.contact_id==active[0].id)).all()
                assert {c.normalized_value for c in channels if c.kind=='email'}=={'mohd@abb.com'}
                assert db.scalar(select(AuditEvent.id).where(AuditEvent.action=='supplier.contacts_deduplicated',AuditEvent.org_id==org_id))
        finally: client.close()
    finally: engine.dispose()
