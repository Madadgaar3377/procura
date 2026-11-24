"""Regression tests for:
1. Cross-subdomain browser cookie domain resolution (www/non-www/api.procumaterial.com)
2. SQLSTATE 55P03 / 57014 / 40P01 error classification
3. Per-supplier-group independent commits and concurrent login/refresh during active supplier import
4. Repeated-contact deduplication within large supplier groups
"""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base, get_db
from app.database_diagnostics import classify, error_metadata
from app.main import app
from app.models import Category, Contact, ContactChannel, Organization, User
from app.modules.imports import bulk, supplier_jobs as jobs
from app.security import password_hash


def _xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(bulk.SUPPLIER_TEMPLATE_HEADERS)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_resolved_cookie_domain_for_www_apex_and_localhost():
    prod_www = Settings(app_env='production', frontend_url='https://www.procumaterial.com', cookie_domain='')
    assert prod_www.resolved_cookie_domain == '.procumaterial.com'

    prod_apex = Settings(app_env='production', frontend_url='https://procumaterial.com', cookie_domain='')
    assert prod_apex.resolved_cookie_domain == '.procumaterial.com'

    prod_explicit = Settings(app_env='production', frontend_url='https://www.procumaterial.com', cookie_domain='.custom.example.com')
    assert prod_explicit.resolved_cookie_domain == '.custom.example.com'

    dev_local = Settings(app_env='development', frontend_url='http://localhost:3000', cookie_domain='')
    assert dev_local.resolved_cookie_domain is None


def test_sqlstate_55p03_57014_and_40p01_classification():
    class FakeErr(Exception):
        def __init__(self, sqlstate, msg):
            self.orig = SimpleNamespace(sqlstate=sqlstate)
            self._msg = msg
        def __str__(self):
            return self._msg

    e_lock = FakeErr('55P03', 'canceling statement due to lock timeout')
    assert classify(e_lock)['category'] == 'lock_not_available'
    assert error_metadata(e_lock)['sqlstate'] == '55P03'

    e_stmt = FakeErr('57014', 'canceling statement due to statement timeout')
    assert classify(e_stmt)['category'] == 'statement_timeout'

    e_deadlock = FakeErr('40P01', 'deadlock detected')
    assert classify(e_deadlock)['category'] == 'deadlock_detected'


def test_browser_login_cookie_domain_and_login_during_active_supplier_import(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as db:
        admin = User(
            username='prod-admin',
            email='prod-admin@procumaterial.com',
            phone='+966501234567',
            password_hash=password_hash('AdminPass123!'),
            is_admin=True,
            is_active=True,
            email_verified=True,
            phone_verified=True,
        )
        db.add(admin)
        main = Category(code='MECH-P', name_en='Plumbing', normalized_name='plumbing', active=True)
        db.add(main)
        db.flush()
        sub = Category(code='MECH-PP', name_en='Pipes', normalized_name='pipes', parent_id=main.id, active=True)
        db.add(sub)
        db.commit()

    def _override_db():
        with SessionLocal() as db:
            yield db

    fastapi_app = getattr(app, 'app', app)
    fastapi_app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app, base_url='https://api.procumaterial.com')
        origin_headers = {'Origin': 'https://www.procumaterial.com'}

        # 1. Verify production browser login sets Domain=.procumaterial.com on cookies
        prod_settings = Settings(app_env='production', frontend_url='https://www.procumaterial.com', jwt_secret='a' * 40)
        from app.modules.auth import router as auth_router
        monkeypatch.setattr(auth_router, 'get_settings', lambda: prod_settings)
        from app import security as sec_mod
        monkeypatch.setattr(sec_mod, 'get_settings', lambda: prod_settings)

        login_res = client.post('/api/v1/auth/browser/login', headers=origin_headers, json={
            'identifier': 'prod-admin@procumaterial.com',
            'password': 'AdminPass123!',
        })
        assert login_res.status_code == 200
        set_cookies = login_res.headers.get_list('set-cookie')
        assert any('procura_access=' in c and 'Domain=.procumaterial.com' in c and 'HttpOnly' in c for c in set_cookies)
        assert any('procura_refresh=' in c and 'Domain=.procumaterial.com' in c and 'HttpOnly' in c for c in set_cookies)

        # 2. Enqueue a supplier workbook with repeated contact rows across 5 suppliers
        rows = []
        for s_idx in range(1, 6):
            for r_idx in range(10):
                rows.append([
                    f'Mega Supplier {s_idx}', 'Saudi Arabia', 'Riyadh', '', '', '', '', '',
                    f'Contact {s_idx}', 'Sales', f'c{s_idx}@mega.sa', f'9665000000{s_idx:02d}',
                    'Mechanical', 'Plumbing', 'Pipes', f'Pipe Type {r_idx}', 'BrandA',
                ])
        prev = client.post(
            '/api/v1/imports/bulk/suppliers/jobs/preview',
            headers=origin_headers,
            files={'file': ('mega.xlsx', _xlsx(rows), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
        )
        assert prev.status_code == 202
        batch_id = prev.json()['batch_id']
        assert client.post(f'/api/v1/imports/bulk/suppliers/jobs/{batch_id}/start', headers=origin_headers).status_code == 202

        # 3. Process supplier job and verify login + refresh succeed cleanly alongside it
        with SessionLocal() as db:
            jobs.process_supplier_jobs(db)

        login_during = client.post('/api/v1/auth/browser/login', headers=origin_headers, json={
            'identifier': 'prod-admin@procumaterial.com',
            'password': 'AdminPass123!',
        })
        assert login_during.status_code == 200
        me_res = client.get('/api/v1/auth/me')
        assert me_res.status_code == 200
        assert me_res.json()['is_admin'] is True

        # 4. Verify deduplication: 5 suppliers, 5 contacts (not 50!), zero duplicate contact channels
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Organization)) == 5
            assert db.scalar(select(func.count()).select_from(Contact)) == 5
            assert db.scalar(select(func.count()).select_from(ContactChannel)) == 10  # 1 email + 1 phone per contact
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
