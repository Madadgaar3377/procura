import asyncio
from io import BytesIO
import json

from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Capability, Category, Contact, ContactCapability, ImportBatch, Material, Organization, User
from app.modules.imports import bulk


def setup(monkeypatch):
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(bulk,'SessionLocal',sessions)
    with sessions() as db:
        admin=User(username='supplier-category-import-admin',phone='+966500000021',phone_e164='+966500000021',password_hash='x',is_admin=True)
        db.add(admin);db.flush()
        categories={}
        materials={}
        for index,(name,material_name) in enumerate([('Lighting','Lighting Fixture'),('Fixer','Fixer'),('Generator','Generator'),('Electrical','Cables'),('Mechanical','Pumps')]):
            main=Category(code=f'TEST{index}',name_en=f'Main {name}',normalized_name=f'main {name}'.casefold(),active=True)
            db.add(main);db.flush()
            category=Category(code=f'TEST{index}-SUB',name_en=name,normalized_name=name.casefold(),parent_id=main.id,active=True)
            db.add(category);db.flush();categories[name]=category
            material=Material(code=f'MAT{index}',category_id=category.id,main_category_id=main.id,subcategory_id=category.id,
                name_en=material_name,normalized_description=material_name.casefold(),unit='PCS',active=True)
            db.add(material);db.flush();materials[material_name]=material
        db.commit();admin_id=admin.id
    return engine,sessions,admin_id,categories,materials


def test_supplier_rows_group_companies_and_assign_categories_to_contacts_idempotently(monkeypatch):
    engine,sessions,admin_id,categories,materials=setup(monkeypatch)
    try:
        rows=[
            {'Company Name':'Sydney Fine','Country':'Saudi Arabia','City':'Jeddah','Contact Person':'Heal','Contact Email':'heal@example.com','Contact Phone':'+966111111111','Contact Designation':'Sales','Category':'Lighting','Material':'Lighting Fixture','Is Primary Contact':'Yes'},
            {'Company Name':'Sydney Fine','Country':'Saudi Arabia','City':'Jeddah','Contact Person':'Abby Adeeb Arshad','Contact Email':'abby@example.com','Contact Phone':'+966222222222','Category':'Fixer','Material':'Fixer'},
            {'Company Name':'Sydney Fine','Country':'Saudi Arabia','City':'Jeddah','Contact Person':'Another Contact','Contact Email':'contact@example.com','Contact Phone':'+966333333333','Category':'Generator','Material':'Generator'},
            {'Company Name':'Sydney Fine','Country':'Saudi Arabia','City':'Jeddah','Contact Person':'Heal','Contact Email':'heal@example.com','Contact Phone':'+966111111111','Category':'Generator','Material':'Generator'},
            {'Company Name':'ABC Trading','Country':'Saudi Arabia','City':'Riyadh','Contact Person':'Ali','Contact Email':'ali@example.com','Contact Phone':'+966444444444','Category':'Electrical','Material':'Cables','Is Primary Contact':'Yes'},
            {'Company Name':'ABC Trading','Country':'Saudi Arabia','City':'Riyadh','Contact Person':'Ahmed','Contact Email':'ahmed@example.com','Contact Phone':'+966555555555','Category':'Mechanical','Material':'Pumps'},
        ]
        workbook=Workbook();sheet=workbook.active;sheet.append(bulk.SUPPLIER_HEADERS)
        for row in rows:sheet.append([row.get(header,'') for header in bulk.SUPPLIER_HEADERS])
        raw=BytesIO();workbook.save(raw)
        parsed=bulk._read(raw.getvalue(),'multi-contact.xlsx',bulk.SUPPLIER_HEADERS)
        events=[json.loads(line) for line in bulk._stream_import('suppliers',parsed,'multi-contact.xlsx',admin_id)]
        result=events[-1]
        assert result['total_supplier_records_identified']==2
        assert result['new_suppliers_saved']==2 and result['contacts_created']==5
        assert result['capabilities_saved']==5 and result['category_assignments_created']==6
        with Session(engine) as db:
            companies=db.scalars(select(Organization).where(Organization.is_supplier.is_(True))).all()
            assert len(companies)==2
            sydney=next(company for company in companies if company.name=='Sydney Fine')
            contacts=db.scalars(select(Contact).where(Contact.org_id==sydney.id,Contact.deleted_at.is_(None))).all()
            assert len(contacts)==3
            heal=next(contact for contact in contacts if contact.email=='heal@example.com')
            assignments=db.scalars(select(ContactCapability).where(ContactCapability.contact_id==heal.id)).all()
            assert len(assignments)==2
            assert db.scalar(select(func.count()).select_from(Capability).where(Capability.org_id==sydney.id))==3
        retry=[json.loads(line) for line in bulk._stream_import('suppliers',parsed,'multi-contact-retry.xlsx',admin_id)][-1]
        assert retry['new_suppliers_saved']==0 and retry['contacts_created']==0 and retry['category_assignments_created']==0
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Organization).where(Organization.is_supplier.is_(True)))==2
            assert db.scalar(select(func.count()).select_from(ContactCapability))==6
    finally:engine.dispose()


