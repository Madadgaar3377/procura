"""Comprehensive backend RBAC, workspace resolution, account suspension, and
cross-organization isolation tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Member, Organization, RFQ, User
from app.security import password_hash, token_for


@pytest.fixture()
def auth_env():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as db:
        admin = User(username='rbac-admin', email='rbac-admin@example.com', phone='+966500100001', password_hash=password_hash('StrongPass123!'), is_admin=True, is_active=True, email_verified=True, phone_verified=True)
        buyer_u = User(username='rbac-buyer', email='rbac-buyer@example.com', phone='+966500100002', password_hash=password_hash('StrongPass123!'), is_admin=False, is_active=True, email_verified=True, phone_verified=True)
        supplier_u = User(username='rbac-supplier', email='rbac-supplier@example.com', phone='+966500100003', password_hash=password_hash('StrongPass123!'), is_admin=False, is_active=True, email_verified=True, phone_verified=True)
        multi_u = User(username='rbac-multi', email='rbac-multi@example.com', phone='+966500100004', password_hash=password_hash('StrongPass123!'), is_admin=False, is_active=True, email_verified=True, phone_verified=True)
        suspended_u = User(username='rbac-suspended', email='rbac-suspended@example.com', phone='+966500100005', password_hash=password_hash('StrongPass123!'), is_admin=False, is_active=False, email_verified=True, phone_verified=True)
        db.add_all([admin, buyer_u, supplier_u, multi_u, suspended_u])
        db.flush()

        buyer_org = Organization(name='Buyer Org A', name_normalized='buyer org a', is_buyer=True, is_supplier=False, is_active=True, verified_status='verified')
        other_buyer_org = Organization(name='Buyer Org B', name_normalized='buyer org b', is_buyer=True, is_supplier=False, is_active=True, verified_status='verified')
        supplier_org = Organization(name='Supplier Org A', name_normalized='supplier org a', is_buyer=False, is_supplier=True, is_active=True, verified_status='verified')
        db.add_all([buyer_org, other_buyer_org, supplier_org])
        db.flush()

        db.add_all([
            Member(user_id=buyer_u.id, organization_id=buyer_org.id, role='owner'),
            Member(user_id=supplier_u.id, organization_id=supplier_org.id, role='owner'),
            Member(user_id=multi_u.id, organization_id=buyer_org.id, role='buyer'),
            Member(user_id=multi_u.id, organization_id=supplier_org.id, role='supplier'),
        ])
        rfq_b = RFQ(
            reference='RFQ-ISO-B',
            title='Confidential Buyer B RFQ',
            buyer_org_id=other_buyer_org.id,
            city='Riyadh',
            status='draft',
            response_deadline=datetime.now(UTC) + timedelta(days=7),
        )
        db.add(rfq_b)
        db.commit()

        tokens = {
            'admin': token_for(admin),
            'buyer': token_for(buyer_u),
            'supplier': token_for(supplier_u),
            'multi': token_for(multi_u),
            'suspended': token_for(suspended_u),
        }
        ids = {
            'buyer_org': buyer_org.id,
            'other_buyer_org': other_buyer_org.id,
            'supplier_org': supplier_org.id,
            'rfq_b': rfq_b.id,
        }

    def _override_db():
        with SessionLocal() as db:
            yield db

    fastapi_app = getattr(app, 'app', app)
    fastapi_app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(app), SessionLocal, tokens, ids
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


def _auth(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def test_admin_endpoints_enforce_401_and_403(auth_env):
    client, _, tokens, ids = auth_env
    admin_get_paths = [
        '/api/v1/admin/dashboard',
        '/api/v1/admin/users',
        '/api/v1/admin/settings',
        '/api/v1/admin/audit',
        '/api/v1/imports/bulk/suppliers/template',
        '/api/v1/imports/bulk/suppliers/jobs',
        '/api/v1/capabilities/admin/pending',
        '/api/v1/files/admin/summary',
        '/api/v1/db/performance',
        f"/api/v1/matching/admin/rfqs/{ids['rfq_b']}/diagnostics",
    ]

    for path in admin_get_paths:
        assert client.get(path).status_code == 401, f'Expected 401 unauthenticated on {path}'
        assert client.get(path, headers=_auth(tokens['buyer'])).status_code == 403, f'Expected 403 for buyer on {path}'
        assert client.get(path, headers=_auth(tokens['supplier'])).status_code == 403, f'Expected 403 for supplier on {path}'
        assert client.get(path, headers=_auth(tokens['suspended'])).status_code == 401, f'Expected 401 for suspended on {path}'
        admin_res = client.get(path, headers=_auth(tokens['admin']))
        assert admin_res.status_code == 200, f'Expected 200 for admin on {path}, got {admin_res.status_code}'


def test_workspace_resolution_and_unauthorized_workspace_switch_blocked(auth_env):
    client, _, tokens, ids = auth_env

    assert client.get('/api/v1/auth/workspace', headers=_auth(tokens['admin'])).json()['destination'] == '/app/admin/dashboard'
    assert client.get('/api/v1/auth/workspace', headers=_auth(tokens['buyer'])).json()['destination'] == '/app/buyer/dashboard'
    assert client.get('/api/v1/auth/workspace', headers=_auth(tokens['supplier'])).json()['destination'] == '/app/supplier/dashboard'

    # Buyer cannot switch to supplier org or admin role
    res_sup = client.put('/api/v1/auth/workspace', headers=_auth(tokens['buyer']), json={'role': 'supplier', 'org_id': ids['supplier_org']})
    assert res_sup.status_code == 403
    res_wrong_role = client.put('/api/v1/auth/workspace', headers=_auth(tokens['buyer']), json={'role': 'supplier', 'org_id': ids['buyer_org']})
    assert res_wrong_role.status_code == 403

    # Multi-workspace user can switch between their authorized buyer and supplier workspaces
    sw1 = client.put('/api/v1/auth/workspace', headers=_auth(tokens['multi']), json={'role': 'supplier', 'org_id': ids['supplier_org']})
    assert sw1.status_code == 200
    assert sw1.json()['destination'] == '/app/supplier/dashboard'
    assert client.get('/api/v1/auth/workspace', headers=_auth(tokens['multi'])).json()['destination'] == '/app/supplier/dashboard'

    sw2 = client.put('/api/v1/auth/workspace', headers=_auth(tokens['multi']), json={'role': 'buyer', 'org_id': ids['buyer_org']})
    assert sw2.status_code == 200
    assert sw2.json()['destination'] == '/app/buyer/dashboard'


def test_cross_organization_rfq_isolation(auth_env):
    client, _, tokens, ids = auth_env
    res_buyer_a = client.get(f"/api/v1/rfqs/{ids['rfq_b']}", headers=_auth(tokens['buyer']))
    assert res_buyer_a.status_code in {403, 404}
    res_supplier = client.get(f"/api/v1/rfqs/{ids['rfq_b']}", headers=_auth(tokens['supplier']))
    assert res_supplier.status_code in {403, 404}
    res_admin = client.get(f"/api/v1/rfqs/{ids['rfq_b']}", headers=_auth(tokens['admin']))
    assert res_admin.status_code == 200
