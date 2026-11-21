"""Durable material import acceptance, isolated DB/files; no real notifications."""
from io import BytesIO
import os
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
    postgres_url=os.environ.get('MATERIAL_IMPORT_TEST_DATABASE_URL')
    if postgres_url:
        from sqlalchemy.engine import make_url
        target=make_url(postgres_url)
        assert target.get_backend_name()=='postgresql' and target.host=='127.0.0.1'
        assert target.database.startswith('procura_material_test_'), 'Use only a disposable, migrated test database'
        engine=create_engine(postgres_url,pool_pre_ping=True)
        # PostgreSQL is initialized with Alembic, never create_all. Run this
        # single integration scenario against an empty, disposable database.
        with engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(Category))==0
            assert conn.scalar(select(func.count()).select_from(Material))==0
    else:
        engine=create_engine('sqlite:///:memory:',poolclass=StaticPool,connect_args={'check_same_thread':False})
        Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
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


def test_group_entry_ten_materials_retry_units_and_permissions(system):
    s=system
    with s.db() as db:
        db.add_all([Category(id='main',code='M',name_en='Plumbing',normalized_name='plumbing'),Category(id='sub',code='S',name_en='Pipes',normalized_name='pipes',parent_id='main'),Category(id='fittings',code='F',name_en='Fittings',normalized_name='fittings',parent_id='main')]);db.commit()
    body={'main_category_id':'main','subcategory_id':'sub','discipline':'Mechanical','rows':[{'description':f'BCW Pipe diameter {20+i} mm','unit':'M'} for i in range(10)]}
    result=s.client.post('/taxonomy/materials/group',json=body);assert result.status_code==200,result.text
    assert result.json()['created']==10
    ids=[m['id'] for m in result.json()['materials']]
    retry=s.client.post('/taxonomy/materials/group',json=body).json()
    assert retry['created']==0 and retry['reused']==10 and [m['id'] for m in retry['materials']]==ids
    next_group={**body,'subcategory_id':'fittings','rows':[{'description':'BCW Pipe Tee diameter 32 mm','unit':'PCS'}]}
    assert s.client.post('/taxonomy/materials/group',json=next_group).json()['created']==1
    invalid={**body,'rows':[{'description':'Valid pipe 99mm','unit':'M'},{'description':'Invalid unit pipe','unit':'INVALID'}]}
    assert s.client.post('/taxonomy/materials/group',json=invalid).status_code==422
    assert s.client.post('/taxonomy/materials/group',json={**body,'main_category_id':'sub'}).status_code==422
    s.actor[0]='buyer';assert s.client.post('/taxonomy/materials/group',json=body).status_code==403
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(Material))==11
        assert db.scalar(select(func.count()).select_from(Category))==3
        assert len(set(db.scalars(select(Material.normalized_description))))==11
        assert {m.discipline for m in db.scalars(select(Material))}=={'Mechanical'}


def test_grouped_headings_csv_mapping_approved_categories_only(system):
    s=system
    with s.db() as db:
        db.add_all([Category(id='main',code='M',name_en='Plumbing',normalized_name='plumbing'),Category(id='pipes',code='P',name_en='Approved pipes',normalized_name='approved pipes',parent_id='main'),Category(id='fits',code='F',name_en='Approved fittings',normalized_name='approved fittings',parent_id='main')]);db.commit()
    content=b'Main Category,Subcategory,Description,Unit,Quantity\n,,PIPES,,\n,,BCW Pipe diameter 20mm,M,128.38\n,,BCW Pipe diameter 25mm,M,27.17\n,,PIPE FITTINGS,,\n,,BCW Tee diameter 32mm,PCS,0\n'
    response=s.client.post('/imports/materials/preview',files={'file':('groups.csv',content,'text/csv')},data={'approved_only':'true'})
    assert response.status_code==202,response.text
    key=response.json()['batch_id'];preview=parse(s,key)
    assert preview['rows']==3 and preview['conflicts']==3 and len(preview['detected_groups'])==2
    mapping={g['key']:{'main_category_id':'main','subcategory_id':'pipes' if g['subcategory']=='PIPES' else 'fits'} for g in preview['detected_groups']}
    import json
    mapped=s.client.post(f'/imports/materials/{key}/preview-options',data={'group_mapping':json.dumps(mapping),'discipline':'Mechanical'})
    assert mapped.status_code==202,mapped.text
    preview=parse(s,key);assert preview['create']==3 and preview['new_categories']==[],preview
    assert s.client.post(f'/imports/materials/{key}/confirm').status_code==200
    with s.db() as db:imports.process_queued_material_imports(db)
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(Category))==3
        materials=db.scalars(select(Material)).all();assert len(materials)==3
        assert {m.name_en for m in materials}=={'BCW Pipe diameter 20mm','BCW Pipe diameter 25mm','BCW Tee diameter 32mm'}
        assert all(not hasattr(m,'quantity') for m in materials)


