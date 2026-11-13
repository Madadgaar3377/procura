"""Recovery regression tests. Optional PostgreSQL uses a disposable UUID database.

APP_ENV=test DATABASE_URL=sqlite:///:memory: python -m pytest -q
Set RESET_TEST_DATABASE_URL to a loopback-only disposable PostgreSQL database
to run the same tests, including concurrency, against real row locks/migrations.
No configured application database or delivery provider is used by this fixture.
"""
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.models import AuditEvent, EmailOTP, Notification, Outbox, PhoneOTP, RefreshSession, User
from app.modules.auth import router as auth
from app.modules.notifications.crypto import decrypt_delivery_secret
from app.security import check_password, credential_stamp, password_hash


def _process_reset(url,token,barrier,results):
    """Fresh interpreter and SQLAlchemy pool, sharing only PostgreSQL state."""
    from fastapi import BackgroundTasks, HTTPException, Request
    engine=create_engine(url,hide_parameters=True)
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    auth.enforce_rate_limit=lambda *args,**kwargs:None
    auth.schedule_auth_deliveries=lambda *args:None
    request=Request({'type':'http','method':'POST','path':'/auth/password/reset','headers':[],'client':('127.0.0.1',1234)})
    request.state.correlation_id='multiprocess-reset-test'
    with sessions() as db:
        try:
            barrier.wait(timeout=30)
            auth.password_reset(auth.PasswordReset(reset_token=token,new_password='Process-password-456'),request,BackgroundTasks(),db)
            results.put(200)
        except HTTPException as exc:
            db.rollback();results.put(exc.status_code)
        except Exception:
            db.rollback();results.put(500)
    engine.dispose()


@pytest.fixture(scope='module')
def recovery_sessions(tmp_path_factory):
    url=os.environ.get('RESET_TEST_DATABASE_URL')
    if url:
        parsed=make_url(url)
        assert parsed.get_backend_name()=='postgresql' and parsed.host in {'127.0.0.1','localhost','::1'}, 'Disposable loopback PostgreSQL only'
        database='reset_test_'+uuid4().hex
        admin=create_engine(url, hide_parameters=True,isolation_level='AUTOCOMMIT')
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database}"'))
        engine=create_engine(parsed.set(database=database), hide_parameters=True, connect_args={'options':'-clock_timeout=5000 -cstatement_timeout=15000'})
        with engine.connect() as conn:
            config=Config(str(Path(__file__).parents[1]/'alembic.ini'))
            config.set_main_option('script_location',str(Path(__file__).parents[1]/'alembic'))
            config.attributes['connection']=conn
            command.upgrade(config,'heads')
    else:
        engine=create_engine('sqlite:///'+str(tmp_path_factory.mktemp('reset')/'fixture.db'),connect_args={'check_same_thread':False},hide_parameters=True)
        # Only disposable unit-test tables, never application schema management.
        for model in (User,EmailOTP,PhoneOTP,RefreshSession,Outbox,Notification,AuditEvent):
            model.__table__.create(engine)
    yield sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    engine.dispose()
    if url:
        # This exact database was generated and created above, never user supplied.
        assert database.startswith('reset_test_') and len(database)==43
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{database}"'))
        admin.dispose()


@pytest.fixture
def recovery(recovery_sessions,monkeypatch):
    # Keep external dispatch disabled; exercise real encrypted transactional outbox.
    monkeypatch.setattr(auth,'schedule_auth_deliveries',lambda *args:None)
    monkeypatch.setattr(auth,'enforce_rate_limit',lambda *args,**kwargs:None)
    app=FastAPI()
    app.include_router(auth.router)

    @app.middleware('http')
    async def correlation(request,call_next):
        request.state.correlation_id='reset-security-test'
        return await call_next(request)

    def session():
        with recovery_sessions() as db:
            try:
                yield db
            except Exception:
                db.rollback()
                raise
    app.dependency_overrides[get_db]=session
    identity=uuid4().hex
    phone='+96650'+str(int(identity[:12],16)%10000000).zfill(7)
    with recovery_sessions() as db:
        user=User(username='reset_'+identity,email=identity+'@example.test',
                  phone=phone,phone_e164=phone,email_verified=True,phone_verified=True,
                  password_hash=password_hash('Old-password-123'))
        db.add(user);db.commit()
        uid,email=user.id,user.email
    with TestClient(app,raise_server_exceptions=False) as client:
        yield client,recovery_sessions,uid,email,phone


