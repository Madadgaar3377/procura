from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Capability, Category, Contact, ImportBatch, ImportedRow, ImportedSupplierTrade, MeasurementUnit, Organization, User, Verification
from app.modules.materials.service import create_material_record
from app.modules.imports.materials import _discover
from app.modules.imports.router import extract, field, map_supplier_trade, process_queued_supplier_imports, router, reprocess_imported_suppliers, supplier_reprocess_preview
from app.schemas import SupplierTradeMappingIn


def test_supplier_import_reads_all_sheets_and_carries_down_names():
    workbook=Workbook()
    first=workbook.active;first.title='Lighting'
    first.append(['Supplier Name','Trade / Products','Contact Person','Mobile / Phone','Email'])
    first.append(['Bright Co','Lighting','Sales One','+966 50 111 2222','one@example.com'])
    first.append([None,'Emergency lighting','Sales Two','+966 50 333 4444','two@example.com'])
    second=workbook.create_sheet('Cables')
    second.append(['Company Name','Products','Contact Name','Phone Number'])
    second.append(['Cable Works','Cables','Export Desk','+44 20 1234 5678'])
    data=BytesIO();workbook.save(data)

    rows,sheets=extract(data.getvalue(),'client.xlsx','suppliers',sheet_name='__all__')

    assert [sheet['name'] for sheet in sheets if sheet['eligible']]==['Lighting','Cables']
    assert len(rows)==3
    assert rows[1]['supplier name']=='Bright Co'
    assert rows[1]['_source_sheet']=='Lighting'
    assert rows[2]['company name']=='Cable Works'
    assert field(rows[0],'phone')=='+966 50 111 2222'


def test_material_import_all_sheets_normalizes_each_sheet_to_material_fields():
    workbook=Workbook()
    first=workbook.active;first.title='Electrical'
    first.append(['Main Category','Sub Category','Description','Unit'])
    first.append(['Electrical','Tools','Hole Saw Ss 89Mm Germany','PCS'])
    second=workbook.create_sheet('Civil')
    second.append(['Category','Subcategory','Material Name','UOM'])
    second.append(['Reinforcement & Prestressing','Reinforcing Steel','Rebar Deformed ASTM A615 Gr60 Ø10mm 12m','kg'])
    data=BytesIO();workbook.save(data)

    sheets=_discover(data.getvalue(),'materials.xlsx','__all__')
    aggregate=next(sheet for sheet in sheets if sheet['name']=='All eligible worksheets')

    assert aggregate['row_count']==2
    assert aggregate['_map']=={'main_category':'main_category','subcategory':'subcategory','material_name':'material_name','unit':'unit'}
    assert aggregate['_rows'][0]['material_name']=='Hole Saw Ss 89Mm Germany'
    assert aggregate['_rows'][1]['main_category']=='Reinforcement & Prestressing'
    assert aggregate['_rows'][1]['_sheet']=='Civil'


def test_material_import_can_register_an_unlisted_source_unit():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        main=Category(code='ELEC',name_en='Electrical',normalized_name='electrical',active=True)
        db.add(main);db.flush()
        material,created=create_material_record(db,actor=None,main_category=main,subcategory=None,
            description='Imported cable assembly',unit='BUNDLE',source_unit='bundle',create_unknown_unit=True)
        db.flush()
        unit=db.scalar(select(MeasurementUnit).where(MeasurementUnit.code=='BUNDLE'))
        assert created and material.unit=='BUNDLE' and material.source_unit=='bundle'
        assert unit and unit.active
    engine.dispose()


def test_supplier_import_worker_commits_bounded_idempotent_batches_and_auto_verifies_admin_imports():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin=User(username='import-admin',phone='+966500000001',phone_e164='+966500000001',password_hash='x',is_admin=True)
        db.add(admin);db.flush()
        batch=ImportBatch(kind='suppliers',filename='suppliers.xlsx',status='queued',stage='apply',rows=2,
                          initiated_by=admin.id)
        db.add(batch);db.flush()
        db.add_all([
            ImportedRow(batch_id=batch.id,row_number=2,status='valid',raw={'supplier name':'Bright Co','trade':'Panels / MV','type':'Electrical / Panels','contact person':'Sales One','mobile':'0501112222','email':'one@example.com'}),
            ImportedRow(batch_id=batch.id,row_number=3,status='valid',raw={'supplier name':'Bright Co','trade':'Panels / MV','type':'Electrical / Panels','contact person':'Sales Two','mobile':'0503334444','email':'two@example.com'}),
        ]);db.commit()

        assert not process_queued_supplier_imports(db,batch_limit=1,chunk_size=1)
        assert batch.status=='queued' and batch.completed_rows==1
        assert process_queued_supplier_imports(db,batch_limit=1,chunk_size=1)==[batch.id]
        company=db.scalar(select(Organization).where(Organization.source=='imported'))
        assert company and company.verified_status=='verified' and not company.is_buyer
        verification=db.scalar(select(Verification).where(Verification.org_id==company.id,Verification.status=='approved'))
        assert verification and verification.reviewer_id==admin.id and verification.reviewed_at
        assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id==company.id))==2
        assert db.scalar(select(func.count()).select_from(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id==company.id))==1
        assert db.scalar(select(func.count()).select_from(ImportedRow).where(ImportedRow.batch_id==batch.id,ImportedRow.status=='created'))==2
        trade=db.scalar(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id==company.id))
        capability=db.scalar(select(Capability).where(Capability.org_id==company.id))
        assert trade and trade.mapping_status=='mapped'
        assert capability and capability.confirmed and capability.trade_keywords=='Panels / MV'
        assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id==company.id,Contact.permission_to_notify.is_(True)))==0
    engine.dispose()