def test_different_specifications_are_flagged_for_review_not_merged(system):
    s=system
    book=Workbook();page=book.active
    page.append(['Main Category','Subcategory','Description','Unit','Technical specifications'])
    page.append(['Plumbing','Pipes','BCW Pipe diameter 20mm','M','Grade A'])
    page.append(['Plumbing','Pipes','BCW Pipe diameter 20mm','M','Grade B'])
    out=BytesIO();book.save(out)
    key=stage(s,out.getvalue());preview=parse(s,key)
    assert preview['create']==1 and preview['conflicts']==1 and preview['duplicate_rows']==0,preview


def test_group_reuses_legacy_material_without_normalized_description(system):
    s=system
    with s.db() as db:
        db.add(Category(id='main',code='M',name_en='Pipes',normalized_name='pipes'));db.flush()
        db.add(Material(id='legacy',code='LEGACY',name_en='Pipe diameter 20mm',normalized_description=None,unit='M',category_id='main',main_category_id='main',active=True));db.commit()
    body={'main_category_id':'main','rows':[{'description':'Pipe diameter 20mm','unit':'M'}]}
    response=s.client.post('/taxonomy/materials/group',json=body)
    assert response.status_code==200,response.text
    assert response.json()['created']==0 and response.json()['materials'][0]['id']=='legacy'


def test_row_discipline_precedence_and_errors():
    mapping = {'discipline':'Discipline','main_category':'Main','material_name':'Description','unit':'Unit'}
    rows = [{'_row':i,'Discipline':value,'Main':'Concrete','Description':'Concrete material','Unit':'M3'}
            for i,value in enumerate([' civil ', 'ELECTRICAL', 'Mechanical', '', 'Plumbing'], 2)]
    prepared = imports._prepared(rows, mapping, 'Mechanical')
    assert [r['raw']['_discipline'] for r in prepared[:4]] == ['Civil','Electrical','Mechanical','Mechanical']
    assert prepared[4]['status'] == 'invalid'
    assert imports._prepared([rows[3]], mapping, '')[0]['status'] == 'invalid'