def issue(recovery,identifier=None):
    client,sessions,uid,email,_=recovery
    response=client.post('/auth/password/forgot',json={'identifier':identifier or email})
    assert response.status_code==202,response.text
    with sessions() as db:
        otp=db.scalar(select(EmailOTP).where(EmailOTP.user_id==uid,EmailOTP.purpose=='password_reset',EmailOTP.used==False))
        delivery=db.scalar(select(Outbox).where(Outbox.dedupe_key==f'password-reset:{otp.id}:email'))
        if not delivery:
            delivery=db.scalar(select(Outbox).where(Outbox.dedupe_key==f'password-reset:{otp.id}:sms'))
        secret=json.loads(decrypt_delivery_secret(delivery.secret_ciphertext))
        assert secret['code'] not in json.dumps(delivery.payload)
        assert secret['link_token'] not in json.dumps(delivery.payload)
        assert otp.code_hash!=secret['code']
        return otp.id,secret['code'],secret['link_token']


def reset(recovery,code=None,token=None,identifier=None,password='New-password-456'):
    client,_,_,email,_=recovery
    payload={'new_password':password}
    if token:payload['reset_token']=token
    else:payload.update(identifier=identifier or email,code=code)
    return client.post('/auth/password/reset',json=payload)


def assert_password(recovery,password):
    _,sessions,uid,_,_=recovery
    with sessions() as db:
        assert check_password(db.get(User,uid).password_hash,password)


@pytest.mark.parametrize('kind',['otp','link','temporary'])
def test_password_reset_is_single_use_across_fresh_sessions(recovery,kind):
    client,sessions,uid,email,_=recovery
    oid,code,link=issue(recovery)
    token=link if kind=='link' else None
    if kind=='temporary':
        verified=client.post('/auth/password/verify',json={'identifier':email,'code':code})
        assert verified.status_code==200
        token=verified.json()['reset_token']
        assert client.post('/auth/password/verify',json={'identifier':email,'code':code}).status_code==400
    result=reset(recovery,code=code,token=token)
    assert result.status_code==200,result.text
    for _ in range(3):
        assert reset(recovery,code=code,token=token).status_code==400
    assert reset(recovery,code=code).status_code==400
    assert reset(recovery,token=link).status_code==400
    assert_password(recovery,'New-password-456')
    with sessions() as db:
        consumed=db.scalar(select(EmailOTP).where(EmailOTP.user_id==uid,EmailOTP.consumed_at.is_not(None)))
        assert consumed
        assert all(row.used for row in db.scalars(select(EmailOTP).where(EmailOTP.user_id==uid)))
        notices=list(db.scalars(select(Outbox).where(Outbox.dedupe_key.like(f'password-changed:{consumed.id}:%'))))
        assert len(notices)==2
        assert all('password' not in json.dumps(n.payload.get('context',{})).lower() for n in notices)