def test_imported_trade_admin_mapping_is_confirmed_for_publication():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin=User(username='trade-admin',phone='+966500000002',phone_e164='+966500000002',password_hash='x',is_admin=True)
        company=Organization(name='Bright Co',name_normalized='bright co',source='imported',is_supplier=True,is_buyer=False)
        category=Category(code='ELEC',name_en='Electrical',normalized_name='electrical',active=True)
        db.add_all([admin,company,category]);db.flush()
        batch=ImportBatch(kind='suppliers',filename='suppliers.csv',initiated_by=admin.id);db.add(batch);db.flush()
        trade=ImportedSupplierTrade(org_id=company.id,import_batch_id=batch.id,raw_trade='Lighting fixtures',raw_location='Riyadh')
        db.add(trade);db.commit()

        result=map_supplier_trade(trade.id,SupplierTradeMappingIn(category_id=category.id,city='Riyadh'),admin,db)

        capability=db.scalar(select(Capability).where(Capability.org_id==company.id))
        assert result['requires_capability_approval'] is False
        assert capability and capability.category_id==category.id and capability.trade_keywords=='Lighting fixtures'
        assert capability.confirmed is True and capability.accepts_rfqs is True
        assert trade.mapping_status=='mapped'
    engine.dispose()


def test_supplier_import_read_and_download_routes_require_admin():
    from app.security import admin_user

    protected={'/api/v1/imports/suppliers/{batch_id}','/api/v1/imports/suppliers/{batch_id}/errors.csv',
               '/api/v1/imports/suppliers/{batch_id}/confirm','/api/v1/imports/supplier-trades',
               '/api/v1/imports/supplier-trades/{trade_id}/map','/api/v1/imports/suppliers/reprocess/preview',
               '/api/v1/imports/suppliers/reprocess'}
    for route in router.routes:
        if not hasattr(route,'path'):
            continue
        full_path='/api/v1'+route.path
        if full_path in protected:
            assert any(dependency.call is admin_user for dependency in route.dependant.dependencies)
    assert protected <= {'/api/v1'+route.path for route in router.routes if hasattr(route,'path')}


def test_imported_supplier_backfill_preserves_importer_and_does_not_verify_registered_supplier():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin=User(username='backfill-admin',phone='+966500000003',phone_e164='+966500000003',password_hash='x',is_admin=True)
        imported=Organization(name='Legacy Imported',name_normalized='legacy imported',source='imported',is_supplier=True,
            is_buyer=False,verified_status='unverified',profile_status='incomplete')
        registered=Organization(name='Self Registered',name_normalized='self registered',source='registered',is_supplier=True,
            is_buyer=False,verified_status='unverified',profile_status='incomplete')
        db.add_all([admin,imported,registered]);db.flush()
        batch=ImportBatch(kind='suppliers',filename='legacy.xlsx',initiated_by=admin.id);db.add(batch);db.flush()
        trade=ImportedSupplierTrade(org_id=imported.id,import_batch_id=batch.id,raw_type='Electrical / Panels',raw_trade='Panels / MV')
        db.add(trade);db.commit()
        preview=supplier_reprocess_preview(user=admin,db=db)
        assert preview['suppliers_to_reprocess']==1
        result=reprocess_imported_suppliers(None,admin,db)
        db.refresh(imported);db.refresh(registered);db.refresh(trade)
        verification=db.scalar(select(Verification).where(Verification.org_id==imported.id,Verification.status=='approved'))
        capability=db.scalar(select(Capability).where(Capability.org_id==imported.id))
        assert result['processed']==1 and result['verified']==1
        assert imported.verified_status=='verified' and verification.reviewer_id==admin.id
        assert capability and capability.confirmed and capability.trade_keywords=='Panels / MV'
        assert trade.mapping_status=='mapped'
        assert registered.verified_status=='unverified'
        assert not db.scalar(select(Verification.id).where(Verification.org_id==registered.id))
    engine.dispose()
