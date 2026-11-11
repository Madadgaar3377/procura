"""Opt-in replay of privately retained real source rows into an isolated fixture."""
import json
import os
from io import BytesIO
from pathlib import Path
from time import perf_counter

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.models import Category, Contact, ContactCapability, ContactSupplierTrade, Capability, Material, Organization
from app.modules.imports import bulk
from tests.test_supplier_bulk_jobs import system, enqueue, drain


def test_retained_real_source_replay(system):
    source = os.environ.get('SUPPLIER_SOURCE_REPLAY_PATH')
    if not source:
        pytest.skip('Set SUPPLIER_SOURCE_REPLAY_PATH for private real-data replay')
    client, sessions, _, app = system
    from app.modules.organizations.router import router
    app.include_router(router)
    source = Path(source)
    rows = json.loads(source.read_text(encoding='utf-8'))
    taxonomy = json.loads(source.with_name('recovered-supplier-taxonomy.json').read_text(encoding='utf-8'))
    with sessions() as db:
        pending = list(taxonomy)
        saved = set()
        while pending:
            ready = [item for item in pending if item['parent_id'] is None or item['parent_id'] in saved]
            assert ready, 'Source taxonomy contains a cycle or missing parent'
            db.add_all([Category(**item) for item in ready])
            db.flush()
            for item in ready:
                pending.remove(item)
                saved.add(item['id'])
        db.commit()
    book = Workbook()
    sheet = book.active
    sheet.append(bulk.SUPPLIER_HEADERS)
    for _, row in rows:
        sheet.append([row.get(key, '') for key in bulk.SUPPLIER_HEADERS])
    out = BytesIO()
    book.save(out)
    started = perf_counter()
    first = enqueue(client, out.getvalue())
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{first['batch_id']}").json()
    assert result['supplier_failures'] == 0, result['issues']
    assert result['total_rows'] == len(rows)
    counts = {}
    with sessions() as db:
        for model in [Organization, Contact, Capability, ContactCapability, ContactSupplierTrade, Material]:
            counts[model.__tablename__] = db.scalar(select(func.count()).select_from(model))
        assert counts['organizations'] == result['total_supplier_records_identified']
        assert counts['contact_supplier_trades'] == result['responsibilities_created']
        assert counts['organization_contacts'] == result['contacts_created']
        assert db.scalar(select(func.count()).select_from(Organization).where(Organization.is_active.is_(True))) == counts['organizations']
        material_assignments = sum(len(link.material_ids or []) for link in db.scalars(select(ContactCapability)))
        assert counts['materials'] == result['materials_created']
    listed = client.get('/organizations/admin/suppliers?limit=100')
    assert listed.status_code == 200
    assert len(listed.json()) == counts['organizations']
    elapsed = perf_counter() - started
    repeated = enqueue(client, out.getvalue())
    drain(sessions)
    repeated = client.get(f"/bulk/suppliers/jobs/{repeated['batch_id']}").json()
    for key in ['new_suppliers_saved', 'contacts_created', 'category_assignments_created', 'responsibilities_created', 'capabilities_saved']:
        assert repeated[key] == 0, (key, repeated)
    with sessions() as db:
        for model in [Organization, Contact, Capability, ContactCapability, ContactSupplierTrade, Material]:
            assert db.scalar(select(func.count()).select_from(model)) == counts[model.__tablename__]
    report = {'source_rows':len(rows), 'seconds':round(elapsed, 3), 'database_counts':counts,
        'employee_material_assignments':material_assignments,'admin_list_count':len(listed.json()),
        'first_import': {k:v for k,v in result.items() if k not in {'warnings','issues','batch_id'}},
        'repeated_import': {k:v for k,v in repeated.items() if k not in {'warnings','issues','batch_id'}}}
    source.with_name('supplier-source-replay-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