@pytest.mark.parametrize('kind',['otp','link','temporary'])
def test_expired_reset_credentials_fail(recovery,kind):
    client,sessions,uid,email,_=recovery
    _,code,link=issue(recovery)
    token=link if kind=='link' else None
    if kind=='temporary':
        token=client.post('/auth/password/verify',json={'identifier':email,'code':code}).json()['reset_token']
    with sessions() as db:
        db.execute(update(EmailOTP).where(EmailOTP.user_id==uid).values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        db.commit()
    assert reset(recovery,code=code,token=token).status_code==400
    assert_password(recovery,'Old-password-123')


def test_incorrect_otp_and_durable_attempt_limit(recovery):
    _,sessions,_,_,_=recovery
    oid,code,_=issue(recovery)
    wrong='000000' if code!='000000' else '111111'
    for _ in range(5):
        assert reset(recovery,code=wrong).status_code==400
    assert reset(recovery,code=code).status_code==400
    with sessions() as db:
        assert db.get(EmailOTP,oid).attempts==5
    assert_password(recovery,'Old-password-123')


@pytest.mark.parametrize('first,second',[('otp','link'),('link','otp'),('temporary','link')])
def test_new_request_supersedes_all_previous_channels(recovery,first,second):
    client,sessions,uid,email,phone=recovery
    _,old_code,old_link=issue(recovery,email)
    old_token=old_link if first=='link' else None
    if first=='temporary':
        old_token=client.post('/auth/password/verify',json={'identifier':email,'code':old_code}).json()['reset_token']
    _,new_code,new_link=issue(recovery,phone)
    assert reset(recovery,code=old_code,token=old_token).status_code==400
    assert reset(recovery,code=new_code,token=new_link if second=='link' else None,identifier=phone).status_code==200
    assert reset(recovery,code=old_code,token=old_token).status_code==400
    with sessions() as db:
        assert not db.scalar(select(EmailOTP.id).where(EmailOTP.user_id==uid,EmailOTP.used==False))


def test_verification_and_access_tokens_cannot_authorize_reset(recovery):
    client,sessions,uid,email,phone=recovery
    with sessions() as db:
        db.add(EmailOTP(email=email,user_id=uid,purpose='verification',code_hash=auth.otp_digest('verification',email,'123456'),expires_at=datetime.now(UTC)+timedelta(minutes=5)))
        db.add(PhoneOTP(phone=phone,purpose='registration',code_hash=auth.otp_digest('registration',phone,'123456'),expires_at=datetime.now(UTC)+timedelta(minutes=5)))
        db.commit()
    assert reset(recovery,code='123456').status_code==400
    assert client.post('/auth/password/verify',json={'identifier':email,'code':'123456'}).status_code==400
    login=client.post('/auth/login',json={'identifier':email,'password':'Old-password-123'})
    assert login.status_code==200,login.text
    assert reset(recovery,token=login.json()['access_token']).status_code==400
    _,code,_=issue(recovery)
    assert client.post('/auth/otp/verify',json={'phone':phone,'code':code}).status_code==400


def test_reset_revokes_refresh_and_access_sessions_and_preserves_new_login(recovery):
    client,_,_,email,_=recovery
    login=client.post('/auth/login',json={'identifier':email,'password':'Old-password-123'})
    assert login.status_code==200,login.text
    pair=login.json()
    rotated=client.post('/auth/refresh',json={'refresh_token':pair['refresh_token']})
    assert rotated.status_code==200
    _,code,_=issue(recovery)
    assert reset(recovery,code=code).status_code==200
    for value in (pair,rotated.json()):
        assert client.post('/auth/refresh',json={'refresh_token':value['refresh_token']}).status_code==401
        assert client.get('/auth/me',headers={'Authorization':'Bearer '+value['access_token']}).status_code==401
    assert client.post('/auth/login',json={'identifier':email,'password':'Old-password-123'}).status_code==401
    assert client.post('/auth/login',json={'identifier':email,'password':'New-password-456'}).status_code==200


@pytest.mark.parametrize('kind',['otp','link','temporary'])
def test_database_failure_rolls_back_password_consumption_revocations_and_notices(recovery,monkeypatch,kind):
    client,sessions,uid,email,_=recovery
    oid,code,link=issue(recovery)
    token=link if kind=='link' else None
    if kind=='temporary':
        token=client.post('/auth/password/verify',json={'identifier':email,'code':code}).json()['reset_token']
    pair=client.post('/auth/login',json={'identifier':email,'password':'Old-password-123'}).json()
    def fail_commit(db):
        # Fail after SQL writes have been flushed, not merely before mutation.
        db.flush()
        raise RuntimeError('injected commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(sessions.class_,'commit',fail_commit)
        assert reset(recovery,code=code,token=token).status_code==500
    assert_password(recovery,'Old-password-123')
    with sessions() as db:
        otp=db.get(EmailOTP,oid)
        assert not otp.used and not otp.consumed_at and not otp.revoked_at
        assert not db.scalar(select(Outbox.id).where(Outbox.dedupe_key.like(f'password-changed:{oid}:%')))
        assert not db.scalar(select(RefreshSession.revoked).where(RefreshSession.user_id==uid))
    assert client.get('/auth/me',headers={'Authorization':'Bearer '+pair['access_token']}).status_code==200
    assert reset(recovery,code=code,token=token).status_code==200


@pytest.mark.parametrize('kind',['otp','link','temporary','mixed'])
def test_concurrent_reset_requests_exactly_one_succeeds(recovery,kind):
    client,sessions,_,email,_=recovery
    if sessions.kw['bind'].dialect.name!='postgresql':
        pytest.skip('Requires RESET_TEST_DATABASE_URL: SQLite does not implement PostgreSQL row locks')
    _,code,link=issue(recovery)
    token=link if kind in {'link','mixed'} else None
    if kind=='temporary':
        token=client.post('/auth/password/verify',json={'identifier':email,'code':code}).json()['reset_token']
    barrier=Barrier(2)
    def attempt(index):
        barrier.wait(timeout=10)
        return reset(recovery,code=code,token=None if kind=='mixed' and index==0 else token,password=f'Concurrent-password-{index}').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,range(2)))
    assert sorted(results)==[200,400],results
    assert reset(recovery,code=code,token=token).status_code==400


