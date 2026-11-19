"""Phase 4 tests: 24-hour automatic approval rules, safety guards, and idempotency."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    AuditEvent,
    Capability,
    Category,
    CompanyClaim,
    Member,
    Notification,
    Organization,
    Outbox,
    PlatformSettings,
    User,
)
from app.modules.admin.router import router as admin_router
from app.modules.organizations.auto_approval import run_auto_approval_sweep
from app.notification_worker import process_batch
from app.security import admin_user


def _make_db():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _seed_user(db, idx, *, hours_ago=25, phone_verified=True, email_verified=True,
               email='user@example.com', approval_status='pending', manual_review_required=False, is_active=True):
    now = datetime.now(UTC)
    user = User(
        username=f'user_{idx}',
        phone=f'+9665000000{idx:02d}',
        phone_e164=f'+9665000000{idx:02d}',
        phone_verified=phone_verified,
        email=f'{idx}_{email}' if email else None,
        email_verified=email_verified,
        email_notifications_enabled=True,
        sms_notifications_enabled=True,
        password_hash='hashed',
        is_active=is_active,
        approval_status=approval_status,
        pending_approval_since=now - timedelta(hours=hours_ago),
        manual_review_required=manual_review_required,
    )
    db.add(user)
    db.flush()
    return user


def test_23h_remains_pending_and_24h_approves_idempotently():
    _engine, SessionLocal = _make_db()
    with SessionLocal() as db:
        u_23h = _seed_user(db, 1, hours_ago=23)
        u_24h = _seed_user(db, 2, hours_ago=25)
        buyer_org = Organization(name='Buyer 24h', name_normalized='buyer 24h', city='Riyadh', is_buyer=True, is_supplier=False, source='registered')
        db.add(buyer_org)
        db.flush()
        db.add(Member(user_id=u_24h.id, organization_id=buyer_org.id, role='owner'))
        db.commit()

        res = run_auto_approval_sweep(db)
        assert res['approved_users'] == 1

        db.expire_all()
        assert db.get(User, u_23h.id).approval_status == 'pending'
        approved = db.get(User, u_24h.id)
        assert approved.approval_status == 'approved'
        assert approved.approval_method == 'auto_24h'
        assert approved.approval_reason == 'AUTO_APPROVED_AFTER_24_HOURS'
        assert approved.approved_at is not None
        assert db.get(Organization, buyer_org.id).verified_status == 'verified'

        # Audit history recorded with SYSTEM actor and AUTO_APPROVED_AFTER_24_HOURS
        audit_ev = db.scalar(select(AuditEvent).where(AuditEvent.entity_id == u_24h.id, AuditEvent.action == 'account.auto_approved'))
        assert audit_ev is not None
        assert audit_ev.actor_user_id is None
        assert audit_ev.metadata_json['actor'] == 'SYSTEM'
        assert audit_ev.metadata_json['reason'] == 'AUTO_APPROVED_AFTER_24_HOURS'

        # Repeated scheduler execution must be completely idempotent
        res2 = run_auto_approval_sweep(db)
        assert res2['approved_users'] == 0
        assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.entity_id == u_24h.id, AuditEvent.action == 'account.auto_approved')) == 1
        assert db.scalar(select(func.count(Outbox.id)).where(Outbox.dedupe_key == f'account-approved:{u_24h.id}')) == 1

        # Process outbox to verify approval notification delivery (in_app, email, sms)
        process_batch(db, limit=20)
        channels = set(db.scalars(select(Notification.channel).where(Notification.recipient_user_id == u_24h.id, Notification.event == 'account_approved')).all())
        assert channels == {'in_app', 'email', 'sms'}


def test_unverified_rejected_flagged_and_imported_ownership_never_auto_approved():
    _engine, SessionLocal = _make_db()
    with SessionLocal() as db:
        u_unverified_phone = _seed_user(db, 10, hours_ago=30, phone_verified=False)
        u_unverified_email = _seed_user(db, 11, hours_ago=30, email_verified=False)
        u_rejected = _seed_user(db, 12, hours_ago=30, approval_status='rejected')
        u_flagged = _seed_user(db, 13, hours_ago=30, manual_review_required=True)

        # User claiming an imported company must NOT get ownership or auto-approval
        u_claimant = _seed_user(db, 14, hours_ago=30)
        imported_org = Organization(name='Imported Co', name_normalized='imported co', city='Riyadh', is_buyer=False, is_supplier=True, source='imported', verified_status='verified')
        db.add(imported_org)
        db.flush()
        db.add(CompanyClaim(org_id=imported_org.id, claimant_id=u_claimant.id, evidence='I work here', status='pending'))

        # Self-registered supplier: user account activates at 24h, but capabilities remain unapproved
        u_sup = _seed_user(db, 15, hours_ago=30)
        sup_org = Organization(name='Self Reg Sup', name_normalized='self reg sup', city='Riyadh', is_buyer=False, is_supplier=True, source='registered', verified_status='unverified')
        cat = Category(code='CAT-1', name_en='Pipes', name_ar='أنابيب', active=True)
        db.add_all([sup_org, cat])
        db.flush()
        db.add(Member(user_id=u_sup.id, organization_id=sup_org.id, role='owner'))
        cap = Capability(org_id=sup_org.id, category_id=cat.id, city='Riyadh', confirmed=False, accepts_rfqs=True, eligibility_status='enabled')
        db.add(cap)
        db.commit()

        res = run_auto_approval_sweep(db)
        assert res['approved_users'] == 1  # Only u_sup

        db.expire_all()
        assert db.get(User, u_unverified_phone.id).approval_status == 'pending'
        assert db.get(User, u_unverified_email.id).approval_status == 'pending'
        assert db.get(User, u_rejected.id).approval_status == 'rejected'
        assert db.get(User, u_flagged.id).approval_status == 'pending'
        assert db.get(User, u_claimant.id).approval_status == 'pending'
        # Imported company still has NO owner
        assert db.scalar(select(func.count(Member.id)).where(Member.organization_id == imported_org.id)) == 0
        # Self-registered supplier user is approved, but capability remains unconfirmed!
        assert db.get(User, u_sup.id).approval_status == 'approved'
        assert db.get(Capability, cap.id).confirmed is False


def test_manual_admin_approval_and_configurable_setting():
    _engine, SessionLocal = _make_db()
    app = FastAPI()
    app.include_router(admin_router)

    def database():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[admin_user] = lambda: SimpleNamespace(id='admin-1', is_admin=True, is_active=True)

    with SessionLocal() as db:
        u1 = _seed_user(db, 20, hours_ago=2)
        u2 = _seed_user(db, 21, hours_ago=30)
        db.commit()
        u1_id, u2_id = u1.id, u2.id

    with TestClient(app) as client:
        # Manual admin approval works immediately
        res = client.post(f'/admin/users/{u1_id}/approve', json={'reason': 'Verified CR manually'})
        assert res.status_code == 200, res.text
        assert res.json()['approval_status'] == 'approved'
        assert res.json()['approval_method'] == 'manual_admin'

        # Disable auto-approval via admin settings
        s_res = client.patch('/admin/settings', json={'auto_approval_enabled': False, 'auto_approval_hours': 48})
        assert s_res.status_code == 200
        assert s_res.json()['auto_approval_enabled'] is False

    with SessionLocal() as db:
        # Scheduler respects disabled setting and does not duplicate u1
        res = run_auto_approval_sweep(db)
        assert res['approved_users'] == 0
        assert res['enabled'] is False
        assert db.get(User, u2_id).approval_status == 'pending'
        assert db.get(User, u1_id).approval_method == 'manual_admin'