@pytest.mark.parametrize('fallback', ['', 'Civil'])
def test_mixed_discipline_rows(system, fallback):
    s=system; book=Workbook(); page=book.active; page.title='Materials'
    page.append(['Discipline','Main Category','Subcategory','Description','Unit'])
    rows=[[' civil ','Concrete','Mix','Concrete mix 30MPa','M3'],['ELECTRICAL','Cables','Copper','Copper cable 10mm','M'],['Mechanical','Pipes','Steel','Steel pipe 20mm','M'],['Electrical','Cables','Copper','Copper cable 10mm','M'],['Invalid','Pipes','Steel','Invalid pipe 40mm','M'],['','Unmapped','Other','Fallback material 50mm','PCS']]
    for row in rows:page.append(row)
    out=BytesIO();book.save(out)
    def upload():
        return s.client.post('/imports/materials/preview',files={'file':('mixed.xlsx',out.getvalue())},data={'discipline':fallback}).json()['batch_id']
    key=upload();preview=parse(s,key)
    assert preview['duplicate_rows']==1
    assert preview['invalid']==(1 if fallback else 2)
    s.client.post(f'/imports/materials/{key}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    state=s.client.get('/imports/materials/'+key).json()
    assert state['status']=='completed_with_errors'
    assert state['created']==(4 if fallback else 3)
    assert state['completed_rows']==6 and state['progress_percent']==100
    with s.db() as db:
        records={m.name_en:m.discipline for m in db.scalars(select(Material))}
        assert records['Copper cable 10mm']=='Electrical'
        assert records['Steel pipe 20mm']=='Mechanical'
    assert parse(s,upload())['create']==0


def test_category_discipline_inference():
    mapping={'discipline':'Discipline','main_category':'Main','subcategory':'Sub','material_name':'Description','unit':'Unit'}
    row={'_row':2,'Main':'Pipes','Sub':'Steel','Description':'Steel pipe 20mm','Unit':'M'}
    result=imports._prepared([row],mapping,'',{('pipes','steel'):{'Mechanical'}})[0]
    assert result['status']=='valid' and result['raw']['_discipline']=='Mechanical'
    result=imports._prepared([row],mapping,'',{('pipes','steel'):{'Mechanical','Civil'}})[0]
    assert result['status']=='conflict' and 'multiple disciplines' in result['issue']
    result=imports._prepared([row],mapping,'Civil',{('pipes','steel'):{'Mechanical','Civil'}})[0]
    assert result['status']=='conflict' and 'multiple disciplines' in result['issue']



def test_infer_discipline_from_existing_material_category(system):
    s=system;key=stage(s,workbook());parse(s,key)
    s.client.post(f'/imports/materials/{key}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    content=workbook(rows=[['2','Reinforcement & Prestressing','Reinforcing Steel','New reinforcement 50mm','KG','']])
    key=s.client.post('/imports/materials/preview',files={'file':('materials.xlsx',content)}).json()['batch_id']
    assert parse(s,key)['create']==1
    with s.db() as db:
        row=db.scalar(select(ImportedRow).where(ImportedRow.batch_id==key))
        assert row.raw['_discipline']=='Civil'


FAILED_ROW_HEADERS = ['SN', 'Main Category', 'Sub Category', 'Description/Pacification',
                      'UNIT', 'Expenditures', 'Source Sheet', 'Source Row', 'Reason']
PREFIX_CATEGORIES = ['Electrical', 'Electrical - Low Current System', 'Mechanical - HVAC',
                     'Mechanical - Plumbing', 'Mechanical - Fire Fighting',
                     'Mechanical - Multi-System (Plumbing / Fire / HVAC)',
                     'Civil - Roofing', 'Civil - Reinforcement & Prestressing']


def failed_row_workbook(count):
    book = Workbook(); page = book.active; page.title = 'Failed Materials'
    page.append(FAILED_ROW_HEADERS)
    for i in range(count):
        page.append([str(i + 1), PREFIX_CATEGORIES[i % len(PREFIX_CATEGORIES)],
                     'Original subcategory', f'Material specification {i + 1} mm', 'PCS', '003-20',
                     'Original sheet', i + 2, 'Row discipline conflicts with the selected import discipline.'])
    out = BytesIO(); book.save(out); return out.getvalue()


@pytest.mark.parametrize('fallback', ['', 'Civil'])
def test_failed_row_workbook_prefixes_apply_and_reuse(system, fallback):
    s = system; content = failed_row_workbook(8)
    def upload():
        response = s.client.post('/imports/materials/preview',
            files={'file': ('failed-material-rows.xlsx', content)}, data={'discipline': fallback})
        assert response.status_code == 202
        return response.json()['batch_id']
    key = upload(); preview = parse(s, key)
    assert preview['valid'] == 8 and preview['invalid'] == 0 and preview['errors'] == 0
    assert preview['mapping']['material_name'] == 'Description/Pacification'
    assert preview['mapping']['subcategory'] == 'Sub Category'
    assert preview['mapping']['unit'] == 'UNIT'
    assert s.client.post(f'/imports/materials/{key}/confirm').json()['status'] == 'queued'
    with s.db() as db: imports.process_queued_material_imports(db)
    state = s.client.get('/imports/materials/' + key).json()
    assert state['status'] == 'completed' and state['created'] == 8
    with s.db() as db:
        categories = {c.id: c for c in db.scalars(select(Category))}
        originals = {m.id for m in db.scalars(select(Material))}
        for material in db.scalars(select(Material)):
            main = categories[material.main_category_id]
            sub = categories[material.subcategory_id]
            assert main.name_en in PREFIX_CATEGORIES and main.parent_id is None
            assert sub.name_en == 'Original subcategory' and sub.parent_id == main.id
            assert material.discipline == main.name_en.split(' - ')[0]
    key = upload(); preview = parse(s, key)
    assert preview['create'] == 0 and preview['unchanged'] == 8
    s.client.post(f'/imports/materials/{key}/confirm')
    with s.db() as db: imports.process_queued_material_imports(db)
    state = s.client.get('/imports/materials/' + key).json()
    assert state['created'] == 0 and state['unchanged_count'] == 8
    with s.db() as db:
        assert {m.id for m in db.scalars(select(Material))} == originals
        assert db.scalar(select(func.count()).select_from(Category)) == 16


def test_5118_failed_export_rows_preview_and_queue(system):
    s = system
    response = s.client.post('/imports/materials/preview',
        files={'file': ('failed-material-rows.xlsx', failed_row_workbook(5118))},
        data={'discipline': 'Civil', 'sheet_name': '__all__'})
    assert response.status_code == 202
    key = response.json()['batch_id']
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(Material)) == 0
    preview = parse(s, key)
    assert preview['rows'] == preview['valid'] == preview['create'] == 5118
    assert preview['invalid'] == preview['errors'] == preview['conflicts'] == 0
    assert preview['jobs_total'] == 18 and preview['batch_size'] == 30
    assert preview['issues'] == []
    response = s.client.post(f'/imports/materials/{key}/confirm')
    assert response.json()['status'] == 'queued'
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(Material)) == 0
        imports.process_queued_material_imports(db)
    state = s.client.get('/imports/materials/' + key).json()
    assert state['status'] == 'queued' and state['completed_rows'] == state['created'] == 300
    assert state['errors'] == 0 and state['current_job'] == 2
    # Every job uses a fresh session, as after a worker restart. Complete all
    # 5,118 rows, then prove a new upload matches the persisted identities.
    cursors = [state['completed_rows']]
    while state['status'] == 'queued':
        with s.db() as db: imports.process_queued_material_imports(db)
        state = s.client.get('/imports/materials/' + key).json()
        cursors.append(state['completed_rows'])
    assert cursors == list(range(300, 5118, 300)) + [5118]
    assert state['status'] == 'completed' and state['created'] == 5118
    assert state['errors'] == state['invalid'] == state['duplicate_rows'] == 0
    assert state['progress_percent'] == 100
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(Material)) == 5118
        assert db.scalar(select(func.count()).select_from(Category)) == 16
    response = s.client.post('/imports/materials/preview',
        files={'file': ('failed-material-rows.xlsx', failed_row_workbook(5118))},
        data={'discipline': 'Civil', 'sheet_name': '__all__'})
    repeated = parse(s, response.json()['batch_id'])
    assert repeated['create'] == 0 and repeated['unchanged'] == 5118
    assert repeated['invalid'] == repeated['duplicate_rows'] == 0