def test_link_validation_does_not_mint_or_consume_authorization(recovery):
    client,_,_,_,_=recovery
    _,_,link=issue(recovery)
    for _ in range(2):
        checked=client.post('/auth/password/verify',json={'reset_token':link})
        assert checked.status_code==200
        assert checked.json()['reset_token']==link
    assert reset(recovery,token=link).status_code==200
    used=client.post('/auth/password/verify',json={'reset_token':link})
    assert used.status_code==400 and used.json()['detail']==auth.RESET_ERROR


def test_unbound_legacy_reset_rows_are_rejected(recovery):
    _,sessions,_,email,_=recovery
    with sessions() as db:
        db.add(EmailOTP(email=email,purpose='password_reset',code_hash=auth.otp_digest('password_reset',email,'123456'),expires_at=datetime.now(UTC)+timedelta(minutes=5)))
        db.commit()
    assert reset(recovery,code='123456').status_code==400


def test_new_requests_never_reissue_a_historical_otp(recovery,monkeypatch):
    _,_,_,_,_=recovery
    _,old_code,_=issue(recovery)
    new_number=(int(old_code)+1)%1000000
    choices=iter([int(old_code),new_number])
    monkeypatch.setattr(auth.secrets,'randbelow',lambda maximum:next(choices))
    _,new_code,_=issue(recovery)
    assert new_code==f'{new_number:06d}'
    assert reset(recovery,code=old_code).status_code==400
    assert reset(recovery,code=new_code).status_code==200


def test_recovery_destination_change_does_not_leave_usable_old_links(recovery):
    _,sessions,uid,_,_=recovery
    _,_,link=issue(recovery)
    with sessions() as db:
        db.get(User,uid).email_verified=False
        db.commit()
    assert reset(recovery,token=link).status_code==400
    issue(recovery,recovery[4])
    with sessions() as db:
        db.get(User,uid).email_verified=True
        db.commit()
    assert reset(recovery,token=link).status_code==400


@pytest.mark.parametrize('kind',['otp','link','temporary'])
def test_preloaded_orm_state_cannot_resurrect_consumed_credentials(recovery,kind):
    from fastapi import BackgroundTasks, HTTPException, Request
    client,sessions,uid,email,_=recovery
    oid,code,link=issue(recovery)
    token=link if kind=='link' else None
    if kind=='temporary':
        token=client.post('/auth/password/verify',json={'identifier':email,'code':code}).json()['reset_token']
    with sessions() as stale:
        old_user=stale.get(User,uid)
        old_otp=stale.get(EmailOTP,oid)
        if kind=='link':
            old_link=stale.scalar(select(EmailOTP).where(EmailOTP.code_hash==auth.digest(link)))
        assert reset(recovery,code=code,token=token).status_code==200
        assert not old_otp.used
        request=Request({'type':'http','method':'POST','path':'/auth/password/reset','headers':[],'client':('127.0.0.1',1234)})
        request.state.correlation_id='stale-reset-test'
        payload=auth.PasswordReset(reset_token=token,new_password='Replay-password-789') if token else auth.PasswordReset(identifier=email,code=code,new_password='Replay-password-789')
        with pytest.raises(HTTPException) as failure:
            auth.password_reset(payload,request,BackgroundTasks(),stale)
        assert failure.value.status_code==400
        stale.rollback()
    assert_password(recovery,'New-password-456')


