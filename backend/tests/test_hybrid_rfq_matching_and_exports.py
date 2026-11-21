"""End-to-end tests for hybrid material classification, pumps & filters supplier matching,
AI fallback, region restrictions, admin diagnostics, and grouped PDF/Excel exports."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    Capability,
    Category,
    ImportedSupplierTrade,
    Invitation,
    Material,
    MeasurementUnit,
    Member,
    Organization,
    PriceBookItem,
    RFQ,
    RFQItem,
    RFQMatch,
    User,
)
from app.modules.matching.router import router as matching_router
from app.modules.matching.service import match_and_invite
from app.modules.rfqs.imports import process_rfq_import_jobs
from app.modules.rfqs.material_matching import _LOCAL_CACHE, classify_material_description
from app.modules.rfqs.router import router as rfqs_router
from app.security import current_user


def _build_env():
    _LOCAL_CACHE.clear()
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as db:
        for code, name_en in [('NO.', 'Number'), ('M', 'Meter'), ('SET', 'Set'), ('EA', 'Each')]:
            db.add(MeasurementUnit(code=code, name_en=name_en, active=True))

        mech = Category(code='MECH', name_en='Mechanical', name_ar='ميكانيكا', active=True)
        plumb = Category(code='PLUMB', name_en='Plumbing & Water', name_ar='السباكة والمياه', active=True)
        elec = Category(code='ELEC', name_en='Electrical', name_ar='كهرباء', active=True)
        db.add_all([mech, plumb, elec])
        db.flush()

        pumps = Category(code='MECH-PUMP', name_en='Pumps', name_ar='مضخات', parent_id=mech.id, active=True)
        filters = Category(code='PLUMB-FILT', name_en='Water Filters', name_ar='فلاتر المياه', parent_id=plumb.id, active=True)
        valves = Category(code='MECH-VALV', name_en='Valves', name_ar='صمامات', parent_id=mech.id, active=True)
        cables = Category(code='ELEC-CAB', name_en='Cables', name_ar='كابلات', parent_id=elec.id, active=True)
        db.add_all([pumps, filters, valves, cables])
        db.flush()

        m_pump = Material(
            code='MAT-PUMP-100',
            name_en='Centrifugal Booster Pump 5kW',
            normalized_description='centrifugal booster pump 5kw',
            name_ar='مضخة طرد مركزي 5 كيلوواط',
            category_id=pumps.id,
            main_category_id=mech.id,
            subcategory_id=pumps.id,
            unit='NO.',
            discipline='plumbing',  # previously dropped by silent discipline filter!
            active=True,
        )
        m_filter = Material(
            code='MAT-FILT-200',
            name_en='Sand Media Water Filter DN80',
            normalized_description='sand media water filter dn80',
            name_ar='فلتر مياه رملي DN80',
            category_id=filters.id,
            main_category_id=plumb.id,
            subcategory_id=filters.id,
            unit='NO.',
            discipline='water',
            active=True,
        )
        db.add_all([m_pump, m_filter])

        buyer_user = User(username='buyer_u', email='buyer@example.com', phone='+966500000001', password_hash='x', is_active=True, is_admin=False)
        admin_user = User(username='admin_u', email='admin@example.com', phone='+966500000002', password_hash='x', is_active=True, is_admin=True)
        supplier_user = User(username='supplier_u', email='supplier@example.com', phone='+966500000003', password_hash='x', is_active=True, is_admin=False)
        db.add_all([buyer_user, admin_user, supplier_user])
        db.flush()

        buyer_org = Organization(name='Buyer Construction Co', name_normalized='buyer construction co', city='Khobar', is_buyer=True, is_supplier=False, is_active=True, verified_status='verified')
        db.add(buyer_org)
        db.flush()
        db.add(Member(user_id=buyer_user.id, organization_id=buyer_org.id, role='buyer'))

        # Supplier 1: Exact subcategory + trade keywords in Eastern Province (covers Khobar/Dammam)
        s_pump_exact = Organization(name='Gulf Pumping Systems', name_normalized='gulf pumping systems', city='Dammam', is_buyer=False, is_supplier=True, is_active=True, verified_status='verified')
        # Supplier 2: Main category capability with trade_keywords for filters & pumps (different wording: "filtration", "strainers")
        s_filter_kw = Organization(name='Al-Khobar Filtration & Hydraulics', name_normalized='al-khobar filtration & hydraulics', city='Al Khobar', is_buyer=False, is_supplier=True, is_active=True, verified_status='verified', description='Industrial water filtration, strainers and booster pumps')
        # Supplier 3: PriceBook verified catalog item for pumps in Al Khobar (alias of Khobar)
        s_catalog = Organization(name='Eastern MEP Catalog Co', name_normalized='eastern mep catalog co', city='Al Khobar', is_buyer=False, is_supplier=True, is_active=True, verified_status='verified')
        # Supplier 4: Jeddah-only supplier (must be excluded by region for Khobar RFQ)
        s_jeddah = Organization(name='Jeddah Pumps Only', name_normalized='jeddah pumps only', city='Jeddah', is_buyer=False, is_supplier=True, is_active=True, verified_status='verified')
        # Supplier 5: Suspended supplier (must be excluded)
        s_suspended = Organization(name='Suspended Pumps Co', name_normalized='suspended pumps co', city='Khobar', is_buyer=False, is_supplier=True, is_active=True, verified_status='suspended')
        # Supplier 6: Unrelated electrical cable supplier (must NOT match pumps/filters)
        s_elec = Organization(name='Riyadh Cables Co', name_normalized='riyadh cables co', city='All Cities', is_buyer=False, is_supplier=True, is_active=True, verified_status='verified')

        db.add_all([s_pump_exact, s_filter_kw, s_catalog, s_jeddah, s_suspended, s_elec])
        db.flush()
        db.add(Member(user_id=supplier_user.id, organization_id=s_pump_exact.id, role='supplier'))

        db.add_all([
            Capability(org_id=s_pump_exact.id, category_id=pumps.id, city='Eastern Province', material_ids=[m_pump.id], trade_keywords='centrifugal pumps | submersible pumps', confirmed=True, accepts_rfqs=True, eligibility_status='enabled'),
            Capability(org_id=s_filter_kw.id, category_id=plumb.id, city='Al Khobar', material_ids=[], trade_keywords='water filters | cartridge filtration | strainers | booster pumps', confirmed=True, accepts_rfqs=True, eligibility_status='enabled'),
            Capability(org_id=s_catalog.id, category_id=valves.id, city='الخبر', material_ids=[], trade_keywords='industrial valves', confirmed=True, accepts_rfqs=True, eligibility_status='enabled'),
            Capability(org_id=s_jeddah.id, category_id=pumps.id, city='Jeddah', material_ids=[m_pump.id], trade_keywords='pumps', confirmed=True, accepts_rfqs=True, eligibility_status='enabled'),
            Capability(org_id=s_suspended.id, category_id=pumps.id, city='Khobar', material_ids=[m_pump.id], trade_keywords='pumps', confirmed=True, accepts_rfqs=True, eligibility_status='enabled'),
            Capability(org_id=s_elec.id, category_id=cables.id, city='All Cities', material_ids=[], trade_keywords='armored cables', confirmed=True, accepts_rfqs=True, eligibility_status='enabled'),
        ])
        db.add(PriceBookItem(
            org_id=s_catalog.id,
            material_id=m_pump.id,
            main_category_id=mech.id,
            subcategory_id=pumps.id,
            supplier_description='Heavy-Duty Centrifugal Booster Pump 5kW',
            unit='NO.',
            unit_price=Decimal('4500.00'),
            active=True,
        ))
        db.add(ImportedSupplierTrade(
            org_id=s_filter_kw.id,
            import_batch_id='batch-seed-1',
            raw_trade='Water Filtration Systems & Sand Filters',
            raw_brands='Pentair, Grundfos',
            raw_location='Al Khobar',
            mapping_status='mapped',
        ))
        db.commit()

        ids = {
            'mech': mech.id, 'plumb': plumb.id, 'elec': elec.id,
            'pumps': pumps.id, 'filters': filters.id, 'valves': valves.id, 'cables': cables.id,
            'm_pump': m_pump.id, 'm_filter': m_filter.id,
            'buyer_user': buyer_user.id, 'admin_user': admin_user.id, 'supplier_user': supplier_user.id,
            'buyer_org': buyer_org.id,
            's_pump_exact': s_pump_exact.id, 's_filter_kw': s_filter_kw.id, 's_catalog': s_catalog.id,
            's_jeddah': s_jeddah.id, 's_suspended': s_suspended.id, 's_elec': s_elec.id,
        }

    app = FastAPI()
    app.include_router(rfqs_router, prefix='/api/v1')
    app.include_router(matching_router, prefix='/api/v1')
    active_user_id = {'id': ids['buyer_user']}

    def _override_db():
        with SessionLocal() as db:
            yield db

    def _override_user():
        with SessionLocal() as db:
            return db.get(User, active_user_id['id'])

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    client = TestClient(app)
    return SessionLocal, client, active_user_id, ids


def test_pumps_and_filters_matching_terminology_regions_and_dedupe():
    SessionLocal, client, active_user, ids = _build_env()
    with SessionLocal() as db:
        rfq = RFQ(
            reference='RFQ-PF-001',
            buyer_org_id=ids['buyer_org'],
            title='Pumps and Filters Package',
            city='Khobar',
            response_deadline=datetime.now(UTC) + timedelta(days=7),
            status='published',
        )
        db.add(rfq)
        db.flush()
        # Different buyer terminology + custom material + exact material
        db.add_all([
            RFQItem(
                rfq_id=rfq.id,
                material_id=ids['m_pump'],
                main_category_id=ids['mech'],
                subcategory_id=ids['pumps'],
                description='Submersible Dewatering & Booster Pump Unit',
                specifications='5kW 3-phase',
                unit='NO.',
                quantity=Decimal('3'),
            ),
            RFQItem(
                rfq_id=rfq.id,
                material_id=None,  # custom material not in Material Master
                main_category_id=ids['plumb'],
                subcategory_id=ids['filters'],
                description='Industrial Multi-Media Filtration Cartridge',
                specifications='PN16 stainless housing',
                unit='NO.',
                quantity=Decimal('6'),
            ),
        ])
        db.flush()

        first = match_and_invite(db, rfq)
        db.commit()

        # s_pump_exact (Eastern Province covers Khobar + exact material),
        # s_filter_kw (Al Khobar alias + trade_keywords/ImportedSupplierTrade),
        # s_catalog (الخبر alias + verified PriceBookItem for m_pump)
        assert first['eligible_suppliers'] == 3
        assert first['new_invitations'] == 3

        invited_ids = set(db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq.id)).all())
        assert invited_ids == {ids['s_pump_exact'], ids['s_filter_kw'], ids['s_catalog']}
        assert ids['s_jeddah'] not in invited_ids
        assert ids['s_suspended'] not in invited_ids
        assert ids['s_elec'] not in invited_ids

        # Duplicate invitation prevention
        second = match_and_invite(db, rfq)
        db.commit()
        assert second['new_invitations'] == 0

        # Check persisted exclusion reasons
        matches = {m.supplier_org_id: m for m in db.scalars(select(RFQMatch).where(RFQMatch.rfq_id == rfq.id)).all()}
        assert matches[ids['s_jeddah']].status == 'location_mismatch'
        assert matches[ids['s_suspended']].status == 'suspended'
        rfq_id = rfq.id

    # Admin matching diagnostics verification
    active_user['id'] = ids['admin_user']
    resp = client.get(f'/api/v1/matching/admin/rfqs/{rfq_id}/diagnostics')
    assert resp.status_code == 200
    diag = resp.json()
    assert diag['rfq_items'] == 2
    assert diag['classified_items'] == 2
    assert diag['unclassified_items'] == 0
    assert diag['exact_matches'] >= 2
    assert diag['invitations_created'] == 3
    assert len(diag['eligible_suppliers']) == 3
    assert any(s['supplier_org_id'] == ids['s_jeddah'] and s['status'] == 'location_mismatch' for s in diag['excluded_suppliers'])
    assert any(s['supplier_org_id'] == ids['s_suspended'] and s['status'] == 'suspended' for s in diag['excluded_suppliers'])


def test_hybrid_classification_auto_ambiguous_arabic_and_gemini_fallback():
    SessionLocal, client, _active_user, ids = _build_env()

    # 1. Material code exact match -> no Gemini called
    with patch('app.modules.rfqs.material_matching._gemini_json_call') as mock_ai:
        res = client.post('/api/v1/rfqs/classify-material', json={'description': 'MAT-PUMP-100'})
        assert res.status_code == 200
        data = res.json()
        assert data['material_id'] == ids['m_pump']
        assert data['main_category_id'] == ids['mech']
        assert data['subcategory_id'] == ids['pumps']
        assert data['confidence'] == 1.0
        assert data['ai_used'] is False
        assert data['needs_confirmation'] is False
        mock_ai.assert_not_called()

    # 2. Arabic custom description -> deterministic category selection without Gemini
    with patch('app.modules.rfqs.material_matching._gemini_json_call') as mock_ai:
        res = client.post('/api/v1/rfqs/classify-material', json={'description': 'فلاتر مياه صناعية للمشروع'})
        assert res.status_code == 200
        data = res.json()
        assert data['main_category_id'] == ids['plumb']
        assert data['subcategory_id'] == ids['filters']
        assert data['ai_used'] is False
        mock_ai.assert_not_called()

    # 3. Unresolved description -> calls Gemini and validates against active DB categories
    with patch(
        'app.modules.rfqs.material_matching._gemini_json_call',
        return_value=({
            'classifications': [{
                'row_id': 'single',
                'main_category_id': ids['mech'],
                'subcategory_id': ids['pumps'],
                'confidence': 0.84,
                'explanation': 'Fluid transfer impeller assembly fits Pumps',
            }]
        }, False),
    ) as mock_ai:
        res = client.post('/api/v1/rfqs/classify-material', json={'description': 'Fluid transfer impeller assembly X900'})
        assert res.status_code == 200
        data = res.json()
        assert data['main_category_id'] == ids['mech']
        assert data['subcategory_id'] == ids['pumps']
        assert data['ai_used'] is True
        mock_ai.assert_called_once()

    # 4. Gemini returns fabricated/invalid category ID -> rejected, never creates arbitrary category!
    with patch(
        'app.modules.rfqs.material_matching._gemini_json_call',
        return_value=({
            'classifications': [{
                'row_id': 'single',
                'main_category_id': 'fake-cat-id',
                'subcategory_id': 'fake-sub-id',
                'confidence': 0.99,
                'explanation': 'Invented category',
            }]
        }, False),
    ):
        res = client.post('/api/v1/rfqs/classify-material', json={'description': 'Unmapped mystery gadget Z77'})
        assert res.status_code == 200
        data = res.json()
        assert data['main_category_id'] is None
        assert data['needs_confirmation'] is True

    # 5. Gemini unavailable -> graceful deterministic fallback
    with patch('app.modules.rfqs.material_matching._gemini_json_call', return_value=(None, True)):
        res = client.post('/api/v1/rfqs/classify-material', json={'description': 'Specialty hydro unit Q12'})
        assert res.status_code == 200
        data = res.json()
        assert data['fallback_used'] is True
        assert data['needs_confirmation'] is True


def test_ai_semantic_supplier_match_when_unresolved_and_fallback_when_gemini_fails():
    SessionLocal, _client, _active_user, ids = _build_env()
    with SessionLocal() as db:
        # Unresolved custom item with wording that has 0 lexical overlap with supplier keywords
        rfq = RFQ(
            reference='RFQ-AI-001',
            buyer_org_id=ids['buyer_org'],
            title='Specialized Impeller Unit',
            city='Khobar',
            response_deadline=datetime.now(UTC) + timedelta(days=5),
            status='published',
        )
        db.add(rfq)
        db.flush()
        item = RFQItem(
            rfq_id=rfq.id,
            material_id=None,
            main_category_id=ids['mech'],
            subcategory_id=None,
            description='Hydrodynamic rotary fluid mover',
            specifications='Industrial grade',
            unit='NO.',
            quantity=Decimal('2'),
        )
        db.add(item)
        db.flush()

        # First: Gemini unavailable -> deterministic still works (no crash)
        with patch('app.modules.matching.service.get_settings') as mock_settings, \
             patch('app.modules.rfqs.material_matching._gemini_json_call', return_value=(None, True)):
            mock_settings.return_value.gemini_api_key.get_secret_value.return_value = 'test-key'
            summary_fail = match_and_invite(db, rfq)
            db.commit()
            assert summary_fail['eligible_suppliers'] == 0

        # Second: Gemini returns valid active eligible supplier + invalid fabricated supplier ID
        _LOCAL_CACHE.clear()
        with patch('app.modules.matching.service.get_settings') as mock_settings, \
             patch(
                 'app.modules.rfqs.material_matching._gemini_json_call',
                 return_value=({
                     'matches': [
                         {'item_id': item.id, 'supplier_org_id': ids['s_pump_exact'], 'confidence': 0.82, 'explanation': 'Supplier specializes in pumping equipment'},
                         {'item_id': item.id, 'supplier_org_id': 'nonexistent-org', 'confidence': 0.95, 'explanation': 'Fabricated'},
                         {'item_id': item.id, 'supplier_org_id': ids['s_suspended'], 'confidence': 0.95, 'explanation': 'Suspended must be ignored'},
                     ]
                 }, False),
             ):
            mock_settings.return_value.gemini_api_key.get_secret_value.return_value = 'test-key'
            summary_ok = match_and_invite(db, rfq)
            db.commit()
            assert summary_ok['eligible_suppliers'] == 1
            invs = list(db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq.id)).all())
            assert invs == [ids['s_pump_exact']]


def test_excel_bulk_upload_auto_classifies_custom_pumps_and_filters(tmp_path, monkeypatch):
    SessionLocal, client, _active_user, ids = _build_env()
    monkeypatch.setenv('IMPORT_STORAGE_DIR', str(tmp_path))

    wb = Workbook()
    ws = wb.active
    ws.append(['Description / Specification', 'Unit', 'Quantity'])
    ws.append(['Centrifugal Booster Pump 5kW', 'NO.', 2])
    ws.append(['Custom Stainless Water Filter Cartridge', 'NO.', 5])
    buf = BytesIO()
    wb.save(buf)

    upload = client.post(
        '/api/v1/rfqs/imports',
        data={'org_id': ids['buyer_org'], 'draft_key': 'bulk-pf-test', 'ai_enabled': 'false'},
        files={'file': ('pumps-filters.xlsx', buf.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert upload.status_code == 202
    batch_id = upload.json()['id']

    with SessionLocal() as db:
        assert process_rfq_import_jobs(db) == batch_id  # parse stage
        assert process_rfq_import_jobs(db) == batch_id  # match + classify stage

    job = client.get(f'/api/v1/rfqs/imports/{batch_id}').json()
    assert job['status'] == 'preview'
    assert len(job['rows']) == 2
    row_pump, row_filter = job['rows']
    assert row_pump['material_id'] == ids['m_pump']
    assert row_pump['main_category_id'] == ids['mech']
    assert row_pump['subcategory_id'] == ids['pumps']
    # Custom filter row gets automatic category suggestion even without Material Master match!
    assert row_filter['material_id'] is None
    assert row_filter['main_category_id'] == ids['plumb']
    assert row_filter['subcategory_id'] == ids['filters']


def test_grouped_rfq_excel_and_pdf_exports_and_multiple_items_same_subcategory():
    SessionLocal, client, active_user, ids = _build_env()
    with SessionLocal() as db:
        rfq = RFQ(
            reference='RFQ-EXP-001',
            buyer_org_id=ids['buyer_org'],
            title='Grouped Export RFQ',
            city='Khobar',
            response_deadline=datetime.now(UTC) + timedelta(days=7),
            status='published',
        )
        db.add(rfq)
        db.flush()
        # Multiple materials under the same subcategory + another category
        i1 = RFQItem(rfq_id=rfq.id, material_id=ids['m_pump'], main_category_id=ids['mech'], subcategory_id=ids['pumps'], description='Booster Pump 5kW', specifications='3-phase', unit='NO.', quantity=Decimal('2'))
        i2 = RFQItem(rfq_id=rfq.id, material_id=None, main_category_id=ids['mech'], subcategory_id=ids['pumps'], description='Submersible Sump Pump 3kW', specifications='Stainless', unit='NO.', quantity=Decimal('4'))
        i3 = RFQItem(rfq_id=rfq.id, material_id=ids['m_filter'], main_category_id=ids['plumb'], subcategory_id=ids['filters'], description='Sand Media Water Filter DN80', specifications='PN16', unit='NO.', quantity=Decimal('3'))
        db.add_all([i1, i2, i3])
        db.commit()
        rfq_id = rfq.id

    # Buyer can export grouped Excel
    xlsx_resp = client.get(f'/api/v1/rfqs/{rfq_id}/export.xlsx')
    assert xlsx_resp.status_code == 200
    assert 'spreadsheetml' in xlsx_resp.headers['content-type']
    wb = load_workbook(BytesIO(xlsx_resp.content))
    rows = [tuple(cell.value for cell in row) for row in wb.active.iter_rows()]
    flat_col0 = [str(r[0] or '') for r in rows]
    # Main Category: Mechanical appears ONCE for both pump items!
    assert flat_col0.count('Main Category: Mechanical') == 1
    assert flat_col0.count('Main Category: Plumbing & Water') == 1
    assert any('Subcategory: Pumps' in v for v in flat_col0)

    # Buyer can export grouped PDF
    pdf_resp = client.get(f'/api/v1/rfqs/{rfq_id}/export.pdf')
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers['content-type'] == 'application/pdf'
    assert pdf_resp.content.startswith(b'%PDF-')

    # Uninvited supplier gets 404
    active_user['id'] = ids['supplier_user']
    assert client.get(f'/api/v1/rfqs/{rfq_id}/export.pdf').status_code == 404
