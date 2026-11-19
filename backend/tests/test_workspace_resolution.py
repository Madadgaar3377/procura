"""Tests for automatic workspace resolution, persistence, and authorization."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Member, Organization, User
from app.modules.auth.router import router as auth_router
from app.modules.organizations.router import router as org_router
from app.security import current_user


def _make_app():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(org_router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    return engine, sessions, app


def test_new_buyer_and_supplier_onboarding_once_then_direct_dashboard():
    _engine, sessions, app = _make_app()
    with TestClient(app) as client:
        # 1. New Buyer registers with preferred_role='buyer'
        reg_b = client.post('/auth/register', json={
            'username': 'buyer_new',
            'phone': '+966501111001',
            'email': 'buyer_new@example.com',
            'password': 'StrongPassword123!',
            'preferred_role': 'buyer',
        })
        assert reg_b.status_code == 201, reg_b.text
        buyer_id = reg_b.json()['id']

        with sessions() as db:
            u_buyer = db.get(User, buyer_id)
            assert u_buyer.preferred_role == 'buyer'
            assert u_buyer.onboarding_completed is False
            db.expunge(u_buyer)

        app.dependency_overrides[current_user] = lambda: u_buyer
        # Before creating company: resolves to /app/buyer/company (setup once)
        ws1 = client.get('/auth/workspace')
        assert ws1.status_code == 200
        assert ws1.json()['destination'] == '/app/buyer/company'
        assert ws1.json()['needs_onboarding'] is True

        # Create buyer company -> onboarding completed, resolves to /app/buyer/dashboard
        org_b = client.post('/organizations', json={'name': 'Buyer Co 1', 'city': 'Riyadh', 'is_buyer': True, 'is_supplier': False})
        assert org_b.status_code == 201
        ws2 = client.get('/auth/workspace')
        assert ws2.status_code == 200
        assert ws2.json()['destination'] == '/app/buyer/dashboard'
        assert ws2.json()['needs_onboarding'] is False
        assert ws2.json()['org_id'] == org_b.json()['id']

        # 2. New Supplier registers with preferred_role='supplier'
        reg_s = client.post('/auth/register', json={
            'username': 'supplier_new',
            'phone': '+966501111002',
            'email': 'supplier_new@example.com',
            'password': 'StrongPassword123!',
            'preferred_role': 'supplier',
        })
        assert reg_s.status_code == 201
        sup_id = reg_s.json()['id']

        with sessions() as db:
            u_sup = db.get(User, sup_id)
            db.expunge(u_sup)
        app.dependency_overrides[current_user] = lambda: u_sup

        ws_s1 = client.get('/auth/workspace')
        assert ws_s1.json()['destination'] == '/app/supplier/company'

        org_s = client.post('/organizations', json={'name': 'Supplier Co 1', 'city': 'Jeddah', 'is_buyer': False, 'is_supplier': True})
        assert org_s.status_code == 201
        ws_s2 = client.get('/auth/workspace')
        assert ws_s2.json()['destination'] == '/app/supplier/dashboard'
        assert ws_s2.json()['needs_onboarding'] is False


def test_multiple_companies_persistence_revoked_membership_pending_and_admin():
    _engine, sessions, app = _make_app()
    with sessions() as db:
        u = User(
            username='multi_user', phone='+966502222001', phone_e164='+966502222001',
            phone_verified=True, email='multi@example.com', email_verified=True,
            password_hash='x', approval_status='pending',
        )
        admin = User(
            username='admin_user', phone='+966502222002', phone_e164='+966502222002',
            phone_verified=True, email='admin@example.com', email_verified=True,
            password_hash='x', is_admin=True, approval_status='approved',
        )
        no_org_user = User(
            username='legacy_no_org', phone='+966502222003', phone_e164='+966502222003',
            phone_verified=True, email='legacy@example.com', email_verified=True,
            password_hash='x', approval_status='approved',
        )
        db.add_all([u, admin, no_org_user])
        db.flush()

        org1 = Organization(name='Alpha Buyer', name_normalized='alpha buyer', city='Riyadh', is_buyer=True, is_supplier=False)
        org2 = Organization(name='Beta Supplier', name_normalized='beta supplier', city='Dammam', is_buyer=False, is_supplier=True)
        org_unauth = Organization(name='Unauthorized Org', name_normalized='unauth', city='Riyadh', is_buyer=True, is_supplier=True)
        db.add_all([org1, org2, org_unauth])
        db.flush()
        m1 = Member(user_id=u.id, organization_id=org1.id, role='owner')
        m2 = Member(user_id=u.id, organization_id=org2.id, role='owner')
        db.add_all([m1, m2])
        db.commit()
        u_id, admin_id, no_org_id, org1_id, org2_id, unauth_id, m2_id = u.id, admin.id, no_org_user.id, org1.id, org2.id, org_unauth.id, m2.id

    with TestClient(app) as client:
        # Pending approval user with existing companies opens deterministic default dashboard (no repeated onboarding)
        with sessions() as db:
            u_obj = db.get(User, u_id)
            db.expunge(u_obj)
        app.dependency_overrides[current_user] = lambda: u_obj
        ws = client.get('/auth/workspace')
        assert ws.status_code == 200
        assert ws.json()['destination'] == '/app/buyer/dashboard'
        assert ws.json()['org_id'] == org1_id
        assert len(ws.json()['workspaces']) == 2

        # Switch to second company (Supplier) -> persisted in DB
        sw = client.put('/auth/workspace', json={'role': 'supplier', 'org_id': org2_id})
        assert sw.status_code == 200
        assert sw.json()['role'] == 'supplier'
        assert sw.json()['org_id'] == org2_id
        assert sw.json()['destination'] == '/app/supplier/dashboard'

        # Re-read (simulating refresh / logout & login) -> last workspace restored
        with sessions() as db:
            u_reloaded = db.get(User, u_id)
            assert u_reloaded.last_workspace_role == 'supplier'
            assert u_reloaded.last_org_id == org2_id
            db.expunge(u_reloaded)
        app.dependency_overrides[current_user] = lambda: u_reloaded
        ws_after = client.get('/auth/workspace')
        assert ws_after.json()['destination'] == '/app/supplier/dashboard'
        assert ws_after.json()['org_id'] == org2_id

        # Unauthorized org switch is rejected with 403
        bad = client.put('/auth/workspace', json={'role': 'buyer', 'org_id': unauth_id})
        assert bad.status_code == 403

        # Revoked membership: remove m2 -> falls back to remaining authorized workspace (org1)
        with sessions() as db:
            db.delete(db.get(Member, m2_id))
            db.commit()
            u_revoked = db.get(User, u_id)
            db.expunge(u_revoked)
        app.dependency_overrides[current_user] = lambda: u_revoked
        ws_revoked = client.get('/auth/workspace')
        assert ws_revoked.json()['destination'] == '/app/buyer/dashboard'
        assert ws_revoked.json()['org_id'] == org1_id

        # Admin account preserves admin routing
        with sessions() as db:
            a_obj = db.get(User, admin_id)
            db.expunge(a_obj)
        app.dependency_overrides[current_user] = lambda: a_obj
        ws_admin = client.get('/auth/workspace')
        assert ws_admin.json()['destination'] == '/app/admin/dashboard'

        # User with no company and no preferred_role -> /organizations
        with sessions() as db:
            n_obj = db.get(User, no_org_id)
            db.expunge(n_obj)
        app.dependency_overrides[current_user] = lambda: n_obj
        ws_none = client.get('/auth/workspace')
        assert ws_none.json()['destination'] == '/organizations'
        assert ws_none.json()['needs_onboarding'] is True