@pytest.mark.parametrize('worker_name',['notification_worker','worker'])
def test_reset_delivery_decodes_envelope_and_suppresses_revoked_requests(recovery,monkeypatch,worker_name):
    from app import notification_worker, worker
    _,sessions,_,_,_=recovery
    oid,code,link=issue(recovery)
    rendered=[]
    def render(template,context,secret):
        rendered.append((template,context,secret))
        return 'Subject','Body','<p>Body</p>'
    monkeypatch.setattr(notification_worker,'render_message',render)
    monkeypatch.setattr(notification_worker,'send_email',lambda *args:'test-message')
    monkeypatch.setattr(notification_worker,'send_sms',lambda *args,**kwargs:{'id':'test-message','state':'Delivered'})
    deliver=notification_worker.deliver if worker_name=='notification_worker' else worker._deliver
    with sessions() as db:
        for channel in ('email','sms'):
            delivery=db.scalar(select(Outbox).where(Outbox.dedupe_key==f'password-reset:{oid}:{channel}'))
            deliver(db,delivery,datetime.now(UTC))
            db.commit()
            assert delivery.secret_ciphertext is None
    assert len(rendered)==2
    assert all(item[2]==code for item in rendered)
    assert rendered[0][1]['url'].endswith('#token='+link)
    revoked_oid,_,_=issue(recovery)
    issue(recovery)
    with sessions() as db:
        delivery=db.scalar(select(Outbox).where(Outbox.dedupe_key==f'password-reset:{revoked_oid}:email'))
        deliver(db,delivery,datetime.now(UTC))
        assert delivery.status=='skipped' and delivery.secret_ciphertext is None
    assert len(rendered)==2


@pytest.mark.parametrize('endpoint',['forgot','verify','reset'])
def test_recovery_ip_rate_limits(recovery,monkeypatch,endpoint):
    from app.rate_limit import enforce_rate_limit
    client,_,_,_,_=recovery
    monkeypatch.setattr(auth,'enforce_rate_limit',enforce_rate_limit)
    # Each unknown account is unique; only the IP quota can block these requests.
    statuses=[]
    address='192.0.2.'+str({'forgot':10,'verify':11,'reset':12}[endpoint])
    with TestClient(client.app,client=(address,1234),raise_server_exceptions=False) as limited:
        for index in range(21):
            payload={'identifier':f'{uuid4().hex}@example.test'}
            if endpoint!='forgot':payload['code']='123456'
            if endpoint=='reset':payload['new_password']='Unused-password'
            statuses.append(limited.post('/auth/password/'+endpoint,json=payload).status_code)
    allowed=10 if endpoint=='forgot' else 20
    assert all(status!=429 for status in statuses[:allowed]),statuses
    assert statuses[allowed]==429


def test_reset_tokens_have_shared_account_quota_across_ips(recovery,monkeypatch):
    from app.rate_limit import enforce_rate_limit
    client,_,_,_,_=recovery
    _,_,link=issue(recovery)
    monkeypatch.setattr(auth,'enforce_rate_limit',enforce_rate_limit)
    for index in range(11):
        with TestClient(client.app,client=(f'198.51.100.{index+1}',1234),raise_server_exceptions=False) as limited:
            result=limited.post('/auth/password/verify',json={'reset_token':link})
            assert result.status_code==(200 if index<10 else 429)


