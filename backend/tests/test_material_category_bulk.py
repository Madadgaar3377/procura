"""Durable material import acceptance, isolated DB/files; no real notifications."""
from io import BytesIO
from types import SimpleNamespace
from datetime import datetime, UTC, timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import Category, Material, MeasurementUnit, ImportBatch, ImportedRow, User, Capability, Organization, RFQ, RFQItem
from app.security import current_user
from app.import_storage import LocalImportStorage
from app.modules.imports import materials as imports
from app.modules.taxonomy.router import router as taxonomy
from app.modules.materials.service import create_material_record
from app import catalog_cache

@pytest.fixture
def system(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///:memory:',poolclass=StaticPool,connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine);sessions=sessionmaker(bind=engine,expire_on_commit=False)
    storage=LocalImportStorage(str(tmp_path));monkeypatch.setattr(imports,'get_import_storage',lambda:storage)
    monkeypatch.setattr(catalog_cache,'get',lambda *a,**k:None)
    monkeypatch.setattr(catalog_cache,'set',lambda *a,**k:None)
    monkeypatch.setattr(catalog_cache,'version',lambda:0)
    invalidations=[];monkeypatch.setattr(catalog_cache,'invalidate',lambda:invalidations.append(True))
    with sessions() as db:
        for number,(key,admin) in enumerate([('admin',True),('other-admin',True),('buyer',False)]):
            db.add(User(id=key,username=key,phone=f'+96650000100{number}',password_hash='x',is_admin=admin,is_active=True))
        db.commit()
    app=FastAPI();app.include_router(imports.router,prefix='/imports');app.include_router(taxonomy)
    def database():
        with sessions() as db:yield db
    actor=['admin']
    def user():
        with sessions() as db:return db.get(User,actor[0])
    app.dependency_overrides[get_db]=database;app.dependency_overrides[current_user]=user
    with TestClient(app) as client:
        yield SimpleNamespace(db=sessions,client=client,actor=actor,storage=storage,engine=engine,invalidations=invalidations,app=app)
    engine.dispose()


def workbook(count=1,rows=None):
    book=Workbook();page=book.active;page.title='Civil Coding';page.append(imports.TEMPLATE_HEADERS)
    for row in rows if rows is not None else [[str(i+1),'Reinforcement & Prestressing','Reinforcing Steel',f'Rebar Deformed ASTM A615 Gr60 diameter {10+i}mm 12m','KG','003-20'] for i in range(count)]:page.append(row)
    out=BytesIO();book.save(out);return out.getvalue()


def stage(s,content):
    response=s.client.post('/imports/materials/preview',files={'file':('civil.xlsx',content,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},data={'sheet_name':'__all__','discipline':'Civil'})
    assert response.status_code==202,response.text
    return response.json()['batch_id']


def parse(s,key):
    with s.db() as db:imports.process_queued_material_imports(db)
    result=s.client.get('/imports/materials/'+key).json()
    assert result['status']=='preview',result
    return result


@pytest.mark.parametrize('count',[30,300,310,1310])
def test_complete_upload_once_jobs_and_30_row_atomic_commits(system,count):
    s=system;key=stage(s,workbook(count));preview=parse(s,key)
    assert preview['rows']==count and preview['create']==count and len(preview['new_categories'])==2
    assert preview['jobs_total']==(count+299)//300
    assert s.client.post(f'/imports/materials/{key}/confirm').status_code==200
    cursors=[]
    @event.listens_for(s.engine,'commit')
    def commits(conn):
        row=conn.execute(select(ImportBatch.completed_rows).where(ImportBatch.id==key)).first()
        if row:cursors.append(row[0])
    job_totals=[]
    while True:
        # New session simulates a worker restart between every job.
        with s.db() as db:imports.process_queued_material_imports(db)
        state=s.client.get('/imports/materials/'+key).json();job_totals.append(state['completed_rows'])
        if state['status']=='completed':break
        assert state['status']=='queued',state
        assert len(job_totals)<=5
    assert job_totals==list(range(300,count,300))+[count]
    unique=list(dict.fromkeys(cursors));assert all(0<=b-a<=30 for a,b in zip([0]+unique,unique))
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(Material))==count
        assert db.scalar(select(func.count()).select_from(MeasurementUnit))==1
        assert db.scalar(select(func.count()).select_from(Category))==2
        material=db.scalar(select(Material));assert material.expenditure_code=='003-20'
    assert state['progress_percent']==100 and state['created']==count
    assert len(s.invalidations)>=((count+29)//30)
    assert s.client.get('/imports/materials/recent').json()[0]['batch_id']==key
    # A repeated confirmation is rejected and cannot replay committed rows.
    assert s.client.post(f'/imports/materials/{key}/confirm').status_code==409


def test_template_invalid_xlsx_duplicates_and_failed_export(system):
    s=system;response=s.client.get('/imports/materials/template');assert response.status_code==200
    book=load_workbook(BytesIO(response.content));assert list(next(book['Civil Coding'].values))==imports.TEMPLATE_HEADERS
    assert 'Example (reference)' in book.sheetnames and 'Instructions' in book.sheetnames
    # Example rows cannot accidentally enter a real import.
    data=book['Civil Coding'];data.append(['1','Electrical','Accessories','Insulated clamp 10mm','PCS','0001']);out=BytesIO();book.save(out)
    key=stage(s,out.getvalue());assert parse(s,key)['rows']==1
    assert s.client.post('/imports/materials/preview',files={'file':('bad.xlsx',b'not Excel')}).status_code==422
    row=['1','Electrical','Accessories','Insulated clamp 10mm','PCS','0001']
    key2=stage(s,workbook(rows=[row,row,['3','','','+SUM(1,2)','','']]))
    preview=parse(s,key2);assert preview['rows']==3 and preview['duplicate_rows']==1 and preview['invalid']==1
    assert s.client.post(f'/imports/materials/{key2}/confirm').status_code==200
    with s.db() as db:imports.process_queued_material_imports(db)
    state=s.client.get('/imports/materials/'+key2).json();assert state['status']=='completed_with_errors' and state['completed_rows']==3
    book=load_workbook(BytesIO(s.client.get(f'/imports/materials/{key2}/errors.xlsx').content))
    values=list(book.active.values);assert len(values)==3
    assert values[-1][3].startswith("'+")


def test_existing_metadata_enrichment_preserves_material_id_and_conflicts(system):
    s=system
    with s.db() as db:
        main=Category(id='main',code='MAIN',name_en='Electrical',normalized_name='electrical');sub=Category(id='sub',code='SUB',name_en='Accessories',normalized_name='accessories',parent_id='main')
        db.add_all([main,sub]);db.flush()
        material,_=create_material_record(db,actor=None,main_category=main,subcategory=sub,description='Insulated clamp 10mm',unit='PCS',source_unit='PCS')
        db.flush();original=material.id;db.commit()
    key=stage(s,workbook(rows=[['SN1',' Electrical ','ACCESSORIES','Insulated clamp 10mm','PCS','0001']]))
    preview=parse(s,key);assert preview['update']==1 and preview['existing_categories_reused']==2
    s.client.post(f'/imports/materials/{key}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    with s.db() as db:
        material=db.get(Material,original);assert material.expenditure_code=='0001' and material.source_sn=='SN1'
        assert db.scalar(select(func.count()).select_from(Material))==1
    key=stage(s,workbook(rows=[['SN1','Electrical','Accessories','Changed to a different material 12mm','PCS','0001']]))
    preview=parse(s,key);assert preview['conflicts']==1 and preview['valid']==0
    with s.db() as db:assert db.get(Material,original).name_en=='Insulated clamp 10mm'


def test_failed_batch_retry_keeps_committed_batches_and_ids(system,monkeypatch):
    s=system;key=stage(s,workbook(310));parse(s,key);s.client.post(f'/imports/materials/{key}/confirm')
    apply=imports._apply_row;calls=[0]
    def fail(db,*args,**kwargs):
        calls[0]+=1
        if calls[0]==35:raise RuntimeError('simulated worker crash')
        return apply(db,*args,**kwargs)
    monkeypatch.setattr(imports,'_apply_row',fail)
    with s.db() as db:imports.process_queued_material_imports(db)
    state=s.client.get('/imports/materials/'+key).json();assert state['status']=='failed' and state['completed_rows']==30
    with s.db() as db:original_ids=set(db.scalars(select(Material.id)))
    assert len(original_ids)==30
    # Reparse is forbidden after confirmation, even when application failed.
    assert s.client.post(f'/imports/materials/{key}/preview-options').status_code==409
    monkeypatch.setattr(imports,'_apply_row',apply)
    assert s.client.post(f'/imports/materials/{key}/retry').status_code==202
    with s.db() as db:imports.process_queued_material_imports(db)
    assert s.client.get('/imports/materials/'+key).json()['completed_rows']==300
    with s.db() as db:imports.process_queued_material_imports(db)
    with s.db() as db:
        assert original_ids<=set(db.scalars(select(Material.id)))
        assert db.scalar(select(func.count()).select_from(Material))==310


def test_upload_recovery_two_confirmed_files_idempotency_and_permissions(system):
    s=system;content=workbook(30);key=stage(s,content);assert stage(s,content)==key
    parse(s,key)
    s.actor[0]='other-admin'
    assert s.client.get('/imports/materials/recent').json()==[]
    assert s.client.post(f'/imports/materials/{key}/confirm').status_code==403
    assert s.client.post(f'/imports/materials/{key}/retry').status_code==403
    assert s.client.post(f'/imports/materials/{key}/preview-options').status_code==403
    s.actor[0]='buyer'
    for path in ['/imports/materials/template','/imports/materials/recent',f'/imports/materials/{key}',f'/imports/materials/{key}/errors.xlsx','/taxonomy/categories/duplicates']:
        assert s.client.get(path).status_code==403,path
    assert s.client.post('/imports/materials/preview',files={'file':('civil.xlsx',content)}).status_code==403
    assert s.client.post('/taxonomy/categories/duplicates/merge',json={'category_ids':['a','b'],'preview_token':'x'}).status_code==403
    s.actor[0]='admin';s.client.post(f'/imports/materials/{key}/confirm')
    # Another filename/job with identical materials is safely reused by the worker.
    other=stage(s,workbook(rows=[[str(i+1),'Reinforcement & Prestressing','Reinforcing Steel',f'Rebar Deformed ASTM A615 Gr60 diameter {10+i}mm 12m','KG','003-20'] for i in range(30)]+[['31','Electrical','Accessories','Extra material','PCS','1']]))
    parse(s,other) if s.client.get('/imports/materials/'+other).json()['status']=='preview' else None
    for _ in range(4):
        with s.db() as db:imports.process_queued_material_imports(db)
    assert s.client.get('/imports/materials/'+other).json()['status']=='preview'
    s.client.post(f'/imports/materials/{other}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    with s.db() as db:assert db.scalar(select(func.count()).select_from(Material))==31


def test_duplicate_category_preview_merge_preserves_references_and_blocks_ambiguity(system):
    s=system
    with s.db() as db:
        db.add_all([Category(id='main',code='M',name_en='Electrical',normalized_name='electrical'),
                    Category(id='a',code='A',name_en='Accessories',parent_id='main'),Category(id='b',code='B',name_en=' accessories ',parent_id='main')]);db.flush()
        main=db.get(Category,'main');sub=db.get(Category,'b')
        material,_=create_material_record(db,actor=None,main_category=main,subcategory=sub,description='Insulated clamp',unit='PCS',source_unit='PCS');db.flush();mid=material.id
        org=Organization(id='org',name='Supplier',name_normalized='supplier',city='Riyadh',is_supplier=True,is_buyer=True);db.add(org);db.flush()
        cap=Capability(org_id=org.id,category_id='b',city='Riyadh',material_ids=[mid],confirmed=True);db.add(cap)
        rfq=RFQ(reference='RFQ-MERGE',buyer_org_id=org.id,title='Test',city='Riyadh',response_deadline=datetime.now(UTC)+timedelta(days=2));db.add(rfq);db.flush()
        item=RFQItem(rfq_id=rfq.id,material_id=mid,main_category_id='main',subcategory_id='b',description='Customized',specifications='',unit='PCS',quantity=1);db.add(item);db.flush();itemid=item.id;capid=cap.id;db.commit()
    groups=s.client.get('/taxonomy/categories/duplicates').json();assert len(groups)==1 and groups[0]['safe_to_merge']
    g=groups[0];canonical=g['canonical_id'];response=s.client.post('/taxonomy/categories/duplicates/merge',json={'category_ids':['a','b'],'preview_token':g['preview_token']})
    assert response.status_code==200,response.text
    with s.db() as db:
        assert db.get(Material,mid).subcategory_id==canonical
        assert db.get(Capability,capid).category_id==canonical and db.get(Capability,capid).material_ids==[mid]
        assert db.get(RFQItem,itemid).material_id==mid and db.get(RFQItem,itemid).subcategory_id==canonical
        assert db.scalar(select(func.count()).select_from(Category))==3
        assert len(list(db.scalars(select(Category).where(Category.active.is_(True)))))==2
    assert s.client.post('/taxonomy/categories/duplicates/merge',json={'category_ids':['a','b'],'preview_token':g['preview_token']}).status_code==409


def test_anonymous_import_endpoints_require_authentication(system):
    system.app.dependency_overrides.pop(current_user)
    for path in ['/imports/materials/template','/imports/materials/recent','/taxonomy/categories/duplicates']:
        assert system.client.get(path).status_code==401


def test_row_failure_continues_and_missing_unit_is_registered(system,monkeypatch):
    s=system;key=stage(s,workbook(rows=[['1','New Main','New Sub','First distinct material','BUNDLE','01'],['2','New Main','New Sub','Second distinct material','BUNDLE','02']]))
    parse(s,key);s.client.post(f'/imports/materials/{key}/confirm');apply=imports._apply_row
    def row_error(db,item,*args,**kwargs):
        from fastapi import HTTPException
        if item.row_number==2:raise HTTPException(422,'Bad row for test')
        return apply(db,item,*args,**kwargs)
    monkeypatch.setattr(imports,'_apply_row',row_error)
    with s.db() as db:imports.process_queued_material_imports(db)
    state=s.client.get('/imports/materials/'+key).json()
    assert state['status']=='completed_with_errors' and state['completed_rows']==2 and state['created']==1 and state['errors']==1
    with s.db() as db:assert db.scalar(select(MeasurementUnit).where(MeasurementUnit.code=='BUNDLE'))


def test_ambiguous_category_or_existing_material_identity_requires_review(system):
    s=system
    with s.db() as db:
        db.add_all([Category(id='m',code='M',name_en='Electrical'),Category(id='a',code='A',name_en='Accessories',parent_id='m'),Category(id='b',code='B',name_en=' accessories ',parent_id='m')]);db.commit()
    key=stage(s,workbook(rows=[['1','Electrical','Accessories','Example distinct material','PCS','1']]))
    preview=parse(s,key);assert preview['conflicts']==1
    with s.db() as db:
        db.add(Category(id='child',code='CH',name_en='A child',parent_id='a'));db.commit()
    group=s.client.get('/taxonomy/categories/duplicates').json()[0]
    assert not group['safe_to_merge']
    assert s.client.post('/taxonomy/categories/duplicates/merge',json={'category_ids':['a','b'],'preview_token':group['preview_token']}).status_code==409


def test_transient_failure_is_requeued_and_abandoned_parse_is_resumed(system,monkeypatch):
    s=system;key=stage(s,workbook(30))
    with s.db() as db:
        batch=db.get(ImportBatch,key);batch.status='processing';batch.stage='parse';batch.updated_at=datetime.now(UTC)-timedelta(minutes=11);db.commit()
    parse(s,key);s.client.post(f'/imports/materials/{key}/confirm');apply=imports._apply_row
    def outage(*args,**kwargs):raise OSError('Temporary infrastructure outage')
    monkeypatch.setattr(imports,'_apply_row',outage)
    with s.db() as db:imports.process_queued_material_imports(db)
    state=s.client.get('/imports/materials/'+key).json();assert state['status']=='queued' and state['completed_rows']==0
    monkeypatch.setattr(imports,'_apply_row',apply)
    with s.db() as db:imports.process_queued_material_imports(db)
    assert s.client.get('/imports/materials/'+key).json()['created']==30
