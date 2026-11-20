"""Comprehensive tests for Supplier Private Price Book Bulk Excel/CSV Import."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.import_storage import LocalImportStorage
from app.main import app as asgi_app
from app.models import (
    Category,
    ImportBatch,
    ImportedRow,
    Invitation,
    Material,
    MeasurementUnit,
    Member,
    Organization,
    PriceBookItem,
    Quotation,
    QuoteLine,
    RFQ,
    RFQItem,
    User,
)
from app.modules.materials.service import normalize_text
from app.modules.pricebooks import imports as pricebook_imports
from app.modules.pricebooks.imports import TEMPLATE_HEADERS
from app.modules.pricebooks.worker import process_pricing_jobs
from app.modules.quotations.automation import build_draft, prepare_authorized_quote
from app.security import password_hash, token_for


def _fastapi_app():
    target = asgi_app
    while not hasattr(target, 'dependency_overrides'):
        target = target.app
    return target


def _make_xlsx(rows: list[list[object]], headers: list[str] | None = None, sheet_name: str = 'Price Book Import') -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers or TEMPLATE_HEADERS)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    storage = LocalImportStorage(tmp_path / 'imports')
    monkeypatch.setattr(pricebook_imports, 'get_import_storage', lambda: storage)

    fastapi_app = _fastapi_app()

    def override_db():
        with SessionTesting() as db:
            yield db

    fastapi_app.dependency_overrides[get_db] = override_db

    with SessionTesting() as db:
        # Measurement units
        for idx, (code, name) in enumerate([('BAG', 'Bag'), ('TON', 'Ton'), ('PCS', 'Piece'), ('M3', 'Cubic Meter')], 1):
            db.add(MeasurementUnit(code=code, name_en=name, active=True, sort_order=idx))

        # Categories
        main = Category(code='CIVIL', name_en='Civil Materials', name_ar='مواد مدنية', normalized_name='civil materials', parent_id=None, active=True)
        db.add(main)
        db.flush()
        sub = Category(code='CEMENT', name_en='Cement & Concrete', name_ar='الأسمنت والخرسانة', normalized_name='cement & concrete', parent_id=main.id, active=True)
        db.add(sub)
        db.flush()

        # Users & Organizations
        sup1_user = User(username='sup1', phone='+966500000001', phone_e164='+966500000001', email='sup1@example.com', password_hash=password_hash('StrongPass123!'), is_active=True, phone_verified=True, approval_status='approved')
        sup2_user = User(username='sup2', phone='+966500000002', phone_e164='+966500000002', email='sup2@example.com', password_hash=password_hash('StrongPass123!'), is_active=True, phone_verified=True, approval_status='approved')
        buyer_user = User(username='buyer1', phone='+966500000003', phone_e164='+966500000003', email='buyer@example.com', password_hash=password_hash('StrongPass123!'), is_active=True, phone_verified=True, approval_status='approved')
        db.add_all([sup1_user, sup2_user, buyer_user])
        db.flush()

        sup1_org = Organization(name='Riyadh Materials Co', name_normalized='riyadh materials co', city='Riyadh', is_supplier=True, is_buyer=False, verified_status='verified', is_active=True)
        sup2_org = Organization(name='Jeddah Supply Co', name_normalized='jeddah supply co', city='Jeddah', is_supplier=True, is_buyer=False, verified_status='verified', is_active=True)
        buyer_org = Organization(name='BuildCo Buyer', name_normalized='buildco buyer', city='Riyadh', is_supplier=False, is_buyer=True, verified_status='verified', is_active=True)
        db.add_all([sup1_org, sup2_org, buyer_org])
        db.flush()

        db.add_all([
            Member(user_id=sup1_user.id, organization_id=sup1_org.id, role='owner'),
            Member(user_id=sup2_user.id, organization_id=sup2_org.id, role='owner'),
            Member(user_id=buyer_user.id, organization_id=buyer_org.id, role='owner'),
        ])

        # Base materials
        m1 = Material(
            code='MAT-CEM-001',
            category_id=sub.id,
            main_category_id=main.id,
            subcategory_id=sub.id,
            name_en='Ordinary Portland Cement 50kg',
            normalized_description=normalize_text('Ordinary Portland Cement 50kg'),
            unit='BAG',
            active=True,
        )
        m2 = Material(
            code='MAT-STL-002',
            category_id=sub.id,
            main_category_id=main.id,
            subcategory_id=sub.id,
            name_en='Deformed Steel Rebar 16mm',
            normalized_description=normalize_text('Deformed Steel Rebar 16mm'),
            unit='TON',
            active=True,
        )
        db.add_all([m1, m2])
        db.commit()

        ctx = {
            'Session': SessionTesting,
            'client': TestClient(asgi_app),
            'sup1_headers': {'Authorization': f'Bearer {token_for(sup1_user)}'},
            'sup2_headers': {'Authorization': f'Bearer {token_for(sup2_user)}'},
            'buyer_headers': {'Authorization': f'Bearer {token_for(buyer_user)}'},
            'sup1_org': sup1_org.id,
            'sup2_org': sup2_org.id,
            'buyer_org': buyer_org.id,
            'main': main,
            'sub': sub,
            'm1': m1,
            'm2': m2,
        }

    try:
        yield ctx
    finally:
        fastapi_app.dependency_overrides.clear()


def _seed_materials(SessionTesting, main_id: str, sub_id: str, count: int, prefix: str = 'BULK') -> list[tuple[str, str]]:
    created = []
    with SessionTesting() as db:
        objs = []
        for i in range(1, count + 1):
            code = f'{prefix}-{i:04d}'
            name = f'{prefix} Construction Material {i:04d}'
            objs.append(
                Material(
                    code=code,
                    category_id=sub_id,
                    main_category_id=main_id,
                    subcategory_id=sub_id,
                    name_en=name,
                    normalized_description=normalize_text(name),
                    unit='PCS',
                    active=True,
                )
            )
            created.append((code, name))
        db.add_all(objs)
        db.commit()
    return created


def test_download_template_structure_dropdowns_and_no_fake_ids(env):
    resp = env['client'].get(
        f"/api/v1/pricebooks/{env['sup1_org']}/import-template",
        headers=env['sup1_headers'],
    )
    assert resp.status_code == 200
    assert 'spreadsheetml' in resp.headers['content-type']

    wb = load_workbook(BytesIO(resp.content))
    assert 'Price Book Import' in wb.sheetnames
    assert 'Instructions & Examples' in wb.sheetnames
    ws = wb['Price Book Import']
    headers = [ws.cell(row=1, column=i).value for i in range(1, 13)]
    assert headers == TEMPLATE_HEADERS
    # Sheet 1 should be clean for supplier entry (no prefilled fake rows)
    assert ws.cell(row=2, column=3).value is None
    # Dropdown data validations exist
    assert len(ws.data_validations.dataValidation) >= 2

    # Sheet 2 instructions & examples must not contain fake production material IDs
    inst = wb['Instructions & Examples']
    all_text = ' '.join(str(cell.value or '') for row in inst.iter_rows() for cell in row)
    assert 'Procumaterial' in all_text
    assert 'MAT-CEM-001' not in all_text


def test_upload_30_records_preview_confirm_and_export(env):
    mats = _seed_materials(env['Session'], env['main'].id, env['sub'].id, 30, prefix='B30')
    rows = [
        ['Civil Materials', 'Cement & Concrete', name, code, 'PCS', 'BrandX', f'M-{i}', 45.50 + i, 10, 5, 'Active', f'Note {i}']
        for i, (code, name) in enumerate(mats, 1)
    ]
    xlsx = _make_xlsx(rows)
    up = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup1_headers'],
        files={'file': ('rates30.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert up.status_code == 200
    batch_id = up.json()['batch_id']

    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)

    preview = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup1_headers']).json()
    assert preview['status'] == 'preview'
    assert preview['rows'] == 30
    assert preview['valid'] == 30
    assert preview['new_count'] == 30
    assert preview['errors'] == 0

    conf = env['client'].post(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/confirm", headers=env['sup1_headers'])
    assert conf.status_code == 200
    assert conf.json()['status'] == 'queued'

    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)

    done = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup1_headers']).json()
    assert done['status'] == 'completed'
    assert done['created'] == 30
    assert done['saved'] == 30
    assert done['progress_percent'] == 100
    assert done['current_job'] == 1
    assert done['jobs_total'] == 1
    assert done['current_batch'] == 1
    assert done['batches_in_job'] == 1

    # Verify export downloads all 30 saved items
    exp = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/export", headers=env['sup1_headers'])
    assert exp.status_code == 200
    wb = load_workbook(BytesIO(exp.content))
    ws = wb.active
    assert ws.max_row == 31  # 1 header + 30 rows


def test_upload_300_records_single_job_ten_batches(env):
    mats = _seed_materials(env['Session'], env['main'].id, env['sub'].id, 300, prefix='B300')
    rows = [
        ['Civil Materials', 'Cement & Concrete', name, code, 'PCS', 'SABIC', f'MOD-{i}', 100 + (i % 50), 5, 3, 'Active', '']
        for i, (code, name) in enumerate(mats, 1)
    ]
    xlsx = _make_xlsx(rows)
    up = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup1_headers'],
        files={'file': ('rates300.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    batch_id = up.json()['batch_id']

    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)
    env['client'].post(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/confirm", headers=env['sup1_headers'])

    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)

    done = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup1_headers']).json()
    assert done['status'] == 'completed'
    assert done['created'] == 300
    assert done['jobs_total'] == 1
    assert done['batches_in_job'] == 10
    assert done['progress_percent'] == 100


def test_upload_1000_records_automatic_multi_job_continuation(env):
    mats = _seed_materials(env['Session'], env['main'].id, env['sub'].id, 1000, prefix='B1K')
    rows = [
        ['Civil Materials', 'Cement & Concrete', name, code, 'PCS', 'BrandA', '', 25.00 + (i % 10), '', 7, 'Active', '']
        for i, (code, name) in enumerate(mats, 1)
    ]
    xlsx = _make_xlsx(rows)
    up = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup1_headers'],
        files={'file': ('rates1000.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    batch_id = up.json()['batch_id']

    # Parse
    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)
    env['client'].post(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/confirm", headers=env['sup1_headers'])

    # Execute job by job: 300 -> 600 -> 900 -> 1000
    expected_progress = [(300, 'applying'), (600, 'applying'), (900, 'applying'), (1000, 'completed')]
    for expected_saved, expected_status in expected_progress:
        with env['Session']() as db:
            process_pricing_jobs(db, limit=1)
        st = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup1_headers']).json()
        assert st['created'] == expected_saved
        assert st['completed_rows'] == expected_saved
        assert st['status'] == expected_status
        assert st['jobs_total'] == 4

    with env['Session']() as db:
        total_saved = db.scalar(select(func.count(PriceBookItem.id)).where(PriceBookItem.org_id == env['sup1_org']))
        assert total_saved == 1000


def test_duplicates_updates_invalid_prices_units_unknown_materials_and_failed_export(env):
    # Pre-seed an existing rate for m1 so we test update detection
    with env['Session']() as db:
        db.add(
            PriceBookItem(
                org_id=env['sup1_org'],
                material_id=env['m1'].id,
                main_category_id=env['main'].id,
                subcategory_id=env['sub'].id,
                supplier_description=env['m1'].name_en,
                unit='BAG',
                unit_price=Decimal('15.00'),
                lead_days=5,
                active=True,
                currency='SAR',
                price_basis='supply',
            )
        )
        db.commit()
        initial_material_count = db.scalar(select(func.count(Material.id)))

    rows = [
        # Row 2: Valid update to existing m1 (matched by description + category + unit, no code)
        ['Civil Materials', 'Cement & Concrete', 'Ordinary Portland Cement 50kg', '', 'BAG', 'Saudi Cement', 'OPC-I', '19.75', 50, 3, 'Active', 'Updated rate'],
        # Row 3: Duplicate of m1 in same file
        ['Civil Materials', 'Cement & Concrete', 'Ordinary Portland Cement 50kg', 'MAT-CEM-001', 'BAG', 'Saudi Cement', 'OPC-I', '20.00', 50, 3, 'Active', 'Dup row'],
        # Row 4: Valid new rate for m2 (matched by code)
        ['Civil Materials', 'Cement & Concrete', 'Deformed Steel Rebar 16mm', 'MAT-STL-002', 'TON', 'Hadeed', 'G60', '2750.00', 2, 7, 'Active', 'Steel'],
        # Row 5: Invalid price (-10)
        ['Civil Materials', 'Cement & Concrete', 'Deformed Steel Rebar 16mm', 'MAT-STL-002', 'TON', '', '', '-10', '', 7, 'Active', ''],
        # Row 6: Invalid unit (KG instead of TON)
        ['Civil Materials', 'Cement & Concrete', 'Deformed Steel Rebar 16mm', 'MAT-STL-002', 'KG', '', '', '2750', '', 7, 'Active', ''],
        # Row 7: Unknown material (must NEVER create global Material Master record)
        ['Civil Materials', 'Cement & Concrete', 'Nonexistent Alien Material XYZ', '', 'PCS', '', '', '99.00', '', 7, 'Active', ''],
    ]
    xlsx = _make_xlsx(rows)
    up = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup1_headers'],
        files={'file': ('mixed.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    batch_id = up.json()['batch_id']
    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)

    preview = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup1_headers']).json()
    assert preview['status'] == 'preview'
    assert preview['rows'] == 6
    assert preview['valid'] == 2
    assert preview['new_count'] == 1
    assert preview['update_count'] == 1
    assert preview['duplicate_count'] == 1
    assert preview['invalid_count'] == 2
    assert preview['unmatched_count'] == 1
    assert preview['errors'] == 4

    # Confirm imports ONLY the 2 valid rows and skips the 4 issue rows
    conf = env['client'].post(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/confirm", headers=env['sup1_headers'])
    assert conf.status_code == 200
    with env['Session']() as db:
        process_pricing_jobs(db, limit=2)

    done = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup1_headers']).json()
    assert done['status'] == 'completed_with_errors'
    assert done['created'] == 1
    assert done['updated_count'] == 1
    assert done['skipped_count'] == 4

    # Verify NO global Material Master record was created & existing rate was updated
    with env['Session']() as db:
        assert db.scalar(select(func.count(Material.id))) == initial_material_count
        updated_m1 = db.scalar(select(PriceBookItem).where(PriceBookItem.org_id == env['sup1_org'], PriceBookItem.material_id == env['m1'].id))
        assert updated_m1.unit_price == Decimal('19.75')
        assert updated_m1.brand == 'Saudi Cement'
        assert updated_m1.model == 'OPC-I'
        assert updated_m1.min_order_quantity == Decimal('50.000')
        assert updated_m1.remarks == 'Updated rate'

    # Download failed rows workbook
    err_resp = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/errors.xlsx", headers=env['sup1_headers'])
    assert err_resp.status_code == 200
    err_wb = load_workbook(BytesIO(err_resp.content))
    err_ws = err_wb.active
    assert err_ws.max_row == 5  # 1 header + 4 issue rows


def test_tenant_isolation_and_unauthorized_imports(env):
    xlsx = _make_xlsx([['Civil Materials', 'Cement & Concrete', 'Ordinary Portland Cement 50kg', 'MAT-CEM-001', 'BAG', '', '', '18.00', '', 3, 'Active', '']])

    # Unauthenticated request -> 401
    unauth = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        files={'file': ('r.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert unauth.status_code == 401

    # Wrong supplier organization -> 404
    wrong_org = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup2_headers'],
        files={'file': ('r.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert wrong_org.status_code == 404

    # Buyer attempting to import or read supplier price book -> 404/422
    buyer_upload = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['buyer_headers'],
        files={'file': ('r.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert buyer_upload.status_code in (403, 404)

    buyer_own = env['client'].post(
        f"/api/v1/pricebooks/{env['buyer_org']}/bulk-import",
        headers=env['buyer_headers'],
        files={'file': ('r.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert buyer_own.status_code == 422

    # Upload by sup1 cannot be read/confirmed/exported by sup2
    up = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup1_headers'],
        files={'file': ('r.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    batch_id = up.json()['batch_id']
    assert env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup2_headers']).status_code == 404
    assert env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/export", headers=env['sup2_headers']).status_code == 404


def test_interrupted_import_and_safe_retry(env):
    mats = _seed_materials(env['Session'], env['main'].id, env['sub'].id, 350, prefix='RETRY')
    rows = [
        ['Civil Materials', 'Cement & Concrete', name, code, 'PCS', '', '', 50.00, '', 5, 'Active', '']
        for code, name in mats
    ]
    xlsx = _make_xlsx(rows)
    up = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup1_headers'],
        files={'file': ('retry350.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    batch_id = up.json()['batch_id']

    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)
    env['client'].post(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/confirm", headers=env['sup1_headers'])

    # Complete first job (300 rows committed), then simulate worker crash/interruption
    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)
        batch = db.get(ImportBatch, batch_id)
        assert batch.created == 300
        assert batch.status == 'applying'
        batch.status = 'failed'
        batch.failure_reason = 'Simulated database interruption'
        db.commit()

    # Safe retry must preserve the 300 saved rows and finish only the remaining 50 rows
    retry_resp = env['client'].post(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/retry", headers=env['sup1_headers'])
    assert retry_resp.status_code == 200
    assert retry_resp.json()['status'] == 'queued'

    with env['Session']() as db:
        process_pricing_jobs(db, limit=2)

    done = env['client'].get(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}", headers=env['sup1_headers']).json()
    assert done['status'] == 'completed'
    assert done['created'] == 350
    assert done['updated_count'] == 0
    assert done['saved'] == 350

    with env['Session']() as db:
        assert db.scalar(select(func.count(PriceBookItem.id)).where(PriceBookItem.org_id == env['sup1_org'])) == 350


def test_quotation_prefill_and_no_implicit_auto_submission(env):
    xlsx = _make_xlsx([
        ['Civil Materials', 'Cement & Concrete', 'Ordinary Portland Cement 50kg', 'MAT-CEM-001', 'BAG', 'Saudi Cement', 'OPC-I', '21.50', 20, 4, 'Active', 'Bulk import'],
    ])
    up = env['client'].post(
        f"/api/v1/pricebooks/{env['sup1_org']}/bulk-import",
        headers=env['sup1_headers'],
        files={'file': ('quote.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    batch_id = up.json()['batch_id']
    with env['Session']() as db:
        process_pricing_jobs(db, limit=1)
    env['client'].post(f"/api/v1/pricebooks/{env['sup1_org']}/imports/{batch_id}/confirm", headers=env['sup1_headers'])
    with env['Session']() as db:
        process_pricing_jobs(db, limit=2)

    with env['Session']() as db:
        # Automatic quotation submission must NOT be enabled merely because prices were imported
        org = db.get(Organization, env['sup1_org'])
        assert not (org.quote_automation or {}).get('enabled', False)

        # Create an RFQ & invitation referencing m1
        rfq = RFQ(
            reference='RFQ-PB-001',
            buyer_org_id=env['buyer_org'],
            title='Cement Supply',
            city='Riyadh',
            response_deadline=datetime.now(UTC) + timedelta(days=3),
            status='published',
        )
        db.add(rfq)
        db.flush()
        rfq_item = RFQItem(
            rfq_id=rfq.id,
            material_id=env['m1'].id,
            main_category_id=env['main'].id,
            subcategory_id=env['sub'].id,
            description=env['m1'].name_en,
            specifications='',
            unit='BAG',
            quantity=Decimal('100'),
        )
        db.add(rfq_item)
        inv = Invitation(rfq_id=rfq.id, supplier_org_id=env['sup1_org'], status='accepted')
        db.add(inv)
        db.commit()

        # prepare_authorized_quote must NOT auto-submit without explicit authorization
        assert prepare_authorized_quote(db, inv.id) is None

        # build_draft prefills quotation line from imported private price book while requiring supplier review
        quote = build_draft(db, inv)
        db.commit()
        assert quote.status == 'draft'
        assert quote.review_required is True
        line = db.scalar(select(QuoteLine).where(QuoteLine.quotation_id == quote.id))
        assert line is not None
        assert line.unit_price == Decimal('21.50')
        assert line.brand == 'Saudi Cement'
        assert line.model == 'OPC-I'
        assert line.lead_days == 4