def test_password_change_invalidates_outstanding_reset_credentials(recovery):
    client,_,_,email,_=recovery
    _,code,link=issue(recovery)
    login=client.post('/auth/login',json={'identifier':email,'password':'Old-password-123'}).json()
    response=client.post('/auth/password/change',headers={'Authorization':'Bearer '+login['access_token']},json={
        'current_password':'Old-password-123','new_password':'Changed-password-456','confirm_password':'Changed-password-456'})
    assert response.status_code==200,response.text
    assert reset(recovery,code=code).status_code==400
    assert reset(recovery,token=link).status_code==400
    assert_password(recovery,'Changed-password-456')


def test_registration_contact_verification_login_sessions_and_logout_still_work(recovery):
    client,sessions,_,_,_=recovery
    unique=uuid4().hex
    email=unique+'@example.test'
    phone='+96654'+str(int(unique[:12],16)%10000000).zfill(7)
    registered=client.post('/auth/register',json={'username':'signup_'+unique,'email':email,'phone':phone,'password':'Signup-password-123'})
    assert registered.status_code==201,registered.text
    uid=registered.json()['id']
    assert client.post('/auth/otp/request',params={'phone':phone}).status_code==200
    with sessions() as db:
        challenge=db.scalar(select(PhoneOTP).where(PhoneOTP.phone==phone))
        event=db.scalar(select(Outbox).where(Outbox.dedupe_key==f'phone-otp:{challenge.id}'))
        code=decrypt_delivery_secret(event.secret_ciphertext)
    assert client.post('/auth/otp/verify',json={'phone':phone,'code':code}).status_code==200
    assert client.post('/auth/otp/verify',json={'phone':phone,'code':code}).status_code==400
    login=client.post('/auth/login',json={'identifier':phone,'password':'Signup-password-123'})
    assert login.status_code==200,login.text
    pair=login.json()
    headers={'Authorization':'Bearer '+pair['access_token']}
    assert client.post('/auth/email/verification/request',headers=headers).status_code==202
    with sessions() as db:
        challenge=db.scalar(select(EmailOTP).where(EmailOTP.email==email,EmailOTP.purpose=='verification',EmailOTP.used==False))
        event=db.scalar(select(Outbox).where(Outbox.dedupe_key==f'email-otp:{challenge.id}'))
        code=decrypt_delivery_secret(event.secret_ciphertext)
    assert client.post('/auth/email/verification/confirm',headers=headers,json={'code':code}).status_code==200
    assert client.get('/auth/me',headers=headers).json()['email_verified'] is True
    assert client.get('/auth/sessions',headers=headers).status_code==200
    assert client.post('/auth/logout',headers=headers,json={'refresh_token':pair['refresh_token']}).status_code==200
    assert client.get('/auth/me',headers=headers).status_code==401


def test_jwt_expiry_and_user_binding_are_checked_in_addition_to_signature(recovery):
    import jwt
    from app.config import get_settings
    client,sessions,uid,email,phone=recovery
    oid,code,_=issue(recovery)
    token=client.post('/auth/password/verify',json={'identifier':email,'code':code}).json()['reset_token']
    claims=jwt.decode(token,get_settings().jwt_secret,algorithms=['HS256'])
    expired=jwt.encode({**claims,'exp':datetime.now(UTC)-timedelta(seconds=1)},get_settings().jwt_secret,algorithm='HS256')
    assert reset(recovery,token=expired).status_code==400
    with sessions() as db:
        other=User(username='other_'+uuid4().hex,email=uuid4().hex+'@example.test',phone='+966559876543',phone_e164='+966559876543',phone_verified=True,password_hash=password_hash('Other-password'))
        db.add(other);db.commit()
        mismatched=jwt.encode({**claims,'sub':other.id,'stamp':credential_stamp(other)},get_settings().jwt_secret,algorithm='HS256')
    assert reset(recovery,token=mismatched).status_code==400
    assert reset(recovery,token=token).status_code==200


def test_concurrent_otp_verification_mints_only_one_authorization(recovery):
    client,sessions,_,email,_=recovery
    if sessions.kw['bind'].dialect.name!='postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    _,code,_=issue(recovery)
    barrier=Barrier(2)
    def verify(_):
        barrier.wait(timeout=10)
        return client.post('/auth/password/verify',json={'identifier':email,'code':code})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(verify,range(2)))
    assert sorted(result.status_code for result in results)==[200,400]
    token=next(result.json()['reset_token'] for result in results if result.status_code==200)
    assert reset(recovery,token=token).status_code==200
    assert reset(recovery,token=token).status_code==400


