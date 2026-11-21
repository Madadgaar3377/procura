"""Local-only browser QA server with an isolated database and Gemini simulator.

Never import this module from application code. No production services are used.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.update(APP_ENV='test',DATABASE_URL='sqlite:///:memory:',REDIS_URL='',R2_ENABLED='false',MOCK_NOTIFICATIONS='true',GEMINI_API_KEY='')
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import sessionmaker
from fastapi.middleware.cors import CORSMiddleware
from app.database import Base,get_db
from app.models import Material,RFQ,RFQItem,Invitation,Outbox,Notification,ImportBatch,Capability
from app.modules.rfqs import imports as imp
from app.modules.rfqs import material_matching as matching
from app.modules.taxonomy.router import router as taxonomy
from app import catalog_cache
from test_rfq_material_import import env


class GeminiSimulator(BaseHTTPRequestHandler):
    requests=0
    def log_message(self,*args):pass
    def do_POST(self):
        GeminiSimulator.requests+=1
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        rows=json.loads(body['contents'][0]['parts'][0]['text'])['rows']
        matches=[{'row_id':r['row_id'],'material_id':r['candidates'][0]['id'],'confidence':.98} for r in rows if r['candidates']]
        data=json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps({'matches':matches})}]}}]}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)


def main():
    temporary=tempfile.TemporaryDirectory(prefix='procura-rfq-import-qa-',ignore_cleanup_errors=True);root=Path(temporary.name)
    patches=pytest.MonkeyPatch();fixture=env.__wrapped__(root,patches);state=next(fixture)
    # Give the real worker and API independent pooled SQLite connections.
    engine=create_engine('sqlite:///'+(root/'browser.sqlite').as_posix(),connect_args={'check_same_thread':False,'timeout':20})
    Base.metadata.create_all(engine)
    with state.engine.connect() as source,engine.begin() as target:
        for table in Base.metadata.sorted_tables:
            rows=[dict(row) for row in source.execute(select(table)).mappings()]
            if rows:target.execute(table.insert(),rows)
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    with sessions() as db:
        db.add(Capability(org_id='supplier-org',category_id='fits',city='Riyadh',confirmed=True,accepts_rfqs=True,eligibility_status='enabled',material_ids=[]));db.commit()
    def database():
        with sessions() as db:yield db
    app=state.app;app.dependency_overrides[get_db]=database;app.include_router(taxonomy,prefix='/api/v1')
    application=CORSMiddleware(app,allow_origins=['http://127.0.0.1:13132'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
    for name,value in [('get',lambda *a,**k:None),('set',lambda *a,**k:None),('version',lambda:0),('invalidate',lambda:None)]:patches.setattr(catalog_cache,name,value)
    @app.get('/api/v1/auth/me')
    def me():return {'id':'buyer','username':'buyer','full_name':'QA Buyer','phone':'+966501111110','phone_verified':True,'email_verified':True,'is_active':True,'is_admin':False}
    @app.get('/api/v1/organizations')
    def organizations():return [{'id':'org','name':'QA Buyer Organization','is_active':True,'is_buyer':True,'is_supplier':False,'city':'Riyadh','verified_status':'verified'}]
    @app.get('/api/v1/auth/workspace')
    def workspace():return {'role':'buyer','org_id':'org','destination':'/app/buyer/dashboard','needs_onboarding':False,'workspaces':[]}
    @app.get('/test/counts')
    def counts():
        with sessions() as db:return {**{model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (Material,RFQ,RFQItem,Invitation,Outbox,Notification,ImportBatch)},'gemini_requests':GeminiSimulator.requests}
    provider=ThreadingHTTPServer(('127.0.0.1',0),GeminiSimulator);provider_thread=threading.Thread(target=provider.serve_forever,daemon=True);provider_thread.start()
    original_client=httpx.Client
    class Transport(httpx.HTTPTransport):
        def handle_request(self,request):
            request.url=request.url.copy_with(scheme='http',host='127.0.0.1',port=provider.server_port)
            return super().handle_request(request)
    patches.setattr(matching.httpx,'Client',lambda **kwargs:original_client(transport=Transport(),**kwargs))
    patches.setattr(matching,'get_settings',lambda:SimpleNamespace(gemini_api_key=SecretStr('synthetic-browser-key'),gemini_model='simulated-model',gemini_timeout_seconds=2))
    stopped=threading.Event()
    def worker():
        while not stopped.wait(.1):
            with sessions() as db:imp.process_rfq_import_jobs(db)
    worker_thread=threading.Thread(target=worker,daemon=True);worker_thread.start()
    try:uvicorn.run(application,host='127.0.0.1',port=18142,log_level='warning',lifespan='off')
    finally:
        stopped.set();worker_thread.join(timeout=5);provider.shutdown();provider.server_close();patches.undo();fixture.close();engine.dispose();temporary.cleanup()


if __name__=='__main__':main()