def test_template_has_required_columns_examples_and_instructions_sheet():
    engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin=User(username='template-admin',phone='+966500000022',phone_e164='+966500000022',password_hash='x',is_admin=True);db.add(admin);db.flush()
        response=bulk.supplier_template(admin)
    async def consume():return b''.join([chunk async for chunk in response.body_iterator])
    content=asyncio.run(consume());book=load_workbook(BytesIO(content),data_only=True)
    assert book.sheetnames==['Supplier Import','Instructions']
    assert list(book['Supplier Import'].values)[0]==tuple(bulk.SUPPLIER_HEADERS)
    assert book['Supplier Import'].max_row==6
    assert book['Supplier Import']['A2'].value=='Sydney Fine' and book['Supplier Import']['A5'].value=='ABC Trading'
    assert any('multiple rows' in str(row[0]).casefold() for row in list(book['Instructions'].values)[1:])
    book.close();engine.dispose()


def test_template_rows_are_recognized_by_import_reader():
    book=Workbook();sheet=book.active;sheet.append(bulk.SUPPLIER_HEADERS)
    sheet.append(['Sydney Fine','Saudi Arabia','Jeddah','','','','','','Heal','Sales','heal@example.com','+966111111111','Lighting','Lighting Fixture','Yes'])
    data=BytesIO();book.save(data)
    rows=bulk._read(data.getvalue(),'filled-template.xlsx',bulk.SUPPLIER_HEADERS)
    row=rows[0][1]
    assert row['Company Name']=='Sydney Fine' and row['Contact Email']=='heal@example.com'
    assert row['Contact Person']=='Heal' and row['Category']=='Lighting' and row['Material']=='Lighting Fixture'


def test_supplier_export_preserves_company_contact_and_category_rows(monkeypatch):
    engine,sessions,admin_id,categories,materials=setup(monkeypatch)
    try:
        with sessions() as db:
            org=Organization(name='Export Co',name_normalized='export co',country='Saudi Arabia',city='Jeddah',is_supplier=True,is_buyer=False)
            db.add(org);db.flush()
            contact=Contact(org_id=org.id,name='Heal',email='heal@example.com',phone='+966111111111',is_primary=True)
            db.add(contact);db.flush()
            caps=[]
            for name in ('Lighting','Generator'):
                category=categories[name];material=materials['Lighting Fixture' if name=='Lighting' else 'Generator']
                cap=Capability(org_id=org.id,category_id=category.id,city='Jeddah',material_ids=[material.id],trade_keywords='',confirmed=True,accepts_rfqs=True,eligibility_status='enabled')
                db.add(cap);db.flush();db.add(ContactCapability(contact_id=contact.id,capability_id=cap.id));caps.append(cap)
            db.commit()
            response=bulk.export_suppliers(db.get(User,admin_id),db)
        async def consume():return b''.join([chunk async for chunk in response.body_iterator])
        book=load_workbook(BytesIO(asyncio.run(consume())),data_only=True)
        rows=list(book['Suppliers Export'].values)
        assert len(rows)==3
        data=rows[1:]
        assert {row[0] for row in data}=={'Export Co'}
        assert {row[8] for row in data}=={'Heal'} and {row[10] for row in data}=={'heal@example.com'}
        assert {row[12] for row in data}=={'Main Lighting / Lighting','Main Generator / Generator'}
        book.close()
    finally:engine.dispose()