def test_independent_processes_and_restarts_cannot_replay_reset(recovery):
    import multiprocessing
    _,sessions,_,_,_=recovery
    if sessions.kw['bind'].dialect.name!='postgresql':
        pytest.skip('Requires PostgreSQL shared across processes')
    _,code,link=issue(recovery)
    context=multiprocessing.get_context('spawn')
    results=context.Queue()
    barrier=context.Barrier(2)
    url=sessions.kw['bind'].url.render_as_string(hide_password=False)
    processes=[context.Process(target=_process_reset,args=(url,link,barrier,results)) for _ in range(2)]
    try:
        for process in processes:process.start()
        statuses=[results.get(timeout=45) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode==0
        assert sorted(statuses)==[200,400]
        # A third fresh interpreter models a worker restart after consumption.
        restarted=context.Process(target=_process_reset,args=(url,link,context.Barrier(1),results))
        processes.append(restarted)
        restarted.start()
        assert results.get(timeout=45)==400
        restarted.join(timeout=10)
        assert restarted.exitcode==0
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join(timeout=5)
        results.close()
    assert_password(recovery,'Process-password-456')
    assert reset(recovery,code=code).status_code==400


def test_postgresql_migration_revokes_legacy_credentials_preserves_accounts_and_can_rollback(recovery_sessions):
    engine=recovery_sessions.kw['bind']
    if engine.dialect.name!='postgresql':
        pytest.skip('Requires PostgreSQL migration and foreign-key behavior')
    spec=importlib.util.spec_from_file_location('pg_recovery_migration',Path(__file__).parents[1]/'alembic/versions/zb18e437c569_single_use_recovery.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with engine.connect() as conn:
        transaction=conn.begin()
        try:
            original_users=conn.execute(text('SELECT count(*) FROM users')).scalar()
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                for purpose in (*auth.RESET_PURPOSES,'verification'):
                    conn.execute(text('INSERT INTO email_otps (id,created_at,email,purpose,code_hash,expires_at,attempts,used) '
                                      'VALUES (:id,CURRENT_TIMESTAMP,:email,:purpose,:hash,CURRENT_TIMESTAMP,0,false)'),
                                 {'id':uuid4().hex,'email':'migration@example.test','purpose':purpose,'hash':'0'*64})
                migration.upgrade()
                rows=conn.execute(text("SELECT purpose,used,revoked_at FROM email_otps WHERE email='migration@example.test'")).all()
                assert all(row.used and row.revoked_at for row in rows if row.purpose in auth.RESET_PURPOSES)
                assert not next(row.used for row in rows if row.purpose=='verification')
                assert conn.execute(text('SELECT count(*) FROM users')).scalar()==original_users
                migration.downgrade()
                assert conn.execute(text("SELECT count(*) FROM email_otps WHERE email='migration@example.test' AND used=true")).scalar()==3
        finally:
            transaction.rollback()


def test_migration_revokes_only_recovery_credentials_and_keeps_revocation_on_downgrade():
    spec=importlib.util.spec_from_file_location('recovery_migration',Path(__file__).parents[1]/'alembic/versions/zb18e437c569_single_use_recovery.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE email_otps (id TEXT PRIMARY KEY, purpose TEXT, used BOOLEAN, code_hash TEXT)'))
        for purpose in (*auth.RESET_PURPOSES,'verification'):
            conn.execute(text('INSERT INTO email_otps (id,purpose,used) VALUES (:p,:p,false)'),{'p':purpose})
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.execute(text("SELECT count(*) FROM email_otps WHERE used=true AND revoked_at IS NOT NULL")).scalar()==3
            assert conn.execute(text("SELECT used FROM email_otps WHERE purpose='verification'")).scalar()==0
            migration.downgrade()
            assert conn.execute(text('SELECT count(*) FROM email_otps WHERE used=true')).scalar()==3
    engine.dispose()
