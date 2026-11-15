"""Tests for password/forgot validation, trusted-proxy IP extraction, and rate limiting."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import get_db
from app.models import AuditEvent, EmailOTP, Notification, Outbox, PhoneOTP, RefreshSession, User
from app.modules.auth import router as auth
from app.rate_limit import _local, _local_lock
from app.security import password_hash


@pytest.fixture
def recovery_app(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(auth, 'schedule_auth_deliveries', lambda *args: None)
    with _local_lock:
        _local.clear()

    engine = create_engine(
        'sqlite:///' + str(tmp_path_factory.mktemp('forgot') / 'fixture.db'),
        connect_args={'check_same_thread': False},
        hide_parameters=True,
    )
    for model in (User, EmailOTP, PhoneOTP, RefreshSession, Outbox, Notification, AuditEvent):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    app = FastAPI()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc: RequestValidationError):
        errors = [{'loc': e['loc'], 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]
        return JSONResponse(status_code=422, content={'detail': errors})

    @app.middleware('http')
    async def correlation(request, call_next):
        request.state.correlation_id = request.headers.get('X-Request-ID') or 'test-req-id'
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.correlation_id
        return response

    def session():
        with sessions() as db:
            try:
                yield db
            except Exception:
                db.rollback()
                raise

    app.include_router(auth.router)
    app.dependency_overrides[get_db] = session

    uid = uuid4().hex
    email = f'user_{uid}@example.test'
    phone = '+96650' + str(int(uid[:12], 16) % 10000000).zfill(7)
    with sessions() as db:
        db.add(User(
            username=f'u_{uid}',
            email=email,
            phone=phone,
            phone_e164=phone,
            email_verified=True,
            phone_verified=True,
            password_hash=password_hash('Secret-pass-123'),
        ))
        db.commit()

    yield app, email, phone
    engine.dispose()


def test_valid_email_and_saudi_phone_and_nonexistent_return_generic_202(recovery_app):
    app, email, phone = recovery_app
    with TestClient(app, client=('198.51.100.1', 1234)) as client:
        r_email = client.post('/auth/password/forgot', json={'identifier': f'  {email.upper()}  '})
        assert r_email.status_code == 202
        assert r_email.json() == {'message': 'If an eligible account exists, a password recovery code will be sent'}

    local_phone = '0' + phone[4:]
    with TestClient(app, client=('198.51.100.2', 1234)) as client:
        r_phone = client.post('/auth/password/forgot', json={'identifier': local_phone, 'phone_country': None})
        assert r_phone.status_code == 202
        assert r_phone.json() == r_email.json()

    arabic_digits_phone = local_phone.translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))
    with TestClient(app, client=('198.51.100.3', 1234)) as client:
        r_ar = client.post('/auth/password/forgot', json={'identifier': arabic_digits_phone, 'phone_country': 'SA'})
        assert r_ar.status_code == 429  # Same normalized recipient cooldown across IPs!
        assert int(r_ar.headers['Retry-After']) >= 1

    with TestClient(app, client=('198.51.100.4', 1234)) as client:
        r_unknown = client.post('/auth/password/forgot', json={'identifier': 'nonexistent@example.test'})
        assert r_unknown.status_code == 202
        assert r_unknown.json() == r_email.json()


def test_malformed_requests_return_consistent_422_and_do_not_consume_quota(recovery_app):
    app, _, _ = recovery_app
    with TestClient(app, client=('198.51.100.20', 1234)) as client:
        for bad in (
            {'identifier': '   '},
            {'identifier': 'not-a-phone'},
            {'identifier': '+14155552671'},
            {'identifier': 'bad@'},
            {'identifier': 'a@b.test', 'email': 'a@b.test'},
            {'identifier': '0501234567', 'phone_country': 'XX'},
        ):
            res = client.post('/auth/password/forgot', json=bad, headers={'X-Request-ID': 'req-422-check'})
            assert res.status_code == 422
            assert isinstance(res.json()['detail'], list)
            assert res.headers['X-Request-ID'] == 'req-422-check'

        # After 6 malformed 422 requests, a valid request from the same IP still succeeds!
        ok = client.post('/auth/password/forgot', json={'identifier': 'fresh@example.test'})
        assert ok.status_code == 202


def test_independent_users_behind_trusted_nginx_and_cloudflare_proxy(recovery_app, monkeypatch):
    app, _, _ = recovery_app
    monkeypatch.setenv('TRUST_CLOUDFLARE_PROXY', 'true')
    get_settings.cache_clear()
    try:
        with TestClient(app, client=('127.0.0.1', 5000)) as client:
            # Exhaust IP quota for user A (203.0.113.10) behind Cloudflare (172.68.10.2) + Nginx (127.0.0.1)
            for idx in range(10):
                res = client.post(
                    '/auth/password/forgot',
                    json={'identifier': f'a{idx}@example.test'},
                    headers={'X-Forwarded-For': '203.0.113.10, 172.68.10.2'},
                )
                assert res.status_code == 202

            blocked = client.post(
                '/auth/password/forgot',
                json={'identifier': 'a11@example.test'},
                headers={'X-Forwarded-For': '203.0.113.10, 172.68.10.2'},
            )
            assert blocked.status_code == 429
            assert 1 <= int(blocked.headers['Retry-After']) <= 900

            # Independent user B (203.0.113.11) behind the SAME Cloudflare & Nginx proxy is NOT blocked!
            other = client.post(
                '/auth/password/forgot',
                json={'identifier': 'b1@example.test'},
                headers={'X-Forwarded-For': '203.0.113.11, 172.68.10.2'},
            )
            assert other.status_code == 202
    finally:
        get_settings.cache_clear()


def test_untrusted_forwarded_headers_are_ignored(recovery_app, monkeypatch):
    app, _, _ = recovery_app
    monkeypatch.setenv('TRUST_CLOUDFLARE_PROXY', 'true')
    get_settings.cache_clear()
    try:
        # Direct untrusted peer (198.51.100.55) trying to spoof X-Forwarded-For / CF-Connecting-IP per request
        with TestClient(app, client=('198.51.100.55', 5000)) as client:
            for idx in range(10):
                res = client.post(
                    '/auth/password/forgot',
                    json={'identifier': f'spoof{idx}@example.test'},
                    headers={
                        'X-Forwarded-For': f'203.0.113.{idx + 1}',
                        'CF-Connecting-IP': f'203.0.113.{idx + 1}',
                        'X-Real-IP': f'203.0.113.{idx + 1}',
                    },
                )
                assert res.status_code == 202
            blocked = client.post(
                '/auth/password/forgot',
                json={'identifier': 'spoof11@example.test'},
                headers={
                    'X-Forwarded-For': '203.0.113.99',
                    'CF-Connecting-IP': '203.0.113.99',
                },
            )
            assert blocked.status_code == 429

        # Direct connection to Nginx bypassing Cloudflare: Nginx appends real peer (198.51.100.66),
        # so attacker's spoofed CF-Connecting-IP / earlier XFF hop MUST be ignored.
        with TestClient(app, client=('127.0.0.1', 5000)) as client:
            for idx in range(10):
                res = client.post(
                    '/auth/password/forgot',
                    json={'identifier': f'bypass{idx}@example.test'},
                    headers={
                        'X-Forwarded-For': f'203.0.113.{idx + 1}, 198.51.100.66',
                        'CF-Connecting-IP': f'203.0.113.{idx + 1}',
                    },
                )
                assert res.status_code == 202
            blocked = client.post(
                '/auth/password/forgot',
                json={'identifier': 'bypass11@example.test'},
                headers={
                    'X-Forwarded-For': '203.0.113.200, 198.51.100.66',
                    'CF-Connecting-IP': '203.0.113.200',
                },
            )
            assert blocked.status_code == 429
    finally:
        get_settings.cache_clear()


def test_redis_temporarily_unavailable_behavior(recovery_app, monkeypatch):
    from app import rate_limit

    app, _, _ = recovery_app

    class BrokenRedis:
        def eval(self, *args, **kwargs):
            raise ConnectionError('Redis down')

    monkeypatch.setattr(rate_limit, 'get_redis', lambda: BrokenRedis())
    monkeypatch.setenv('REDIS_URL', 'redis://127.0.0.1:6379/0')

    # Non-production: degrades to local memory cleanly
    monkeypatch.setenv('APP_ENV', 'development')
    get_settings.cache_clear()
    try:
        with TestClient(app, client=('198.51.100.77', 1234)) as client:
            res = client.post('/auth/password/forgot', json={'identifier': 'dev-fallback@example.test'})
            assert res.status_code == 202

        # Production: fails closed with controlled 503 + Retry-After
        monkeypatch.setenv('APP_ENV', 'production')
        get_settings.cache_clear()
        with TestClient(app, client=('198.51.100.78', 1234)) as client:
            res = client.post('/auth/password/forgot', json={'identifier': 'prod-failclosed@example.test'})
            assert res.status_code == 503
            assert res.headers.get('Retry-After') == '10'
    finally:
        get_settings.cache_clear()