@pytest.mark.parametrize(('main', 'expected'), [
    (' cIvIl - Roofing ', 'Civil'), ('Electrical', 'Electrical'),
    ('Mechanical - Unseen category', 'Mechanical'), ('Electricality', None),
    ('Some Electrical category', None), ('Civilian - Roofing', None)])
def test_prefix_detection_is_generic_and_bounded(main, expected):
    assert imports._category_discipline(main) == expected


def test_prefix_does_not_bypass_invalid_material_or_explicit_discipline():
    mapping = imports._column_map(FAILED_ROW_HEADERS + ['Discipline'])
    base = dict(zip(FAILED_ROW_HEADERS, ['1', 'Mechanical - HVAC', 'Ducts', 'Duct 20mm', 'PCS', '', '', '', '']))
    base['_row'] = 2
    row = {**base, 'Discipline': 'Electrical'}
    result = imports._prepared([row], mapping, 'Civil')[0]
    assert result['raw']['_discipline'] == 'Electrical'
    assert result['main'] == 'Mechanical - HVAC'
    row['Discipline'] = 'Unsupported'
    assert 'Unsupported discipline' in imports._prepared([row], mapping, 'Civil')[0]['issue']
    assert imports._prepared([{**base, 'UNIT': ''}], mapping, '')[0]['issue'] == 'Missing unit of measurement'


