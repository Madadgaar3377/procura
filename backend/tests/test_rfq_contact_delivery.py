"""Synthetic RFQ publication -> matching -> durable jobs -> provider boundaries."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import notification_worker as worker
from app.config import get_settings
from app.models import (Capability, Category, Contact, ContactCapability, ContactChannel, Member,
                        DeliveryRestriction, Invitation, Material, Organization,
                        Outbox, RFQ, RFQItem, User)
from app.modules.notifications.delivery import delivery_error
from app.modules.notifications.router import router
from app.database import get_db
from app.security import current_user
from test_rfq_publish_matching_fallback import setup_matching_app


@pytest.fixture
def flow(monkeypatch):
    client,sessions,engine,ids=setup_matching_app('')
    settings=get_settings().model_copy(update={'mock_notifications':False})
    monkeypatch.setattr(worker,'get_settings',lambda:settings)
    calls=[]
    monkeypatch.setattr(worker,'send_email',lambda address,*args: calls.append(('email',address)) or '<smtp-id>')
    monkeypatch.setattr(worker,'send_sms',lambda phone,*args,**kwargs: calls.append(('sms',phone)) or {'id':'sms-id','state':'Pending','http_status':202})
    monkeypatch.setattr(worker,'get_sms_status',lambda key: {'id':key,'state':'Delivered','lookup':True,'http_status':200})
    with sessions() as db:
        org=db.get(Organization,ids['supplier']);org.source='imported';org.verified_status='unverified'
        org.business_email='Sales@Example.com';org.business_phone='0501234567'
        # Imported supplier has no registered account; buyer remains authorized.
        db.execute(delete(Member).where(Member.organization_id==org.id))
        leaf=db.scalar(select(Category).where(Category.parent_id.is_not(None)))
        mat=Material(code='DELIVERY-MAT',name_en='Test requested material',category_id=leaf.id,
                     main_category_id=leaf.parent_id,subcategory_id=leaf.id,unit='EA')
        db.add(mat);db.flush()
        item=db.scalar(select(RFQItem).where(RFQItem.rfq_id==ids['rfq']));item.material_id=mat.id
        ids['material']=mat.id;ids['subcategory']=leaf.id;ids['main']=leaf.parent_id
        db.commit()
    def publish():
        response=client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
        assert response.status_code==200,response.text
        with sessions() as db:
            jobs=db.scalars(select(Outbox).where(Outbox.event=='rfq.invitation')).all()
            assert len(jobs)==1
            worker.process_batch(db,event_ids=[job.id for job in jobs])
            db.commit()
    def process():
        with sessions() as db:
            job_ids=db.scalars(select(Outbox.id).where(Outbox.event=='notification.delivery')).all()
            worker.process_batch(db,event_ids=job_ids)
    yield client,sessions,ids,publish,process,calls
    engine.dispose()


def deliveries(db):
    return {j.payload['channel']:j for j in db.scalars(select(Outbox).where(Outbox.event=='notification.delivery'))}


@pytest.mark.parametrize('verification,source',[('verified','registered'),('unverified','registered'),('pending','imported'),('verified','imported')])
def test_company_and_contact_verification_are_not_delivery_gates(flow,verification,source):
    client,sessions,ids,publish,process,calls=flow
    with sessions() as db:
        org=db.get(Organization,ids['supplier']);org.verified_status=verification;org.source=source
        contact=Contact(org_id=org.id,name=None,is_primary=True,email='SALES@example.com',phone='0501234567')
        db.add(contact);db.flush()
        db.add(ContactChannel(org_id=org.id,contact_id=contact.id,kind='email',value='SALES@example.com',normalized_value='sales@example.com',is_verified=verification=='verified'))
        db.commit()
    publish();process()
    assert sorted(calls)==[('email','sales@example.com'),('sms','+966501234567')]
    with sessions() as db:
        jobs=deliveries(db)
        assert jobs['email'].delivery_status=='accepted' and jobs['email'].payload['smtp_accepted']
        assert jobs['sms'].status=='awaiting_status' and jobs['sms'].delivery_status=='Pending'
        assert jobs['email'].payload['contact_name'] is None
        # Restart/poll: never repeat provider submission.
        worker.process_batch(db,now=datetime.now(UTC)+timedelta(minutes=1),event_ids=[jobs['sms'].id,jobs['email'].id])
        assert db.get(Outbox,jobs['sms'].id).delivery_status=='Delivered'
    assert len(calls)==2


@pytest.mark.parametrize('email,phone,skipped',[(None,None,{'email','sms'}),('sales@example.com',None,{'sms'}),(None,'0501234567',{'email'}),('sales@example.com','0501234567',set()),(None,'0112345678',{'email','sms'}),(None,'bad',{'email','sms'})])
def test_independent_missing_and_invalid_channels(flow,email,phone,skipped):
    _,sessions,ids,publish,process,calls=flow
    with sessions() as db:
        org=db.get(Organization,ids['supplier']);org.business_email=email;org.business_phone=phone;db.commit()
    publish();process()
    with sessions() as db:
        jobs=deliveries(db)
        assert {c for c,j in jobs.items() if j.status=='skipped'}==skipped
        if not phone:
            assert jobs['sms'].delivery_status=='no_contact'
            assert jobs['sms'].payload['reason_detail']=='No phone available'
        elif phone in {'bad','0112345678'}:
            assert jobs['sms'].delivery_status=='invalid_contact'
    assert len(calls)==2-len(skipped)


@pytest.mark.parametrize('scope',['material','subcategory','primary'])
def test_assignment_priority_channel_fallback_and_no_unrelated_employees(flow,scope):
    _,sessions,ids,publish,process,calls=flow
    with sessions() as db:
        cap=db.scalar(select(Capability).where(Capability.org_id==ids['supplier'],Capability.category_id==ids['subcategory']))
        contact=Contact(org_id=ids['supplier'],email='assigned@example.com',name=None,is_primary=scope=='primary')
        unrelated=Contact(org_id=ids['supplier'],email='unrelated@example.com',permission_to_notify=True,role='HR')
        db.add_all([contact,unrelated]);db.flush()
        if scope!='primary':
            db.add(ContactCapability(contact_id=contact.id,capability_id=cap.id,material_ids=[ids['material']] if scope=='material' else None))
        db.commit()
    publish();process()
    assert calls==[('email','assigned@example.com'),('sms','+966501234567')]
    with sessions() as db:
        assert deliveries(db)['email'].payload['recipient_source']==scope
        assert deliveries(db)['sms'].payload['recipient_source']=='company'


@pytest.mark.parametrize('failed_channel',['sms','email'])
def test_provider_failure_isolated_and_bounded_retry(flow,monkeypatch,failed_channel):
    _,sessions,ids,publish,process,calls=flow
    def fail(*args,**kwargs):raise delivery_error('Controlled provider failure')
    monkeypatch.setattr(worker,'send_'+failed_channel,fail)
    publish();process()
    with sessions() as db:
        jobs=deliveries(db);failed=jobs[failed_channel]
        assert failed.status=='retry'
        assert jobs['sms' if failed_channel=='email' else 'email'].status in {'processed','awaiting_status'}
        for i in range(7):
            worker.process_batch(db,event_ids=[failed.id],now=datetime.now(UTC)+timedelta(days=i+1))
        assert db.get(Outbox,failed.id).status=='failed'
        assert db.get(Outbox,failed.id).attempts==get_settings().delivery_max_attempts


@pytest.mark.parametrize('reason',['suppressed','opted_out'])
def test_restrictions_prevent_delivery_and_are_rechecked(flow,reason):
    _,sessions,ids,publish,process,calls=flow
    publish()
    with sessions() as db:
        db.add(DeliveryRestriction(channel='email',destination='sales@example.com',reason=reason))
        db.commit()
    process()
    assert calls==[('sms','+966501234567')]
    with sessions() as db:assert deliveries(db)['email'].delivery_status==reason


def test_invitation_and_recipient_jobs_are_idempotent(flow):
    client,sessions,ids,publish,process,calls=flow
    publish();publish();process();process()
    with sessions() as db:
        assert len(db.scalars(select(Invitation)).all())==1
        assert len(db.scalars(select(Outbox).where(Outbox.event=='notification.delivery')).all())==2
    assert len(calls)==2


def test_admin_restriction_and_feedback_authorization(flow):
    _,sessions,ids,publish,process,calls=flow
    app=FastAPI();app.include_router(router)
    def session():
        with sessions() as db:yield db
    app.dependency_overrides[get_db]=session
    with sessions() as db:user=db.get(User,ids['user'])
    app.dependency_overrides[current_user]=lambda:user
    client=TestClient(app)
    restriction={'channel':'email','destination':'Sales@Example.com','reason':'opted_out','detail':'Controlled customer opt-out'}
    assert client.post('/notifications/admin/restrictions',json=restriction).status_code==403
    assert client.post('/notifications/admin/outbox/missing/feedback',json={'outcome':'bounced','evidence':'SMTP log'}).status_code==403
    user.is_admin=True
    assert client.post('/notifications/admin/restrictions',json=restriction).status_code==200
    assert client.post('/notifications/admin/restrictions',json={**restriction,'destination':'bad'}).status_code==422
    publish();process()
    with sessions() as db:
        jobs=deliveries(db);assert jobs['email'].status=='skipped'
        assert jobs['email'].delivery_status=='opted_out'
        sms_id=jobs['sms'].id
    assert client.post(f'/notifications/admin/outbox/{sms_id}/feedback',json={'outcome':'delivered','evidence':'Not a valid SMS receipt'}).status_code==409


def test_smtp_unknown_outcome_never_automatically_resubmits(flow,monkeypatch):
    _,sessions,ids,publish,process,calls=flow
    def timeout(*args):raise delivery_error('SMTP DATA timed out',uncertain=True)
    monkeypatch.setattr(worker,'send_email',timeout)
    publish();process()
    with sessions() as db:
        job=deliveries(db)['email'];assert job.status=='uncertain'
        worker.process_batch(db,now=datetime.now(UTC)+timedelta(days=1),event_ids=[job.id])
        assert job.status=='uncertain' and job.attempts==1


def test_reviewed_email_bounce_suppresses_future_deliveries(flow):
    _,sessions,ids,publish,process,calls=flow
    publish();process()
    app=FastAPI();app.include_router(router)
    def session():
        with sessions() as db:yield db
    app.dependency_overrides[get_db]=session
    with sessions() as db:
        user=db.get(User,ids['user']);user.is_admin=True
        email_id=deliveries(db)['email'].id
    app.dependency_overrides[current_user]=lambda:user
    client=TestClient(app)
    response=client.post(f'/notifications/admin/outbox/{email_id}/feedback',json={
        'outcome':'bounced','evidence':'Controlled SMTP DSN: permanent recipient failure'})
    assert response.status_code==200,response.text
    with sessions() as db:
        assert db.get(Outbox,email_id).delivery_status=='bounced'
        assert db.scalar(select(DeliveryRestriction).where(DeliveryRestriction.destination=='sales@example.com')).reason=='suppressed'


def test_admin_assignment_preserves_contact_material_scope(flow):
    from app.modules.organizations.router import router as org_router
    _,sessions,ids,publish,process,calls=flow
    app=FastAPI();app.include_router(org_router)
    def session():
        with sessions() as db:yield db
    app.dependency_overrides[get_db]=session
    with sessions() as db:
        user=db.get(User,ids['user']);user.is_admin=True
        contact=Contact(org_id=ids['supplier'],email='assigned@example.com')
        other_org=Organization(name='Other',name_normalized='other',is_supplier=True)
        db.add_all([contact,other_org]);db.commit();contact_id=contact.id;other_id=other_org.id
    app.dependency_overrides[current_user]=lambda:user
    client=TestClient(app)
    payload={'category_id':ids['subcategory'],'material_ids':[ids['material']]}
    assert client.post(f'/organizations/admin/suppliers/{other_id}/contacts/{contact_id}/capabilities',json=payload).status_code==404
    response=client.post(f"/organizations/admin/suppliers/{ids['supplier']}/contacts/{contact_id}/capabilities",json=payload)
    assert response.status_code==201,response.text
    with sessions() as db:
        link=db.scalar(select(ContactCapability).where(ContactCapability.contact_id==contact_id))
        assert link.material_ids==[ids['material']]


def test_material_contact_wins_and_duplicates_are_normalized(flow):
    _,sessions,ids,publish,process,calls=flow
    with sessions() as db:
        cap=db.scalar(select(Capability).where(Capability.org_id==ids['supplier'],Capability.category_id==ids['subcategory']))
        for email,scope in [('category@example.com','category'),('EXACT@example.com','material')]:
            c=Contact(org_id=ids['supplier'],email=email);db.add(c);db.flush()
            db.add(ContactCapability(contact_id=c.id,capability_id=cap.id,
                material_ids=[ids['material']] if scope=='material' else None))
        item=db.scalar(select(RFQItem).where(RFQItem.rfq_id==ids['rfq']))
        db.add(RFQItem(rfq_id=ids['rfq'],material_id=ids['material'],main_category_id=ids['main'],
            subcategory_id=ids['subcategory'],description=item.description,quantity=1,unit='EA',specifications=''))
        db.commit()
    publish();process()
    assert sorted(calls)==[('email','exact@example.com'),('sms','+966501234567')]


def test_email_provider_auth_tls_multipart_and_acceptance(monkeypatch):
    from app.modules.notifications import delivery
    settings=get_settings().model_copy(update={'mock_notifications':False,'smtp_host':'smtp.example.com',
        'smtp_username':'notifications@example.com','smtp_password':'synthetic-test-secret',
        'smtp_from_email':'notifications@example.com','smtp_reply_to':'support@example.com','smtp_port':465})
    monkeypatch.setattr(delivery,'get_settings',lambda:settings)
    messages=[]
    class SMTPBoundary:
        def __init__(self,host,port,**kwargs):assert host=='smtp.example.com' and port==465 and kwargs['context']
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def login(self,username,password):assert username==settings.smtp_username and password==settings.smtp_password
        def send_message(self,message):messages.append(message);return {}
    monkeypatch.setattr(delivery,'SMTP_SSL',SMTPBoundary)
    message_id=delivery.send_email('test@example.com','RFQ invitation','Plain text','<p>HTML</p>','test-key')
    assert messages[0]['Message-ID']==message_id
    assert message_id.endswith('@example.com>')
    assert messages[0]['Reply-To']=='support@example.com'
    assert [part.get_content_type() for part in messages[0].iter_parts()]==['text/plain','text/html']


def test_sms_http202_never_means_delivered(monkeypatch):
    import httpx
    from app.modules.notifications import delivery
    settings=get_settings().model_copy(update={'mock_notifications':False,'sms_gateway_enabled':True,
        'sms_gateway_mode':'live','sms_gate_username':'test','sms_gate_password':'synthetic'})
    monkeypatch.setattr(delivery,'get_settings',lambda:settings)
    requests=[]
    def provider(request):
        requests.append(request)
        return httpx.Response(202,json={'id':'provider-test-id','state':'Delivered'})
    result=delivery.send_sms('+966501234567','Test RFQ','test-key',transport=httpx.MockTransport(provider))
    assert result['state']=='Pending' and result['http_status']==202
    import json
    body=json.loads(requests[0].content)
    assert body['withDeliveryReport'] and body['phoneNumbers']==['+966501234567']


def test_restrictions_migration_unique_destination_and_downgrade():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('delivery_migration',Path(__file__).parents[1]/'alembic/versions/za07d326b458_delivery_restrictions.py')
    revision=importlib.util.module_from_spec(spec);spec.loader.exec_module(revision)
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
            assert inspect(connection).get_unique_constraints('notification_delivery_restrictions')[0]['column_names']==['channel','destination']
            revision.downgrade()
            assert not inspect(connection).has_table('notification_delivery_restrictions')
    engine.dispose()


def test_end_to_end_local_tls_smtp_and_http_gateway(flow,monkeypatch,tmp_path):
    """Real local sockets and TLS, synthetic provider responses; no external sends."""
    import base64
    import ipaddress
    import json
    import socketserver
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from app.modules.notifications import delivery
    _,sessions,ids,publish,process,calls=flow
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC)-timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC)+timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
        .sign(key,hashes.SHA256()))
    cert_path=tmp_path/'smtp.crt';key_path=tmp_path/'smtp.key'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    smtp_messages=[];http_requests=[]
    class SMTPHandler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 localhost test SMTP\r\n')
            while line:=self.rfile.readline():
                command=line.decode().strip().upper()
                if command.startswith('EHLO'):
                    self.wfile.write(b'250-localhost\r\n250 AUTH PLAIN\r\n')
                elif command.startswith('AUTH PLAIN'):
                    assert base64.b64decode(line.split()[2])==b'\0sender@example.com\0synthetic'
                    self.wfile.write(b'235 authenticated\r\n')
                elif command=='DATA':
                    self.wfile.write(b'354 enter message\r\n');body=[]
                    while (part:=self.rfile.readline())!=b'.\r\n':
                        if not part:return
                        body.append(part)
                    smtp_messages.append(b''.join(body));self.wfile.write(b'250 queued-local-test\r\n')
                elif command=='QUIT':self.wfile.write(b'221 bye\r\n');return
                else:self.wfile.write(b'250 ok\r\n')
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert_path,key_path)
    class SMTPServer(socketserver.ThreadingTCPServer):
        daemon_threads=True
        def get_request(self):
            sock,address=super().get_request()
            return tls.wrap_socket(sock,server_side=True),address
    class Gateway(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def answer(self,status,body):
            self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            http_requests.append(('POST',body))
            self.answer(202,{'id':body['id'],'state':'Delivered'})
        def do_GET(self):
            http_requests.append(('GET',self.path))
            self.answer(200,{'id':self.path.rsplit('/',1)[1],'state':'Delivered'})
    smtp=SMTPServer(('127.0.0.1',0),SMTPHandler);gateway=ThreadingHTTPServer(('127.0.0.1',0),Gateway)
    threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in (smtp,gateway)]
    for thread in threads:thread.start()
    settings=get_settings().model_copy(update={'app_env':'test','mock_notifications':False,
        'smtp_host':'127.0.0.1','smtp_port':smtp.server_address[1],'smtp_security':'SSL/TLS',
        'smtp_username':'sender@example.com','smtp_password':'synthetic','smtp_from_email':'sender@example.com',
        'smtp_reply_to':'reply@example.com','sms_gateway_enabled':True,'sms_gateway_mode':'live',
        'sms_gate_username':'synthetic','sms_gate_password':'synthetic',
        'sms_gate_api_url':f'http://127.0.0.1:{gateway.server_address[1]}/3rdparty/v1/messages'})
    trust=ssl.create_default_context(cadata=cert_path.read_text())
    monkeypatch.setattr(delivery,'create_default_context',lambda:trust)
    monkeypatch.setattr(delivery,'get_settings',lambda:settings)
    monkeypatch.setattr(worker,'get_settings',lambda:settings)
    monkeypatch.setattr(worker,'send_email',delivery.send_email)
    monkeypatch.setattr(worker,'send_sms',delivery.send_sms)
    monkeypatch.setattr(worker,'get_sms_status',delivery.get_sms_status)
    try:
        publish();process()
        with sessions() as db:
            jobs=deliveries(db)
            assert jobs['email'].delivery_status=='accepted' and jobs['email'].payload['smtp_accepted']
            assert jobs['sms'].delivery_status=='Pending'
            worker.process_batch(db,event_ids=[jobs['sms'].id],now=datetime.now(UTC)+timedelta(minutes=1))
            assert db.get(Outbox,jobs['sms'].id).delivery_status=='Delivered'
        assert len(smtp_messages)==1 and b'multipart/alternative' in smtp_messages[0]
        assert [r[0] for r in http_requests]==['POST','GET']
        assert http_requests[0][1]['phoneNumbers']==['+966501234567']
    finally:
        for server in (smtp,gateway):server.shutdown();server.server_close()
        for thread in threads:thread.join(timeout=2)
