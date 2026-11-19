"""Phase 2 comprehensive tests and performance benchmark for RFQ matching."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import time

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Capability,
    Category,
    Invitation,
    Material,
    Member,
    Organization,
    Outbox,
    RFQ,
    RFQItem,
    RFQMatch,
    User,
)
from app.modules.matching.service import match_and_invite
from app.notification_worker import process_batch


def _make_db():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_taxonomy(db):
    elec = Category(code='ELEC', name_en='Electrical', name_ar='كهرباء', active=True)
    db.add(elec)
    db.flush()
    panels = Category(code='ELEC-DIST', name_en='Distribution Panels', name_ar='لوحات التوزيع', parent_id=elec.id, active=True)
    ups = Category(code='ELEC-UPS', name_en='UPS Systems', name_ar='أنظمة UPS', parent_id=elec.id, active=True)
    cables = Category(code='ELEC-CAB', name_en='Power Cables', name_ar='كابلات الطاقة', parent_id=elec.id, active=True)
    db.add_all([panels, ups, cables])
    db.flush()

    m_panel = Material(
        code='MAT-DP-01', name_en='Main Distribution Panel 400A', name_ar='لوحة توزيع رئيسية',
        category_id=panels.id, main_category_id=elec.id, subcategory_id=panels.id, unit='No.', active=True,
    )
    m_ups = Material(
        code='MAT-UPS-01', name_en='Online UPS 20kVA', name_ar='UPS 20 كيلو فولت أمبير',
        category_id=ups.id, main_category_id=elec.id, subcategory_id=ups.id, unit='No.', active=True,
    )
    m_cable = Material(
        code='MAT-CAB-01', name_en='XLPE Armored Cable 4x95mm', name_ar='كابل مسلح',
        category_id=cables.id, main_category_id=elec.id, subcategory_id=cables.id, unit='m', active=True,
    )
    db.add_all([m_panel, m_ups, m_cable])
    db.flush()
    return elec, panels, ups, cables, m_panel, m_ups, m_cable


def _make_supplier(db, name, *, city='Riyadh', active=True, verified_status='verified', is_supplier=True):
    org = Organization(
        name=name, name_normalized=name.casefold(), city=city,
        is_buyer=False, is_supplier=is_supplier, is_active=active, verified_status=verified_status,
    )
    db.add(org)
    db.flush()
    return org


def test_exact_material_and_incorrect_material_exclusion_and_status_exclusions():
    SessionLocal = _make_db()
    with SessionLocal() as db:
        elec, panels, ups, _cables, m_panel, m_ups, _m_cable = _seed_taxonomy(db)
        buyer = Organization(name='Buyer Co', name_normalized='buyer co', city='Riyadh', is_buyer=True, is_supplier=False)
        db.add(buyer)
        db.flush()

        # 1. Exact material match (eligible)
        s_exact = _make_supplier(db, 'Exact Panel Co')
        db.add(Capability(org_id=s_exact.id, category_id=panels.id, city='Riyadh', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # 2. UPS supplier in same main category Electrical (must NOT match Distribution Panels!)
        s_ups = _make_supplier(db, 'UPS Only Co')
        db.add(Capability(org_id=s_ups.id, category_id=ups.id, city='Riyadh', material_ids=[m_ups.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # 3. Broad main-category capability only (must NOT match)
        s_broad = _make_supplier(db, 'Broad Elec Co')
        db.add(Capability(org_id=s_broad.id, category_id=elec.id, city='Riyadh', material_ids=[], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # 4. Rejected supplier org (must be excluded)
        s_rejected = _make_supplier(db, 'Rejected Panel Co', verified_status='rejected')
        db.add(Capability(org_id=s_rejected.id, category_id=panels.id, city='Riyadh', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # 5. Suspended supplier org (must be excluded)
        s_suspended = _make_supplier(db, 'Suspended Panel Co', verified_status='suspended')
        db.add(Capability(org_id=s_suspended.id, category_id=panels.id, city='Riyadh', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # 6. Inactive supplier org (must be excluded)
        s_inactive = _make_supplier(db, 'Inactive Panel Co', active=False)
        db.add(Capability(org_id=s_inactive.id, category_id=panels.id, city='Riyadh', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # 7. Unapproved capability (must be excluded)
        s_unapproved = _make_supplier(db, 'Unapproved Cap Co')
        db.add(Capability(org_id=s_unapproved.id, category_id=panels.id, city='Riyadh', material_ids=[m_panel.id], confirmed=False, accepts_rfqs=True, eligibility_status='enabled'))

        rfq = RFQ(
            reference='RFQ-TEST-001', buyer_org_id=buyer.id, title='Panels RFQ', city='Riyadh',
            response_deadline=datetime.now(UTC) + timedelta(days=7), status='published',
        )
        db.add(rfq)
        db.flush()
        db.add(RFQItem(
            rfq_id=rfq.id, material_id=m_panel.id, main_category_id=elec.id, subcategory_id=panels.id,
            description='Main Distribution Panel', specifications='400A', unit='unit', quantity=Decimal('2'),
        ))
        db.flush()

        summary = match_and_invite(db, rfq)
        db.commit()

        assert summary['eligible_suppliers'] == 1
        assert summary['new_invitations'] == 1
        invs = db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq.id)).all()
        assert invs == [s_exact.id]

        matches = {m.supplier_org_id: m for m in db.scalars(select(RFQMatch).where(RFQMatch.rfq_id == rfq.id)).all()}
        assert matches[s_exact.id].status == 'eligible'
        assert s_ups.id not in matches
        assert s_broad.id not in matches
        assert matches[s_rejected.id].status == 'rejected'
        assert matches[s_suspended.id].status == 'suspended'
        assert matches[s_inactive.id].status == 'inactive'
        assert matches[s_unapproved.id].status == 'pending_approval'


def test_regional_eligibility_multi_item_and_duplicate_prevention():
    SessionLocal = _make_db()
    with SessionLocal() as db:
        elec, panels, _ups, cables, m_panel, _m_ups, m_cable = _seed_taxonomy(db)
        buyer = Organization(name='Buyer Co', name_normalized='buyer co', city='Jeddah', is_buyer=True, is_supplier=False)
        db.add(buyer)
        db.flush()

        # Covers both items + exact city
        s_full = _make_supplier(db, 'Full Jeddah Co')
        db.add(Capability(org_id=s_full.id, category_id=panels.id, city='Jeddah', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))
        db.add(Capability(org_id=s_full.id, category_id=cables.id, city='Jeddah', material_ids=[m_cable.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # Covers 1 item + nationwide
        s_partial = _make_supplier(db, 'Nationwide Panels Co')
        db.add(Capability(org_id=s_partial.id, category_id=panels.id, city='All Cities', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        # Wrong city
        s_wrong_city = _make_supplier(db, 'Dammam Only Co')
        db.add(Capability(org_id=s_wrong_city.id, category_id=panels.id, city='Dammam', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))

        rfq = RFQ(
            reference='RFQ-MULTI-001', buyer_org_id=buyer.id, title='Multi-item RFQ', city='Jeddah',
            response_deadline=datetime.now(UTC) + timedelta(days=5), status='published',
        )
        db.add(rfq)
        db.flush()
        db.add_all([
            RFQItem(rfq_id=rfq.id, material_id=m_panel.id, main_category_id=elec.id, subcategory_id=panels.id, description='Panel', specifications='400A', unit='unit', quantity=Decimal('1')),
            RFQItem(rfq_id=rfq.id, material_id=m_cable.id, main_category_id=elec.id, subcategory_id=cables.id, description='Cable', specifications='4x95', unit='m', quantity=Decimal('100')),
        ])
        db.flush()

        first = match_and_invite(db, rfq)
        db.commit()
        assert first['eligible_suppliers'] == 2
        assert first['new_invitations'] == 2

        # Repeated run must NOT duplicate RFQMatch or Invitation
        second = match_and_invite(db, rfq)
        db.commit()
        assert second['new_invitations'] == 0
        assert db.scalar(select(func.count(Invitation.id)).where(Invitation.rfq_id == rfq.id)) == 2
        assert db.scalar(select(func.count(RFQMatch.id)).where(RFQMatch.rfq_id == rfq.id)) == 3

        matches = {m.supplier_org_id: m for m in db.scalars(select(RFQMatch).where(RFQMatch.rfq_id == rfq.id)).all()}
        assert matches[s_full.id].status == 'eligible'
        assert matches[s_partial.id].status == 'partial'
        assert matches[s_wrong_city.id].status == 'location_mismatch'
        assert matches[s_full.id].score > matches[s_partial.id].score


def test_large_supplier_dataset_performance():
    SessionLocal = _make_db()
    with SessionLocal() as db:
        elec, panels, ups, _cables, m_panel, m_ups, _m_cable = _seed_taxonomy(db)
        buyer = Organization(name='Big Buyer', name_normalized='big buyer', city='Riyadh', is_buyer=True, is_supplier=False)
        db.add(buyer)
        db.flush()

        # Create 2,000 suppliers: 200 matching panels in Riyadh, 1,800 in UPS or other cities
        orgs = []
        for i in range(2000):
            orgs.append(Organization(
                name=f'Supplier {i:04d}', name_normalized=f'supplier {i:04d}',
                city='Riyadh' if i % 2 == 0 else 'Dammam',
                is_buyer=False, is_supplier=True, is_active=True, verified_status='verified',
            ))
        db.add_all(orgs)
        db.flush()

        caps = []
        for i, org in enumerate(orgs):
            if i < 200:
                caps.append(Capability(org_id=org.id, category_id=panels.id, city='Riyadh', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))
            else:
                caps.append(Capability(org_id=org.id, category_id=ups.id, city='Riyadh', material_ids=[m_ups.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))
        db.add_all(caps)
        db.flush()

        rfq = RFQ(
            reference='RFQ-PERF-2000', buyer_org_id=buyer.id, title='Perf Test', city='Riyadh',
            response_deadline=datetime.now(UTC) + timedelta(days=5), status='published',
        )
        db.add(rfq)
        db.flush()
        db.add(RFQItem(
            rfq_id=rfq.id, material_id=m_panel.id, main_category_id=elec.id, subcategory_id=panels.id,
            description='Main Panel', specifications='400A', unit='unit', quantity=Decimal('1'),
        ))
        db.commit()

        t0 = time.perf_counter()
        summary = match_and_invite(db, rfq)
        db.commit()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert summary['eligible_suppliers'] == 200
        assert summary['new_invitations'] == 200
        assert elapsed_ms < 5000.0, f'Matching 2,000 suppliers took {elapsed_ms:.1f}ms'


def test_matching_failure_and_outbox_retry(monkeypatch):
    SessionLocal = _make_db()
    with SessionLocal() as db:
        elec, panels, _ups, _cables, m_panel, _m_ups, _m_cable = _seed_taxonomy(db)
        buyer = Organization(name='Buyer Co', name_normalized='buyer co', city='Riyadh', is_buyer=True, is_supplier=False)
        db.add(buyer)
        db.flush()
        s1 = _make_supplier(db, 'Retry Supplier Co')
        db.add(Capability(org_id=s1.id, category_id=panels.id, city='Riyadh', material_ids=[m_panel.id], confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))
        rfq = RFQ(
            reference='RFQ-RETRY-1', buyer_org_id=buyer.id, title='Retry RFQ', city='Riyadh',
            response_deadline=datetime.now(UTC) + timedelta(days=5), status='published',
        )
        db.add(rfq)
        db.flush()
        db.add(RFQItem(
            rfq_id=rfq.id, material_id=m_panel.id, main_category_id=elec.id, subcategory_id=panels.id,
            description='Panel', specifications='400A', unit='unit', quantity=Decimal('1'),
        ))
        job = Outbox(event='rfq.published', payload={'rfq_id': rfq.id}, dedupe_key=f'rfq-published:{rfq.id}')
        db.add(job)
        db.commit()
        job_id = job.id

        # Simulate transient failure on first worker attempt
        import app.modules.matching.service as matching_service
        original_match = matching_service.match_and_invite
        monkeypatch.setattr(matching_service, 'match_and_invite', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('Transient DB glitch')))
        process_batch(db, limit=10, event_ids=[job_id])
        db.expire_all()
        failed_job = db.get(Outbox, job_id)
        assert failed_job.status == 'retry'
        assert failed_job.attempts == 1
        assert failed_job.next_attempt_at is not None

        # Restore and retry after backoff
        monkeypatch.setattr(matching_service, 'match_and_invite', original_match)
        process_batch(db, limit=10, now=failed_job.next_attempt_at + timedelta(seconds=1), event_ids=[job_id])
        db.expire_all()
        done_job = db.get(Outbox, job_id)
        assert done_job.status == 'processed'
        assert db.scalar(select(func.count(Invitation.id)).where(Invitation.rfq_id == rfq.id)) == 1