@pytest.mark.parametrize(('main', 'sub'), [('Electrical', 'Electrical'), ('Civil - Roofing', 'Civil - Roofing')])
def test_prefix_inference_does_not_flatten_duplicate_category_levels(main, sub):
    mapping = imports._column_map(FAILED_ROW_HEADERS)
    row = {'_row': 2, 'Main Category': main, 'Sub Category': sub,
           'Description/Pacification': 'Valid material description', 'UNIT': 'PCS'}
    result = imports._prepared([row], mapping, '')[0]
    assert result['status'] == 'invalid' and 'must be distinct' in result['issue']


SUPPLIED_HEADERS = ['SN', 'Main Category', 'Sub Category', 'Description/Pacification', 'UNIT', 'Expenditures']


def supplied_workbook(rows, explicit=False):
    book=Workbook(); page=book.active; page.title='Materials'
    page.append(SUPPLIED_HEADERS + (['Discipline'] if explicit else []))
    for row in rows: page.append(row)
    out=BytesIO();book.save(out);return out.getvalue()


def upload_supplied(s, rows, fallback='', explicit=False):
    response=s.client.post('/imports/materials/preview',
        files={'file':('materials.xlsx',supplied_workbook(rows,explicit))},data={'discipline':fallback})
    assert response.status_code==202,response.text
    return response.json()['batch_id']


def test_exact_supplied_six_headers_fresh_database_pcs_pkt_and_reuse(system):
    s=system
    rows=[['1','Electrical','Accessories','Tape Cartridge Casio','PCS',''],
          ['2','Electrical','Consumable Material','3way Female Plug','PKT',''],
          ['3','Civil','Concrete','Concrete mix 30MPa','M3',''],
          ['4','Mechanical','Pipes','Steel pipe 20mm','M','']]
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(Category))==0
        assert db.scalar(select(func.count()).select_from(Material))==0
    def run():
        key=upload_supplied(s,rows,'Civil');preview=parse(s,key)
        assert preview['valid']==4 and preview['invalid']==preview['conflicts']==0
        s.client.post(f'/imports/materials/{key}/confirm')
        with s.db() as db:imports.process_queued_material_imports(db)
        return s.client.get('/imports/materials/'+key).json()
    state=run();assert state['created']==4 and state['errors']==0
    with s.db() as db:
        categories={c.id:c for c in db.scalars(select(Category))}
        materials={m.name_en:m for m in db.scalars(select(Material))}
        original_ids={m.id for m in materials.values()}
        assert materials['3way Female Plug'].unit=='PKT'
        assert db.scalar(select(MeasurementUnit).where(MeasurementUnit.code=='PKT')) is not None
        assert materials['Tape Cartridge Casio'].discipline=='Electrical'
        for m in materials.values():
            assert categories[m.subcategory_id].parent_id==m.main_category_id
            assert categories[m.main_category_id].parent_id is None
            assert m.category_id==m.subcategory_id
        assert len(categories)==7
    state=run();assert state['created']==0 and state['unchanged_count']==4
    with s.db() as db:
        assert {m.id for m in db.scalars(select(Material))}==original_ids
        assert db.scalar(select(func.count()).select_from(Category))==7


