"""Phase 6: Complete end-to-end procurement journey test."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base, get_db
from app.models import (
    Capability,
    Category,
    Material,
    MeasurementUnit,
    Member,
    Notification,
    Organization,
    Outbox,
    User,
)
from app.modules.auth.router import router as auth_router
from app.modules.matching.router import router as matching_router
from app.modules.organizations.auto_approval import run_auto_approval_sweep
from app.modules.organizations.router import router as org_router
from app.modules.quotations.router import router as quote_router
from app.modules.rfqs.router import router as rfq_router
from app.notification_worker import process_batch
from app.security import current_user


def test_complete_procurement_journey_buyer_to_supplier_to_closure():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    app = FastAPI()
    for r in (auth_router, org_router, rfq_router, matching_router, quote_router):
        app.include_router(r)

    def database():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = database
    settings = get_settings()
    old_mock = settings.mock_notifications
    settings.mock_notifications = True

    try:
        with SessionLocal() as db:
            unit = MeasurementUnit(code='No.', name_en='Number', active=True)
            elec = Category(code='ELEC', name_en='Electrical', name_ar='كهرباء', active=True)
            db.add_all([unit, elec])
            db.flush()
            panels = Category(code='ELEC-DIST', name_en='Distribution Panels', name_ar='لوحات التوزيع', parent_id=elec.id, active=True)
            db.add(panels)
            db.flush()
            mat = Material(code='MAT-DP-100', name_en='Distribution Panel 400A', name_ar='لوحة توزيع', category_id=panels.id, main_category_id=elec.id, subcategory_id=panels.id, unit='No.', active=True)
            sup_user = User(
                username='sup_owner', phone='+966509998877', phone_e164='+966509998877',
                phone_verified=True, email='sup@supplier.sa', email_verified=True,
                email_notifications_enabled=True, sms_notifications_enabled=True,
                password_hash='x', approval_status='approved',
            )
            sup_org = Organization(
                name='Riyadh Panels Ltd', name_normalized='riyadh panels ltd', city='Riyadh',
                is_buyer=False, is_supplier=True, is_active=True, verified_status='verified',
                business_email='sales@supplier.sa', business_phone='+966509998877',
            )
            db.add_all([mat, sup_user, sup_org])
            db.flush()
            db.add(Member(user_id=sup_user.id, organization_id=sup_org.id, role='owner'))
            db.add(Capability(org_id=sup_org.id, category_id=panels.id, city='Riyadh', material_ids=[mat.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))
            db.commit()
            elec_id, panels_id, mat_id, sup_user_id, sup_org_id = elec.id, panels.id, mat.id, sup_user.id, sup_org.id

        with TestClient(app) as client:
            # 1. Buyer registration (starts in pending approval)
            reg_res = client.post('/auth/register', json={
                'username': 'e2e_buyer',
                'phone': '+966501234567',
                'email': 'buyer@e2e.sa',
                'password': 'StrongPassword123!',
            })
            assert reg_res.status_code == 201, reg_res.text
            buyer_id = reg_res.json()['id']
            assert reg_res.json()['approval_status'] == 'pending'

            # Simulate OTP verification + 24h auto-approval
            with SessionLocal() as db:
                buyer = db.get(User, buyer_id)
                buyer.phone_verified = True
                buyer.email_verified = True
                buyer.email_notifications_enabled = True
                buyer.sms_notifications_enabled = True
                buyer.pending_approval_since = datetime.now(UTC) - timedelta(hours=25)
                db.commit()
                sweep = run_auto_approval_sweep(db)
                assert sweep['approved_users'] == 1
                assert db.get(User, buyer_id).approval_status == 'approved'

            # 2. Buyer creates organization and RFQ
            with SessionLocal() as db:
                buyer_obj = db.get(User, buyer_id)
                db.expunge(buyer_obj)
            app.dependency_overrides[current_user] = lambda: buyer_obj

            org_res = client.post('/organizations', json={
                'name': 'E2E Buyer Construction',
                'city': 'Riyadh',
                'is_buyer': True,
                'is_supplier': False,
            })
            assert org_res.status_code == 201, org_res.text
            buyer_org_id = org_res.json()['id']

            deadline = (datetime.now(UTC) + timedelta(days=7)).isoformat()
            rfq_res = client.post('/rfqs', json={
                'buyer_org_id': buyer_org_id,
                'title': 'E2E Switchgear Project',
                'city': 'Riyadh',
                'response_deadline': deadline,
                'items': [{
                    'material_id': mat_id,
                    'main_category_id': elec_id,
                    'subcategory_id': panels_id,
                    'description': 'Distribution Panel 400A',
                    'specifications': 'IP54 Schneider/ABB',
                    'unit': 'No.',
                    'quantity': 4,
                }],
            })
            assert rfq_res.status_code == 201, rfq_res.text
            rfq_id = rfq_res.json()['rfq']['id']
            item_id = rfq_res.json()['items'][0]['id']

            # 3. Publish -> fast matching -> supplier invitation
            pub_res = client.post(f'/rfqs/{rfq_id}/publish')
            assert pub_res.status_code == 200, pub_res.text
            assert pub_res.json()['matched_suppliers'] == 1
            assert pub_res.json()['invited_suppliers'] == 1

            # Process outbox to dispatch email/SMS invitation
            with SessionLocal() as db:
                process_batch(db, limit=50)
                process_batch(db, limit=50)

            # 4. Supplier checks invitations and accepts
            with SessionLocal() as db:
                sup_obj = db.get(User, sup_user_id)
                db.expunge(sup_obj)
            app.dependency_overrides[current_user] = lambda: sup_obj

            invs = client.get('/matching/invitations').json()
            assert len(invs) == 1
            inv_id = invs[0]['id']
            acc_res = client.post(f'/matching/invitations/{inv_id}/accept')
            assert acc_res.status_code == 200
            assert acc_res.json()['status'] == 'accepted'

            # 5. Supplier creates quotation draft, edits lines, submits
            draft_res = client.post(f'/quotations/invitations/{inv_id}/auto-draft')
            assert draft_res.status_code == 200, draft_res.text
            quote_id = draft_res.json()['quotation']['id']

            edit_res = client.put(f'/quotations/{quote_id}/lines', json={
                'price_basis': 'supply',
                'vat_rate': '0.15',
                'lines': [{
                    'rfq_item_id': item_id,
                    'unit_price': '12500.00',
                    'quantity': '4',
                    'lead_days': 14,
                    'compliance': 'compliant',
                }],
            })
            assert edit_res.status_code == 200, edit_res.text

            sub_res = client.post(f'/quotations/{quote_id}/submit')
            assert sub_res.status_code == 200, sub_res.text
            assert sub_res.json()['quotation']['status'] == 'submitted'

            # Process outbox for buyer notification
            with SessionLocal() as db:
                process_batch(db, limit=50)
                process_batch(db, limit=50)

            # 6. Buyer compares quotes, awards, and closes RFQ
            app.dependency_overrides[current_user] = lambda: buyer_obj
            comp_res = client.get(f'/quotations/rfqs/{rfq_id}/comparison')
            assert comp_res.status_code == 200
            assert len(comp_res.json()['quotations']) == 1
            assert comp_res.json()['quotations'][0]['totals']['subtotal'] == '50000.00'

            award_res = client.post(f'/quotations/{quote_id}/decision/awarded')
            assert award_res.status_code == 200

            close_res = client.post(f'/rfqs/{rfq_id}/close')
            assert close_res.status_code == 200
            assert close_res.json()['status'] == 'closed'

            with SessionLocal() as db:
                process_batch(db, limit=50)
                process_batch(db, limit=50)
                buyer_notifs = db.scalars(select(Notification.event).where(Notification.recipient_user_id == buyer_id)).all()
                sup_notifs = db.scalars(select(Notification.event).where(Notification.recipient_user_id == sup_user_id)).all()
                assert 'rfq_invitation_accepted' in buyer_notifs
                assert 'quotation_submitted' in buyer_notifs
                assert 'rfq.invitation' in sup_notifs
                assert 'quotation_awarded' in sup_notifs
                assert 'rfq_status_updated' in sup_notifs
    finally:
        settings.mock_notifications = old_mock
