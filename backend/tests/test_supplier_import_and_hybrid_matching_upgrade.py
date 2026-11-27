"""Comprehensive tests for the Procumaterial supplier bulk import, review report,
cancellation, brand capability matching, contact routing, and hybrid material matching upgrade."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    Capability,
    Category,
    Contact,
    ContactCapability,
    ImportedSupplierTrade,
    Material,
    Organization,
    RFQ,
    RFQItem,
    User,
)
from app.modules.imports import bulk, supplier_jobs as jobs
from app.modules.materials.router import router as materials_router
from app.modules.materials.service import create_material_record
from app.modules.notifications import recipients
from app.security import admin_user, current_user


def _build_xlsx(headers: list[str], rows: list[list]) -> bytes:
    book = Workbook()
    ws = book.active
    ws.title = 'Matched Suppliers'
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    book.save(buf)
    return buf.getvalue()


@pytest.fixture()
def env():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as db:
        admin = User(username='upgrade-admin', email='upgrade-admin@example.com', phone='+966500000001', password_hash='x', is_admin=True, is_active=True)
        db.add(admin)
        db.flush()

        # 2-level hierarchy: Main Category -> Subcategory (Discipline is on Material!)
        elec_main = Category(code='ELEC-LC', name_en='Low Current System', normalized_name='low current system', active=True)
        elec_self = Category(code='ELEC-MAIN', name_en='Electrical', normalized_name='electrical', active=True)
        mech_main = Category(code='MECH-PLUMB', name_en='Plumbing', normalized_name='plumbing', active=True)
        db.add_all([elec_main, elec_self, mech_main])
        db.flush()

        sub_2nd = Category(code='ELEC-LC-2', name_en='Second Fix', normalized_name='second fix', parent_id=elec_main.id, active=True)
        sub_3rd = Category(code='ELEC-LC-3', name_en='Third Fix', normalized_name='third fix', parent_id=elec_main.id, active=True)
        sub_elec_2nd = Category(code='ELEC-2', name_en='Second Fix', normalized_name='second fix', parent_id=elec_self.id, active=True)
        sub_pipes = Category(code='MECH-PIPES', name_en='Pipes', normalized_name='pipes', parent_id=mech_main.id, active=True)
        db.add_all([sub_2nd, sub_3rd, sub_elec_2nd, sub_pipes])
        db.flush()

        mat_cat6, _ = create_material_record(db, actor=admin, main_category=elec_main, subcategory=sub_2nd, description='Cat6a Socket', unit='No.', source_unit='No.', discipline='Electrical', create_unknown_unit=True)
        mat_panel, _ = create_material_record(db, actor=admin, main_category=elec_main, subcategory=sub_3rd, description='LED Panel', unit='No.', source_unit='No.', discipline='Electrical', create_unknown_unit=True)
        mat_pvc, _ = create_material_record(db, actor=admin, main_category=mech_main, subcategory=sub_pipes, description='PVC Pipe 20mm', unit='M', source_unit='M', discipline='Mechanical', create_unknown_unit=True)
        mat_pvc110, _ = create_material_record(db, actor=admin, main_category=mech_main, subcategory=sub_pipes, description='PVC Pipe 110mm', unit='M', source_unit='M', discipline='Mechanical', create_unknown_unit=True)
        mat_upvc, _ = create_material_record(db, actor=admin, main_category=mech_main, subcategory=sub_pipes, description='UPVC Pipe', unit='M', source_unit='M', discipline='Mechanical', create_unknown_unit=True)
        db.commit()
        ids = {
            'admin_id': admin.id,
            'elec_main': elec_main.id,
            'mech_main': mech_main.id,
            'sub_2nd': sub_2nd.id,
            'sub_3rd': sub_3rd.id,
            'sub_pipes': sub_pipes.id,
            'mat_cat6': mat_cat6.id,
            'mat_panel': mat_panel.id,
            'mat_pvc': mat_pvc.id,
            'mat_pvc110': mat_pvc110.id,
            'mat_upvc': mat_upvc.id,
        }

    app = FastAPI()
    app.include_router(bulk.router, prefix='/api/v1/imports')
    app.include_router(jobs.router, prefix='/api/v1/imports')
    app.include_router(materials_router, prefix='/api/v1')

    def _override_db():
        with SessionLocal() as db:
            yield db

    def _override_user():
        with SessionLocal() as db:
            return db.get(User, ids['admin_id'])

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[admin_user] = _override_user
    app.dependency_overrides[current_user] = _override_user
    client = TestClient(app)
    return client, SessionLocal, ids


def _drain(SessionLocal):
    for _ in range(20):
        with SessionLocal() as db:
            if not jobs.process_supplier_jobs(db):
                break


def test_download_17_column_supplier_template(env):
    client, _, _ = env
    res = client.get('/api/v1/imports/bulk/suppliers/template')
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    assert 'Supplier Import' in wb.sheetnames
    assert 'Instructions' in wb.sheetnames
    ws = wb['Supplier Import']
    headers = [cell.value for cell in ws[1]]
    assert headers == bulk.SUPPLIER_TEMPLATE_HEADERS
    assert len(headers) == 17
    assert 'Subcategory' in headers
    assert ws.freeze_panes == 'A2'
    assert ws.auto_filter.ref is not None


def test_preview_start_multi_row_brands_idempotency_and_review_report(env):
    client, SessionLocal, ids = env
    rows = [
        ['Alpha Supply Co.', 'Saudi Arabia', 'Riyadh', '', 'https://alpha.sa', '', '', 'MEP supplier', 'Ali', 'Electrical Sales', 'ali@alpha.sa', '966501112233', 'Electrical', 'Low Current System', 'Second Fix', 'Cat6a Socket; Unmatched Cable X', 'ABB; Siemens'],
        ['Alpha Supply Co.', 'Saudi Arabia', 'Riyadh', '', '', '', '', '', 'Ali', 'Electrical Sales', 'ali@alpha.sa', '966501112233', 'Electrical', 'Low Current System', 'Third Fix', 'LED Panel', ''],
        ['Alpha Supply Co.', 'Saudi Arabia', 'Riyadh', '', '', '', '', '', 'Sara', 'Plumbing Sales', 'sara@alpha.sa', '966509998877', 'Mechanical', 'Plumbing', 'Pipes', 'PVC Pipe 20mm; UPVC Pipe', 'Cosmoplast'],
        ['No Contact Co', 'Saudi Arabia', 'Jeddah', '', '', '', '', '', '', '', '', '', 'Mechanical', 'Plumbing', 'Pipes', 'PVC Pipe 20mm', ''],
        ['Bad Disc Co', 'Saudi Arabia', 'Dammam', '', '', '', '', '', '', '', '', '', 'Nuclear', 'Plumbing', 'Pipes', 'PVC Pipe 20mm', ''],
    ]
    data = _build_xlsx(bulk.SUPPLIER_TEMPLATE_HEADERS, rows)

    # 1. Preview must NOT mutate production organizations
    prev_res = client.post(
        '/api/v1/imports/bulk/suppliers/jobs/preview',
        files={'file': ('suppliers.xlsx', data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert prev_res.status_code == 202
    prev = prev_res.json()
    assert prev['status'] == 'preview_ready'
    assert prev['total_rows'] == 5
    assert prev['total_supplier_records_identified'] == 3
    assert prev['new_suppliers_estimated'] == 3
    assert prev['existing_suppliers_estimated'] == 0
    assert prev['existing_materials_matched'] == 4
    assert prev['unresolved_material_mappings'] == 1
    assert prev['brands_detected'] == 3
    assert prev['review_required_rows'] == 1

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0

    # 2. Confirm and process
    start_res = client.post(f"/api/v1/imports/bulk/suppliers/jobs/{prev['batch_id']}/start")
    assert start_res.status_code == 202
    assert start_res.json()['status'] == 'queued'
    _drain(SessionLocal)

    done = client.get(f"/api/v1/imports/bulk/suppliers/jobs/{prev['batch_id']}").json()
    assert done['status'] == 'completed_with_errors'
    assert done['new_suppliers_saved'] == 3
    assert done['contacts_created'] == 2
    assert done['capabilities_saved'] == 4
    assert done['material_mappings_created'] >= 4
    assert done['pending_material_mappings'] == 1
    assert done['pending_category_mappings'] == 1

    with SessionLocal() as db:
        alpha = db.scalar(select(Organization).where(Organization.name == 'Alpha Supply Co.'))
        assert alpha is not None
        alpha_caps = db.scalars(select(Capability).where(Capability.org_id == alpha.id)).all()
        assert len(alpha_caps) == 3
        cap_by_cat = {c.category_id: c for c in alpha_caps}
        assert sorted(cap_by_cat[ids['sub_2nd']].brands) == ['ABB', 'Siemens']
        assert cap_by_cat[ids['sub_3rd']].brands == []
        assert cap_by_cat[ids['sub_pipes']].brands == ['Cosmoplast']
        assert ids['mat_cat6'] in cap_by_cat[ids['sub_2nd']].material_ids
        assert ids['mat_pvc'] in cap_by_cat[ids['sub_pipes']].material_ids
        assert ids['mat_upvc'] in cap_by_cat[ids['sub_pipes']].material_ids
        # No Contact Co with blank brands and blank contacts is saved cleanly!
        no_contact = db.scalar(select(Organization).where(Organization.name == 'No Contact Co'))
        assert no_contact is not None
        # Unmatched material was NOT auto-created in Material Master
        assert db.scalar(select(Material).where(Material.name_en == 'Unmatched Cable X')) is None
        pending_trade = db.scalar(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id == alpha.id, ImportedSupplierTrade.mapping_status == 'material_pending_review'))
        assert pending_trade is not None
        source_products = {trade.raw_products for trade in db.scalars(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id == alpha.id))}
        assert {'Cat6a Socket; Unmatched Cable X', 'LED Panel', 'PVC Pipe 20mm; UPVC Pipe'} <= source_products
        assert alpha.description == 'MEP supplier'

    # 3. Download Review Report contains 17 columns + 5 audit columns
    rep = client.get(f"/api/v1/imports/bulk/suppliers/jobs/{prev['batch_id']}/review-report")
    assert rep.status_code == 200
    rwb = load_workbook(BytesIO(rep.content))
    rws = rwb.active
    rheaders = [c.value for c in rws[1]]
    assert rheaders[:17] == bulk.SUPPLIER_TEMPLATE_HEADERS
    assert rheaders[17:] == ['Source Row', 'Import Status', 'Issue Field', 'Issue Code', 'Issue Reason']
    assert rws.max_row == 3  # header + 2 review rows (Unmatched Cable X + Nuclear discipline)

    # 4. Idempotent reimport creates ZERO duplicates
    re_prev = client.post(
        '/api/v1/imports/bulk/suppliers/jobs/preview',
        files={'file': ('suppliers.xlsx', data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    ).json()
    assert re_prev['existing_suppliers_estimated'] == 3
    assert re_prev['new_suppliers_estimated'] == 0
    client.post(f"/api/v1/imports/bulk/suppliers/jobs/{re_prev['batch_id']}/start")
    _drain(SessionLocal)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 3
        assert db.scalar(select(func.count()).select_from(Contact)) == 2
        assert db.scalar(select(func.count()).select_from(Capability)) == 4
        assert db.scalar(select(func.count()).select_from(ImportedSupplierTrade)) == 5


def test_job_cancellation_and_history(env):
    client, SessionLocal, _ = env
    data = _build_xlsx(bulk.SUPPLIER_TEMPLATE_HEADERS, [
        ['Cancel Co', 'Saudi Arabia', 'Riyadh', '', '', '', '', '', '', '', '', '', 'Mechanical', 'Plumbing', 'Pipes', 'PVC Pipe 20mm', ''],
    ])
    prev = client.post(
        '/api/v1/imports/bulk/suppliers/jobs/preview',
        files={'file': ('cancel.xlsx', data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    ).json()
    cancelled = client.post(f"/api/v1/imports/bulk/suppliers/jobs/{prev['batch_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()['status'] == 'cancelled'
    _drain(SessionLocal)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0
    hist = client.get('/api/v1/imports/bulk/suppliers/jobs?limit=5')
    assert hist.status_code == 200
    assert any(item['batch_id'] == prev['batch_id'] and item['status'] == 'cancelled' for item in hist.json())


def test_material_match_endpoints_and_spec_conflict(env, monkeypatch):
    client, _, ids = env
    # Exact match without Gemini
    r1 = client.post('/api/v1/materials/match', json={'description': 'PVC Pipe 20mm', 'ai_enabled': False})
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1['material_id'] == ids['mat_pvc']
    assert body1['confidence_band'] == 'high'
    assert body1['ai_used'] is False

    # Bulk match with specification conflict (20mm vs 110mm must not cross-match as high confidence)
    r2 = client.post('/api/v1/materials/match/bulk', json={
        'ai_enabled': False,
        'items': [
            {'description': 'PVC Pipe 20mm'},
            {'description': 'PVC Pipe 110mm'},
            {'description': 'PVC Pipe 75mm'},
        ],
    })
    assert r2.status_code == 200
    results = r2.json()['results']
    assert results[0]['material_id'] == ids['mat_pvc']
    assert results[1]['material_id'] == ids['mat_pvc110']
    assert results[2]['material_id'] is None or results[2]['confidence_band'] != 'high'


def test_contact_specific_routing_by_main_category_and_subcategory(env):
    _, SessionLocal, ids = env
    with SessionLocal() as db:
        buyer_org = Organization(name='Buyer Co', name_normalized='buyer co', is_buyer=True, is_supplier=False, is_active=True, verified_status='verified')
        sup_org = Organization(name='Multi Contact Supplier', name_normalized='multi contact supplier', is_buyer=False, is_supplier=True, is_active=True, verified_status='verified')
        db.add_all([buyer_org, sup_org])
        db.flush()

        c_elec = Contact(org_id=sup_org.id, name='Elec Contact', email='elec@sup.sa', is_primary=False)
        c_mech = Contact(org_id=sup_org.id, name='Mech Contact', email='mech@sup.sa', is_primary=False)
        db.add_all([c_elec, c_mech])
        db.flush()

        cap_elec = Capability(org_id=sup_org.id, category_id=ids['sub_2nd'], city='Riyadh', material_ids=[ids['mat_cat6']], confirmed=True, accepts_rfqs=True, eligibility_status='enabled')
        # The mechanical capability states the product (strict matching needs product evidence).
        cap_mech = Capability(org_id=sup_org.id, category_id=ids['mech_main'], city='Riyadh', material_ids=[], confirmed=True, accepts_rfqs=True, eligibility_status='enabled', trade_keywords='Plumbing valves')
        db.add_all([cap_elec, cap_mech])
        db.flush()

        db.add(ContactCapability(contact_id=c_elec.id, capability_id=cap_elec.id, is_primary=True, material_ids=[ids['mat_cat6']]))
        db.add(ContactCapability(contact_id=c_mech.id, capability_id=cap_mech.id, is_primary=True, material_ids=None))

        from datetime import UTC, datetime, timedelta
        rfq = RFQ(reference='RFQ-ROUTE-1', title='Plumbing RFQ', buyer_org_id=buyer_org.id, city='Riyadh', status='published', response_deadline=datetime.now(UTC) + timedelta(days=7))
        db.add(rfq)
        db.flush()
        db.add(RFQItem(rfq_id=rfq.id, main_category_id=ids['mech_main'], subcategory_id=None, description='Plumbing valve', specifications='', unit='No.', quantity=10))
        db.commit()

        resolved = recipients.resolve(db, sup_org.id, rfq.id)
        emails = [r.get('destination') for r in resolved['email'] if r.get('destination')]
        assert emails == ['mech@sup.sa']


def test_real_supplier_matched_workbook_slice_if_present(env):
    workbook_path = Path(r'C:\Users\Abubakkar Sajid\Downloads\Supplier_Matched.xlsx')
    if not workbook_path.exists():
        pytest.skip('Supplier_Matched.xlsx not present in Downloads')
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    headers = list(next(it))
    sample_rows = [list(next(it)) for _ in range(50)]
    wb.close()

    client, SessionLocal, _ = env
    sliced = _build_xlsx(headers, sample_rows)
    prev = client.post(
        '/api/v1/imports/bulk/suppliers/jobs/preview',
        files={'file': ('Supplier_Matched_slice.xlsx', sliced, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert prev.status_code == 202
    body = prev.json()
    assert body['total_rows'] == 50
    assert body['total_supplier_records_identified'] >= 1
    assert body['invalid_rows'] == 0
    start = client.post(f"/api/v1/imports/bulk/suppliers/jobs/{body['batch_id']}/start")
    assert start.status_code == 202
    _drain(SessionLocal)
    done = client.get(f"/api/v1/imports/bulk/suppliers/jobs/{body['batch_id']}").json()
    assert done['status'] in {'completed', 'completed_with_errors'}
    assert done['failed'] == 0
    assert done['new_suppliers_saved'] == body['total_supplier_records_identified']