def test_mapping_precedes_upload_fallback_and_explicit_conflict_is_row_specific(system):
    s=system
    key=upload_supplied(s,[['1','Utilities','Pipes','Seed pipe 20mm','M','','Mechanical']],explicit=True)
    assert parse(s,key)['valid']==1
    s.client.post(f'/imports/materials/{key}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    key=upload_supplied(s,[['2','Utilities','Pipes','New pipe 30mm','M','']],fallback='Civil')
    preview=parse(s,key);assert preview['valid']==1
    assert preview['sample'][0]['discipline']=='Mechanical'
    assert imports._row_discipline('', 'Utilities', 'Fittings', 'Civil',
        {('utilities', ''): {'Mechanical'}}) == 'Mechanical'
    key=upload_supplied(s,[['3','Utilities','Pipes','Conflicting pipe 40mm','M','','Civil'],
                           ['4','Electrical','Accessories','Tape Cartridge Casio','PCS','','Electrical']],explicit=True)
    preview=parse(s,key)
    assert preview['valid']==1 and preview['conflicts']==1
    assert 'existing category mapping' in preview['issues'][0]['reason']
    s.client.post(f'/imports/materials/{key}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    state=s.client.get('/imports/materials/'+key).json()
    assert state['created']==1 and state['errors']==1 and state['completed_rows']==2
    with s.db() as db:
        assert db.scalar(select(Material).where(Material.name_en=='Conflicting pipe 40mm')) is None


def test_worker_rechecks_explicit_mapping_conflict_after_preview(system):
    s=system
    key=upload_supplied(s,[['1','Utilities','Pipes','Preview pipe 20mm','M','','Civil']],explicit=True)
    assert parse(s,key)['valid']==1
    seed=upload_supplied(s,[['2','Utilities','Pipes','Concurrent pipe 30mm','M','','Mechanical']],explicit=True)
    assert parse(s,seed)['valid']==1
    s.client.post(f'/imports/materials/{seed}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    s.client.post(f'/imports/materials/{key}/confirm')
    with s.db() as db:imports.process_queued_material_imports(db)
    state=s.client.get('/imports/materials/'+key).json()
    assert state['created']==0 and state['conflicts']==1 and state['errors']==1
    assert 'existing category mapping' in state['issues'][0]['reason']



def test_reupload_does_not_recover_stale_discipline_conflict_preview(system):
    s=system; content=failed_row_workbook(8)
    key=stage(s,content);parse(s,key)
    with s.db() as db:
        batch=db.get(ImportBatch,key);batch.preview_metadata={};batch.errors=8
        for row in db.scalars(select(ImportedRow).where(ImportedRow.batch_id==key)):
            row.status='invalid';row.issue='Row discipline conflicts with the selected import discipline.'
        db.commit()
    old=s.client.get('/imports/materials/'+key).json()
    assert old['status']=='needs_review'
    assert 'older validation rules' in old['failure_reason']
    assert s.client.post(f'/imports/materials/{key}/confirm').status_code==409
    new=stage(s,content);assert new!=key
    preview=parse(s,new)
    assert preview['valid']==8 and preview['invalid']==preview['errors']==0
    with s.db() as db:
        assert db.scalar(select(func.count()).select_from(ImportedRow).where(ImportedRow.batch_id==key))==8
    # The current preview is still recovered rather than spawning duplicate jobs.
    assert stage(s,content)==new


def test_stale_preview_can_be_rechecked_without_reupload(system):
    s=system;key=stage(s,failed_row_workbook(8));parse(s,key)
    with s.db() as db:
        batch=db.get(ImportBatch,key);batch.preview_metadata={};db.commit()
    response=s.client.post(f'/imports/materials/{key}/preview-options',data={'discipline':'Civil'})
    assert response.status_code==202
    preview=parse(s,key)
    assert preview['valid']==8 and preview['invalid']==0
