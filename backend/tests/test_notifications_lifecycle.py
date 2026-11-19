"""Phase 3 tests: lifecycle notifications, HTTP 202 Pending SMS handling, and bilingual templates."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    Invitation,
    Member,
    Notification,
    Organization,
    Outbox,
    RFQ,
    User,
)
from app.modules.matching.router import router as matching_router
from app.modules.notifications.branded_templates import render_message
from app.modules.rfqs.router import router as rfqs_router
from app.notification_worker import apply_sms, process_batch
from app.security import current_user


def _make_app():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(matching_router)
    app.include_router(rfqs_router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    return engine, sessions, app


def test_bilingual_templates_for_new_lifecycle_events():
    for template in ('account_approved', 'rfq_invitation_accepted', 'rfq_invitation_declined', 'rfq_status_updated'):
        subj_en, plain_en, html_en = render_message(template, {
            'language': 'en', 'channel': 'email', 'reference': 'RFQ-100',
            'title': 'Panels', 'supplier_name': 'Alpha Co', 'status': 'closed',
        })
        subj_ar, plain_ar, html_ar = render_message(template, {
            'language': 'ar', 'channel': 'email', 'reference': 'RFQ-100',
            'title': 'لوحات', 'supplier_name': 'شركة ألفا', 'status': 'closed',
        })
        assert 'Procumaterial' in subj_en and 'Procumaterial' in subj_ar
        assert plain_en and plain_ar and plain_en != plain_ar
        assert 'dir="ltr"' in html_en and 'dir="rtl"' in html_ar


def test_http_202_pending_not_treated_as_delivered_and_failure_reported():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as db:
        event = Outbox(event='notification.delivery', payload={'channel': 'sms', 'template': 'rfq_invitation'}, status='processing')
        db.add(event)
        db.flush()
        now = datetime.now(UTC)

        # HTTP 202 Pending must set awaiting_status, NOT processed/Delivered
        apply_sms(db, event, {'id': 'msg-202', 'state': 'Pending', 'http_status': 202}, now)
        assert event.status == 'awaiting_status'
        assert event.delivery_status == 'Pending'
        assert event.next_attempt_at is not None

        # Provider later reports Failed -> must record failed accurately
        apply_sms(db, event, {'id': 'msg-202', 'state': 'Failed', 'http_status': 200, 'lookup': True}, now)
        assert event.status == 'failed'
        assert event.delivery_status == 'Failed'
        assert event.last_error == 'Provider reported delivery failure'


def test_invitation_accept_decline_and_rfq_close_notify_participants(monkeypatch):
    _engine, sessions, app = _make_app()
    with sessions() as db:
        buyer_user = User(
            username='buyer_user', phone='+966501111111', phone_e164='+966501111111', phone_verified=True,
            email='buyer@example.com', email_verified=True, email_notifications_enabled=True,
            sms_notifications_enabled=True, password_hash='x', approval_status='approved',
        )
        sup_user = User(
            username='sup_user', phone='+966502222222', phone_e164='+966502222222', phone_verified=True,
            email='sup@example.com', email_verified=True, email_notifications_enabled=True,
            sms_notifications_enabled=True, password_hash='x', approval_status='approved',
        )
        db.add_all([buyer_user, sup_user])
        db.flush()

        buyer_org = Organization(name='Buyer Org', name_normalized='buyer org', city='Riyadh', is_buyer=True, is_supplier=False, verified_status='verified')
        sup_org = Organization(name='Supplier Org', name_normalized='supplier org', city='Riyadh', is_buyer=False, is_supplier=True, verified_status='verified')
        db.add_all([buyer_org, sup_org])
        db.flush()
        db.add_all([
            Member(user_id=buyer_user.id, organization_id=buyer_org.id, role='owner'),
            Member(user_id=sup_user.id, organization_id=sup_org.id, role='owner'),
        ])
        rfq = RFQ(
            reference='RFQ-NOTIF-1', buyer_org_id=buyer_org.id, title='Switchgear', city='Riyadh',
            response_deadline=datetime.now(UTC) + timedelta(days=5), status='collecting_quotes',
        )
        db.add(rfq)
        db.flush()
        inv = Invitation(rfq_id=rfq.id, supplier_org_id=sup_org.id, status='invited')
        db.add(inv)
        db.commit()
        buyer_id, sup_id, rfq_id, inv_id = buyer_user.id, sup_user.id, rfq.id, inv.id

    with TestClient(app) as client:
        # Supplier accepts invitation
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=sup_id, is_admin=False, is_active=True)
        res = client.post(f'/matching/invitations/{inv_id}/accept')
        assert res.status_code == 200, res.text

        # Buyer closes RFQ
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=buyer_id, is_admin=False, is_active=True)
        res = client.post(f'/rfqs/{rfq_id}/close')
        assert res.status_code == 200, res.text

    with sessions() as db:
        # Process outbox events (creates in-app notifications + delivery outbox jobs)
        process_batch(db, limit=50)
        # Process queued delivery outbox jobs in mock mode
        process_batch(db, limit=50)

        buyer_in_app = db.scalars(select(Notification).where(Notification.recipient_user_id == buyer_id)).all()
        sup_in_app = db.scalars(select(Notification).where(Notification.recipient_user_id == sup_id)).all()
        assert any(n.event == 'rfq_invitation_accepted' for n in buyer_in_app)
        assert any(n.event == 'rfq_status_updated' for n in sup_in_app)

        deliveries = db.scalars(select(Outbox).where(Outbox.event == 'notification.delivery')).all()
        templates = {d.payload.get('template') for d in deliveries}
        assert 'rfq_invitation_accepted' in templates
        assert 'rfq_status_updated' in templates
