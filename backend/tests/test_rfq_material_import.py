"""Isolated buyer import -> matching -> reviewed RFQ -> supplier invitation tests."""
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import (User, Organization, Member, Category, Material, MeasurementUnit,
    ImportBatch, ImportedRow, Capability, RFQ, RFQItem, Invitation, Outbox, Notification)
from app.import_storage import LocalImportStorage
from app.security import current_user
from app.modules.rfqs.router import router
from app.modules.rfqs import imports as imp
from app.modules.rfqs import material_matching as matching


@pytest.fixture
def env(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///:memory:',poolclass=StaticPool,connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine);sessions=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(imp,'get_import_storage',lambda:LocalImportStorage(str(tmp_path)))
    monkeypatch.setattr(imp,'limit',lambda *a,**kw:None)
    actor=['buyer']
    with sessions() as db:
        for i,key in enumerate(['buyer','other','supplier']):db.add(User(id=key,username=key,phone=f'+96650111111{i}',password_hash='x',is_active=True))
        db.add_all([Organization(id='org',name='Buyer',name_normalized='buyer',city='Riyadh',is_buyer=True,is_supplier=False),Organization(id='other-org',name='Other',name_normalized='other',is_buyer=True,is_supplier=False),Organization(id='supplier-org',name='Supplier',name_normalized='supplier',city='Riyadh',is_buyer=False,is_supplier=True,verified_status='verified')]);db.flush()
        for user,org,role in [('buyer','org','owner'),('other','other-org','buyer'),('supplier','supplier-org','supplier')]:db.add(Member(user_id=user,organization_id=org,role=role))
        db.add_all([Category(id='main',code='PL',name_en='Plumbing',name_ar='السباكة',normalized_name='plumbing'),Category(id='hvac',code='HV',name_en='HVAC',normalized_name='hvac'),Category(id='fire',code='FF',name_en='Fire Fighting',normalized_name='fire fighting')]);db.flush()
        db.add_all([Category(id='pipes',code='PI',name_en='Pipes',name_ar='الأنابيب',parent_id='main',normalized_name='pipes'),Category(id='fits',code='FT',name_en='Pipe Fittings',parent_id='main',normalized_name='pipe fittings'),Category(id='hvpipes',code='HP',name_en='Pipes',parent_id='hvac',normalized_name='pipes')]);db.flush()
        for i in range(300):db.add(Material(id=f'pipe-{i}',code=f'PIPE-{i:03}',name_en=f'Boosted Cold Water (BCW) Pipe Ø{20+i}',normalized_description=f'boosted cold water (bcw) pipe ø{20+i}',category_id='pipes',main_category_id='main',subcategory_id='pipes',unit='M',active=True))
        db.add_all([Material(id='archived',code='ARCHIVED',name_en='Archived steel support',normalized_description='archived steel support',category_id='pipes',main_category_id='main',subcategory_id='pipes',unit='M',active=False),Material(id='chw',code='CHW',name_en='Chilled Water Pipe Ø20',normalized_description='chilled water pipe ø20',category_id='hvpipes',main_category_id='hvac',subcategory_id='hvpipes',unit='M',active=True)])
        db.add_all([MeasurementUnit(code='M',name_en='Meter',active=True),MeasurementUnit(code='NO.',name_en='Number',active=True)])
        db.add(Capability(org_id='supplier-org',category_id='pipes',city='Riyadh',confirmed=True,accepts_rfqs=True,eligibility_status='enabled',material_ids=['pipe-0']))
        db.commit()
    app=FastAPI();app.include_router(router,prefix='/api/v1')
    def database():
        with sessions() as db:yield db
    def user():
        with sessions() as db:return db.get(User,actor[0])
    app.dependency_overrides[get_db]=database;app.dependency_overrides[current_user]=user
    with TestClient(app) as client:yield SimpleNamespace(client=client,db=sessions,engine=engine,actor=actor,app=app)
    engine.dispose()


def upload(e,content=None,name='materials.csv',ai=False,**kwargs):
    content=content or b'Description,Unit,Qty\nBoosted Cold Water (BCW) Pipe \xc3\x9820,m,128.38\n'
    response=e.client.post('/api/v1/rfqs/imports',files={'file':(name,content)},data={'org_id':'org','draft_key':'draft-1','ai_enabled':str(ai).lower(),**kwargs})
    assert response.status_code==202,response.text
    return response.json()['id']


def work(e,key,max_ticks=15):
    for _ in range(max_ticks):
        with e.db() as db:imp.process_rfq_import_jobs(db)
        response=e.client.get('/api/v1/rfqs/imports/'+key);assert response.status_code==200,response.text
        job=response.json()
        if job['status'] not in {'uploaded','queued','processing'}:return job
    pytest.fail('Job did not complete in bounded chunks')


def accept_all(e,key):
    result=e.client.post(f'/api/v1/rfqs/imports/{key}/review',json={'action':'accept_high_confidence'})
    assert result.status_code==200,result.text
    return result.json()


def test_template_real_xlsx_numeric_examples_and_actual_terminology(env):
    e=env;response=e.client.get('/api/v1/rfqs/imports/template?org_id=org');assert response.status_code==200
    book=load_workbook(BytesIO(response.content));assert book.sheetnames==['Materials','Instructions']
    assert list(next(book['Materials'].values))==imp.HEADERS
    assert book['Materials']['E2'].value==128.38 and book['Materials']['E2'].number_format=='0.000'
    assert any('Plumbing / Pipes' in str(row[0]) for row in book['Instructions'].values)
    key=upload(e,response.content,'template.xlsx');job=work(e,key)
    assert job['total']==4 and job['matched']==1,job


def test_exact_import_confirm_preserves_ids_decimals_and_publication_only_matching(env):
    e=env;key=upload(e);job=work(e,key)
    assert job['matched']==1 and job['rows'][0]['material_id']=='pipe-0' and not job['rows'][0]['accepted'],job
    assert e.client.post(f'/api/v1/rfqs/imports/{key}/confirm').status_code==409
    with e.db() as db:
        assert db.scalar(select(func.count()).select_from(Invitation))==0
        assert db.scalar(select(func.count()).select_from(Outbox))==0
        assert db.scalar(select(func.count()).select_from(Notification))==0
    accept_all(e,key)
    first=e.client.post(f'/api/v1/rfqs/imports/{key}/confirm');assert first.status_code==200,first.text
    second=e.client.post(f'/api/v1/rfqs/imports/{key}/confirm');assert second.json()==first.json()
    line=first.json()['lines'][0];assert line['material_id']=='pipe-0' and line['quantity']=='128.38'
    manual={'main_category_id':'main','subcategory_id':'pipes','description':'Manual pipe support','unit':'M','quantity':'2','material_id':None,'specifications':''}
    payload={'buyer_org_id':'org','title':'Imported pipe request','city':'Riyadh','response_deadline':(datetime.now(UTC)+timedelta(days=7)).isoformat(),'items':[manual,{k:v for k,v in line.items() if k!='key'}]}
    response=e.client.post('/api/v1/rfqs',json=payload);assert response.status_code==201,response.text
    rfq=response.json();assert len(rfq['items'])==2 and rfq['rfq']['status']=='draft'
    published=e.client.post(f"/api/v1/rfqs/{rfq['rfq']['id']}/publish");assert published.status_code==200,published.text
    assert published.json()['invited_suppliers']==1,published.text
    assert e.client.post(f"/api/v1/rfqs/{rfq['rfq']['id']}/publish").status_code==200
    with e.db() as db:
        assert db.scalar(select(func.count()).select_from(Invitation))==1
        assert db.scalar(select(func.count()).select_from(Material))==302


def test_sections_merged_cells_repeated_headers_arabic_and_custom_review(env):
    e=env;book=Workbook();sheet=book.active;sheet.title='Materials';sheet.append(['Description','Unit','Qty'])
    sheet.append(['PIPES']);sheet.merge_cells('A2:C2')
    sheet.append(['Boosted Cold Water (BCW) Pipe Ø20','m',128.38]);sheet.append(['Description','Unit','Qty']);sheet.append([None,None,None]);sheet.append(['PIPE FITTINGS']);sheet.append(['وصلة مخصصة قطر ٣٢','nr',4])
    out=BytesIO();book.save(out);key=upload(e,out.getvalue(),'sections.xlsx');job=work(e,key)
    assert job['total']==2 and job['rows'][1]['group']=='PIPE FITTINGS',job
    row=job['rows'][1];assert row['original_description']=='وصلة مخصصة قطر ٣٢' and row['unit']=='NO.'
    assert row['status']=='no_match'
    body={**row,'main_category_id':'main','subcategory_id':'fits','accepted':True,'expected_version':row['version']}
    saved=e.client.put(f"/api/v1/rfqs/imports/{key}/rows/{row['id']}",json=body);assert saved.status_code==200,saved.text
    accept_all(e,key);assert e.client.post(f'/api/v1/rfqs/imports/{key}/confirm').status_code==200


def test_300_rows_chunks_duplicate_upload_and_resume_expired_lease(env):
    e=env;content=('Description,Unit,Qty\n'+'\n'.join(f'Boosted Cold Water (BCW) Pipe Ø{20+i},m,1.125' for i in range(300))).encode()
    key=upload(e,content);assert upload(e,content)==key
    with e.db() as db:imp.process_rfq_import_jobs(db)
    with e.db() as db:imp.process_rfq_import_jobs(db)
    job=e.client.get('/api/v1/rfqs/imports/'+key).json();assert job['processed']==30 and job['total']==300
    with e.db() as db:
        batch=db.get(ImportBatch,key);batch.status='processing';batch.import_options={**batch.import_options,'lease_until':(datetime.now(UTC)-timedelta(seconds=1)).isoformat()};db.commit()
    job=work(e,key);assert job['matched']==300 and job['processed']==300,job
    accept_all(e,key);response=e.client.post(f'/api/v1/rfqs/imports/{key}/confirm');assert response.status_code==200,response.text
    assert len(response.json()['lines'])==300 and len({l['key'] for l in response.json()['lines']})==300
    payload={'buyer_org_id':'org','title':'300 materials','city':'','response_deadline':(datetime.now(UTC)+timedelta(days=7)).isoformat(),'items':response.json()['lines']}
    material_queries=[]
    def track(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM materials' in statement:material_queries.append(statement)
    event.listen(e.engine,'before_cursor_execute',track)
    created=e.client.post('/api/v1/rfqs',json=payload);assert created.status_code==201,created.text
    event.remove(e.engine,'before_cursor_execute',track)
    assert len(material_queries)==1,'Creating 300 RFQ lines must not fetch every master separately'


def test_archived_no_match_wrong_diameter_and_wrong_system(env):
    e=env;content='Description,Unit,Qty\nArchived steel support,m,2\nBCW Pipe Ø999,m,5\nFire Fighting Pipe Ø20,m,8\n'.encode()
    key=upload(e,content);job=work(e,key)
    assert job['unmatched']==3 and all(not row['candidates'] for row in job['rows']),job
    row=job['rows'][1];body={**row,'main_category_id':'main','subcategory_id':'pipes','material_id':'pipe-0','accepted':True,'expected_version':row['version']}
    response=e.client.put(f"/api/v1/rfqs/imports/{key}/rows/{row['id']}",json=body);assert response.status_code==422,response.text


def test_gemini_fallback_different_wording_and_retry_does_not_hold_transaction(env,monkeypatch):
    e=env
    def timeout(rows):
        # A separate connection can be checked out; session has no active SQL transaction.
        assert not session_holder[0].in_transaction()
        return {},True
    monkeypatch.setattr(imp,'rerank',timeout)
    key=upload(e,b'Description,Unit,Qty\nBCW cold water pipe diameter 20,m,2.75\n',ai=True)
    with e.db() as db:imp.process_rfq_import_jobs(db)
    session_holder=[]
    with e.db() as db:session_holder.append(db);imp.process_rfq_import_jobs(db)
    job=e.client.get('/api/v1/rfqs/imports/'+key).json();row=job['rows'][0]
    assert row['status']=='needs_review' and row['ai_fallback'] and row['material_id']=='pipe-0',job
    assert not row['accepted']
    assert e.client.post(f'/api/v1/rfqs/imports/{key}/retry',json={'row_ids':[row['id']]}).status_code==202
    monkeypatch.setattr(imp,'rerank',lambda rows:({rows[0]['id']:('pipe-0',.88)},False))
    job=work(e,key);assert job['rows'][0]['confidence']==.88 and not job['rows'][0]['accepted']


def test_unknown_columns_mapping_validation_limits_and_failed_job_retry(env,monkeypatch):
    e=env;key=upload(e,b'Text,Measure,Amount\nBoosted Cold Water (BCW) Pipe \xc3\x9820,m,1\n')
    job=work(e,key);assert job['status']=='needs_mapping' and job['headers']==['Text','Measure','Amount'],job
    assert e.client.put(f'/api/v1/rfqs/imports/{key}/options',json={'column_mapping':{'material_name':'Text','unit':'Measure','quantity':'Amount'}}).status_code==202
    assert work(e,key)['matched']==1
    serial_column=upload(e,b'Item,Description,UOM,Quantity\n1,Boosted Cold Water (BCW) Pipe \xc3\x9820,m,1\n')
    assert work(e,serial_column)['matched']==1
    bad=e.client.post('/api/v1/rfqs/imports',files={'file':('x.xlsx',b'not a workbook')},data={'org_id':'org','draft_key':'bad'});assert bad.status_code==422
    too_many=('Description,Unit,Qty\n'+'test pipe,m,1\n'*301).encode();key2=upload(e,too_many)
    failed=work(e,key2);assert failed['status']=='failed' and '300' in failed['failure_reason']
    malformed=upload(e,b'Description,Unit,Qty\nPipe diameter 20,m,1,234\n')
    assert work(e,malformed)['status']=='failed'
    key3=upload(e,b'Description,Unit,Qty\nCustom thing,m,0\n')
    failed_row=work(e,key3)['rows'][0];assert failed_row['status']=='failed'
    recovered=e.client.put(f"/api/v1/rfqs/imports/{key3}/rows/{failed_row['id']}",json={**failed_row,'quantity':'3.5','main_category_id':'main','subcategory_id':'pipes','accepted':True,'expected_version':failed_row['version']})
    assert recovered.status_code==200 and recovered.json()['accepted'] and recovered.json()['issue'] is None,recovered.text
    assert e.client.post(f'/api/v1/rfqs/imports/{key3}/review',json={'action':'remove','row_ids':[failed_row['id']]}).status_code==200


def test_organization_isolation_every_import_mutation(env):
    e=env;key=upload(e);job=work(e,key);row=job['rows'][0]
    e.actor[0]='other'
    paths=[('get',f'/api/v1/rfqs/imports/{key}',None),('post',f'/api/v1/rfqs/imports/{key}/retry',{}),('post',f'/api/v1/rfqs/imports/{key}/confirm',None),('post',f'/api/v1/rfqs/imports/{key}/review',{'action':'remove'}),('put',f'/api/v1/rfqs/imports/{key}/options',{}),('put',f"/api/v1/rfqs/imports/{key}/rows/{row['id']}",{**row,'expected_version':1,'main_category_id':'main','subcategory_id':'pipes'})]
    for method,path,body in paths:
        response=getattr(e.client,method)(path,**({'json':body} if body is not None else {}));assert response.status_code==404,(path,response.text)
    assert e.client.get('/api/v1/rfqs/imports/template?org_id=org').status_code==404
    assert e.client.get('/api/v1/rfqs/imports/recent?org_id=org&draft_key=draft-1').status_code==404
    e.actor[0]='supplier'
    response=e.client.post('/api/v1/rfqs/imports',files={'file':('x.csv',b'Description,Unit,Qty\naaa,m,1')},data={'org_id':'supplier-org','draft_key':'d'});assert response.status_code==403
    e.app.dependency_overrides.pop(current_user)
    assert e.client.get(f'/api/v1/rfqs/imports/{key}').status_code==401


@pytest.mark.parametrize('left,right',[
    ('BCW Pipe Ø20','BCW Pipe Ø50'),('Fire Fighting Pipe DN100','Chilled Water Pipe DN100'),
    ('Copper Cable 4C x 16mm²','Copper Cable 4C x 25mm²'),('Steel Pipe PN16','Steel Pipe PN10'),
    ('Steel ASTM A615 Grade 60','Steel ASTM A615 Grade 40'),('Cable 400 V','Cable 230 V')])
def test_technical_guards(left,right):
    assert not matching.compatible(left,right)
    assert not matching.compatible('PEX-AL-PEX Pipe Ø26','PEX Pipe Ø26')
    assert not matching.compatible('HDPE PE100 Pipe Ø110 SDR11','HDPE PE80 Pipe Ø110 SDR11')


def test_gemini_real_http_serialization_timeout_and_id_allowlist(env,monkeypatch):
    """Actual HTTP to a local provider simulator; no live Gemini calls or keys."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import time
    import httpx
    from pydantic import SecretStr
    mode=['success'];seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));seen.append((self.path,self.headers.get('x-goog-api-key'),body))
            data=json.loads(body['contents'][0]['parts'][0]['text'])
            if mode[0]=='timeout':time.sleep(.3)
            matches=[{'row_id':r['row_id'],'material_id':'invented-id' if mode[0]=='invent' else r['candidates'][0]['id'],'confidence':.99} for r in data['rows']]
            payload=json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps({'matches':matches})}]}}]}).encode()
            try:self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    real_client=httpx.Client
    class LocalTransport(httpx.HTTPTransport):
        def handle_request(self,request):
            request.url=request.url.copy_with(scheme='http',host='127.0.0.1',port=server.server_port)
            return super().handle_request(request)
    monkeypatch.setattr(matching,'get_settings',lambda:SimpleNamespace(gemini_api_key=SecretStr('synthetic-test-key'),gemini_model='test-model',gemini_timeout_seconds=.1))
    monkeypatch.setattr(matching.httpx,'Client',lambda **kwargs:real_client(transport=LocalTransport(),**kwargs))
    try:
        e=env;key=upload(e,b'Description,Unit,Qty\nBCW cold water pipe diameter 20,m,2.75\n',ai=True)
        row=work(e,key)['rows'][0];assert row['material_id']=='pipe-0' and row['confidence']==.89 and not row['accepted']
        assert seen[0][0]=='/v1beta/models/test-model:generateContent' and seen[0][1]=='synthetic-test-key'
        assert seen[0][2]['generationConfig']['responseMimeType']=='application/json'
        mode[0]='invent';assert matching.rerank([{'id':'row','description':'BCW pipe Ø20','group':'','attributes':{},'candidates':[{'id':'pipe-0'}]}])[0]=={}
        mode[0]='timeout'
        assert e.client.post(f'/api/v1/rfqs/imports/{key}/retry',json={'row_ids':[row['id']]}).status_code==202
        row=work(e,key)['rows'][0];assert row['ai_fallback'] and row['status']=='needs_review'
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_failed_chunk_retry_preserves_completed_rows_and_distant_sizes(env,monkeypatch):
    e=env;content=('Description,Unit,Qty\n'+'\n'.join(f'BCW cold water pipe diameter {100+i},m,2.5' for i in range(60))).encode()
    key=upload(e,content)
    for _ in range(2):
        with e.db() as db:imp.process_rfq_import_jobs(db)
    job=e.client.get('/api/v1/rfqs/imports/'+key).json();assert job['processed']==30
    first_ids=[r['id'] for r in job['rows']]
    original=imp.shortlist
    monkeypatch.setattr(imp,'shortlist',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('synthetic interruption')))
    with e.db() as db:imp.process_rfq_import_jobs(db)
    job=e.client.get('/api/v1/rfqs/imports/'+key).json();assert job['status']=='failed' and job['processed']==30
    monkeypatch.setattr(imp,'shortlist',original)
    assert e.client.post(f'/api/v1/rfqs/imports/{key}/retry',json={}).status_code==202
    job=work(e,key);assert job['processed']==60 and job['needs_review']==60,job
    assert [r['id'] for r in job['rows']]==first_ids
    assert job['rows'][0]['material_id']=='pipe-80'


def test_arabic_headers_and_size_digits_find_real_catalog_candidate(env):
    e=env;content='الوصف,الوحدة,الكمية\nأنبوب قطر ٣٢,م,٢٫٥\n'.encode()
    key=upload(e,content);job=work(e,key);row=job['rows'][0]
    assert row['quantity']=='2.5' and row['material_id']=='pipe-12' and row['status']=='needs_review',job
    with e.db() as db:
        db.add(Material(id='arabic-only',code='AR-33',name_en='أنبوب قطر ٣٣',name_ar='أنبوب قطر ٣٣',normalized_description='أنبوب قطر ٣٣',category_id='pipes',main_category_id='main',subcategory_id='pipes',unit='M',active=True));db.commit()
    key=upload(e,'الوصف,الوحدة,الكمية\nأنبوب قطر ٣٣,م,٢٫٥\n'.encode());row=work(e,key)['rows'][0]
    assert row['material_id']=='arabic-only' and row['status']=='high_confidence',row
