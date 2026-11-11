"""Isolated database tests: never uses the configured application database."""
from io import BytesIO
from math import ceil
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Capability, Category, Contact, ContactCapability, ImportBatch, ImportedRow, Material, Organization, User
from app.modules.imports import bulk, supplier_jobs as jobs
from app.security import current_user


@pytest.fixture
def system():
    postgres_url = os.environ.get('SUPPLIER_BULK_TEST_DATABASE_URL')
    control = None
    if postgres_url:
        from sqlalchemy.engine import make_url
        from alembic import command
        from alembic.config import Config
        import psycopg
        from psycopg import sql
        url = make_url(postgres_url)
        if url.host not in {'127.0.0.1', 'localhost'} or 'test' not in (url.database or ''):
            pytest.fail('PostgreSQL fixture requires an explicitly named local test database')
        database_name = 'bulk_supplier_test_' + uuid4().hex
        control = psycopg.connect(url.set(drivername='postgresql').render_as_string(hide_password=False), autocommit=True)
        control.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
        engine = create_engine(url.set(database=database_name))
        with engine.begin() as connection:
            config = Config('alembic.ini')
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
    else:
        engine = create_engine('sqlite:///:memory:', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        admin = User(username='job-admin', phone='+966500000020', password_hash='x', is_admin=True)
        db.add(admin)
        db.commit()
    app = FastAPI()
    app.include_router(jobs.router)
    app.include_router(bulk.router)
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: admin
    with TestClient(app) as client:
        yield client, sessions, admin, app
    engine.dispose()
    if control:
        # This name was generated above, never supplied by application config.
        control.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database_name)))
        control.close()


def workbook(count=1, repeat_contacts=False):
    book = Workbook()
    sheet = book.active
    sheet.append(bulk.SUPPLIER_HEADERS)
    for i in range(count):
        row = {'Company Name': f'Supplier {i}', 'Country': 'Saudi Arabia', 'City': 'Riyadh',
               'Contact Person': 'Sales', 'Contact Email': f'sales{i}@example.com', 'Category': 'Lighting'}
        sheet.append([row.get(header, '') for header in bulk.SUPPLIER_HEADERS])
        if repeat_contacts:
            sheet.append([row.get(header, '') for header in bulk.SUPPLIER_HEADERS])
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def enqueue(client, content):
    response = client.post('/bulk/suppliers/jobs', files={'file': ('test.xlsx', content)})
    assert response.status_code == 202, response.text
    return response.json()


def drain(sessions):
    for _ in range(20):
        with sessions() as db:
            if not jobs.process_supplier_jobs(db):
                break
    else:
        pytest.fail('Queue did not drain')


def source_workbook(rows):
    book = Workbook()
    sheet = book.active
    sheet.append(['Suppliers Name', 'Trade/Products', 'Contact Person', 'Mobile/Phone', 'Email',
                  'Website', 'Location', 'Supplier/Manufacturer', 'Brands', 'Verification Notes'])
    for row in rows:
        sheet.append(row)
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def test_fresh_import_after_archive_preserves_exact_employee_responsibilities(system):
    from app.models import ContactSupplierTrade, ImportedSupplierTrade
    client, sessions, _, _ = system
    data = source_workbook([
        ['Fresh Company', 'Electrical / Lighting Fixtures', 'Amir', '+966501234567', '', '', '', '', '', 'Review source'],
        ['', 'Mechanical / Pumps', 'Amir', '+966501234567'],
        ['', 'Electrical / Lighting Fixtures', 'Sara', '+966501234568'],
        ['', 'Unclear special product', 'Sara', '+966501234568'],
    ])
    first = enqueue(client, data)
    # Concurrent same-file submission must still share the active job.
    assert enqueue(client, data)['batch_id'] == first['batch_id']
    drain(sessions)
    progress = client.get(f"/bulk/suppliers/jobs/{first['batch_id']}").json()
    assert progress['new_suppliers_saved'] == 1
    assert progress['contacts_created'] == 2
    assert progress['responsibilities_created'] == 4
    assert progress['category_assignments_created'] == 3
    assert progress['capabilities_saved'] == 2
    assert progress['pending_category_mappings'] == 1
    assert progress['status'] == 'completed_with_errors'
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(ContactSupplierTrade)) == 4
        assert db.scalar(select(func.count()).select_from(Contact)) == 2
        original = db.scalar(select(Organization))
        original_id = original.id
        original.is_active = False
        db.commit()
    fresh = enqueue(client, data)
    assert fresh['batch_id'] != first['batch_id']
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{fresh['batch_id']}").json()
    assert result['new_suppliers_saved'] == 1
    assert result['existing_suppliers_reused'] == 0
    assert result['contacts_created'] == 2

    with sessions() as db:
        assert db.get(Organization, original_id).is_active is False
        assert db.scalar(select(func.count()).select_from(Organization).where(Organization.is_active.is_(True))) == 1
        assert db.scalar(select(func.count()).select_from(ContactSupplierTrade)) == 8
        assert db.scalar(select(func.count()).select_from(ImportedSupplierTrade)) == 6
    repeat = enqueue(client, data)
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{repeat['batch_id']}").json()
    for key in ['new_suppliers_saved', 'contacts_created', 'category_assignments_created', 'responsibilities_created', 'capabilities_saved']:
        assert result[key] == 0, (key, result)
    assert result['responsibilities_reused'] == 4
    second = enqueue(client, source_workbook([
        ['Fresh Company', 'Mechanical / Pumps', 'Lina', '+966501234569'],
        ['Next Company', 'Electrical / Lighting Fixtures', 'Omar'],
    ]))
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{second['batch_id']}").json()
    assert result['new_suppliers_saved'] == result['existing_suppliers_reused'] == 1
    assert result['contacts_created'] == 2


def test_employee_materials_do_not_inherit_colleagues_materials(system):
    client, sessions, _, app = system
    from app.modules.organizations.router import router
    app.include_router(router)
    book = Workbook()
    sheet = book.active
    headers = [*bulk.SUPPLIER_HEADERS, 'Unit']
    sheet.append(headers)
    for person, material in [('Ali', 'Panel Board'), ('Sara', 'Transformers'), ('Ali', 'Wiring Devices')]:
        row = {'Company Name':'Material Scope Co', 'Contact Person':person,
               'Category':'Electrical', 'Material':material}
        sheet.append([row.get(key, '') for key in headers])
    out = BytesIO()
    book.save(out)
    job = enqueue(client, out.getvalue())
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{job['batch_id']}").json()
    assert result['status'] == 'completed'
    assert result['contacts_created'] == 2 and result['materials_created'] == 3
    with sessions() as db:
        org = db.scalar(select(Organization))
        org_id = org.id
        for contact in db.scalars(select(Contact)):
            link = db.scalar(select(ContactCapability).where(ContactCapability.contact_id == contact.id))
            names = set(db.scalars(select(Material.name_en).where(Material.id.in_(link.material_ids))))
            assert names == ({'Panel Board','Wiring Devices'} if contact.name == 'Ali' else {'Transformers'})
    detail = client.get(f'/organizations/admin/suppliers/{org_id}')
    assert detail.status_code == 200, detail.text
    for contact in detail.json()['contacts']:
        names = set(contact['assigned_capabilities'][0]['materials'])
        assert names == ({'Panel Board','Wiring Devices'} if contact['name'] == 'Ali' else {'Transformers'})
    listed = client.get('/organizations/admin/suppliers')
    assert listed.status_code == 200 and 'Material Scope Co' in listed.text
    repeated = enqueue(client, out.getvalue())
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{repeated['batch_id']}").json()
    assert result['new_suppliers_saved'] == result['contacts_created'] == result['category_assignments_created'] == result['materials_created'] == 0


def test_two_fifty_row_source_files_reuse_companies_and_name_only_contacts(system):
    client, sessions, _, _ = system
    for offset, expected_new, expected_reused in [(0, 25, 0), (20, 20, 5)]:
        content = source_workbook([
            [f'Source Company {offset + index // 2}' if index % 2 == 0 else '',
             'Electrical / Panels', f'Employee {offset}-{index}']
            for index in range(50)])
        queued = enqueue(client, content)
        drain(sessions)
        result = client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()
        assert result['total_rows'] == 50
        assert result['new_suppliers_saved'] == expected_new
        assert result['existing_suppliers_reused'] == expected_reused
        assert result['contacts_created'] == result['responsibilities_created'] == 50
        assert result['status'] == 'completed'
        repeated = enqueue(client, content)
        drain(sessions)
        result = client.get(f"/bulk/suppliers/jobs/{repeated['batch_id']}").json()
        assert result['new_suppliers_saved'] == result['contacts_created'] == result['category_assignments_created'] == result['responsibilities_created'] == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 45
        assert db.scalar(select(func.count()).select_from(Contact)) == 100
        assert db.scalar(select(func.count()).select_from(ContactCapability)) == 100


def test_archived_supplier_cr_does_not_block_fresh_verified_import(system):
    client, sessions, _, _ = system
    with sessions() as db:
        db.add(Organization(name='CR Supplier', name_normalized='cr supplier', is_supplier=True,
            is_buyer=False, is_active=False, cr_number='ARCHIVED-CR', verified_status='verified'))
        db.commit()
    book = Workbook()
    sheet = book.active
    sheet.append(bulk.SUPPLIER_HEADERS)
    row = {'Company Name':'CR Supplier', 'CR Number':'ARCHIVED-CR', 'Contact Person':'Sales'}
    sheet.append([row.get(key, '') for key in bulk.SUPPLIER_HEADERS])
    out = BytesIO()
    book.save(out)
    job = enqueue(client, out.getvalue())
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{job['batch_id']}").json()
    assert result['new_suppliers_saved'] == 1 and result['failed'] == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 2
        assert db.scalar(select(Organization).where(Organization.is_active.is_(True))).verified_status == 'verified'


@pytest.mark.parametrize('count', [1, 10, 28, 35, 36, 50])
def test_group_counts_and_committed_progress(system, count):
    client, sessions, admin, app = system
    content = workbook(count, repeat_contacts=True)
    queued = enqueue(client, content)
    assert queued['total_supplier_records_identified'] == count
    assert queued['total_rows'] == count * 2
    assert queued['batches_total'] == ceil(count / 10)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0
        jobs.process_supplier_jobs(db)
    partial = client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()
    assert partial['new_suppliers_saved'] == min(10, count)
    # A second session sees only committed records, matching the displayed counts.
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == min(10, count)
    drain(sessions)  # Opens a new session on every chunk, as after a worker restart.
    result = client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()
    assert result['status'] == 'completed_with_errors'  # Lighting has no approved taxonomy in this fixture.
    assert result['new_suppliers_saved'] == count
    assert result['progress_percent'] == 100
    assert result['completed_batches'] == ceil(count / 10)
    repeated = enqueue(client, content)
    assert repeated['batch_id'] != queued['batch_id']
    drain(sessions)
    repeated = client.get(f"/bulk/suppliers/jobs/{repeated['batch_id']}").json()
    assert repeated['new_suppliers_saved'] == repeated['contacts_created'] == repeated['category_assignments_created'] == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == count
        assert db.scalar(select(func.count()).select_from(Contact)) == count


def test_28_suppliers_35_batch_is_one_batch_and_existing_stream_matches(system, monkeypatch):
    client, sessions, admin, app = system
    monkeypatch.setattr(bulk, 'SessionLocal', sessions)
    rows = bulk._read(workbook(28, repeat_contacts=True), 'test.xlsx', bulk.SUPPLIER_HEADERS)
    import json
    events = [json.loads(event) for event in bulk._stream_import('suppliers', rows, 'test.xlsx', admin.id)]
    assert events[0]['batches_total'] == 1
    assert events[-1]['completed_batches'] == 1
    assert events[-1]['new_suppliers_saved'] == 28
    queued = enqueue(client, workbook(28, repeat_contacts=True))
    with sessions() as db:
        batch = db.get(ImportBatch, queued['batch_id'])
        batch.import_options = {'batch_size': 35}
        db.commit()
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()
    assert result['batches_total'] == result['completed_batches'] == 1
    assert result['existing_suppliers_reused'] == 28


def test_partial_failure_retry_is_atomic_and_keeps_contact_assignments(system, monkeypatch):
    client, sessions, admin, app = system
    with sessions() as db:
        db.add(Category(code='JOB-LIGHT', name_en='Lighting', normalized_name='lighting'))
        db.commit()
    queued = enqueue(client, workbook(12))
    original = jobs._apply_group
    def fail(db, batch, item, user):
        value = original(db, batch, item, user)
        if item.raw['source_rows'][0][1]['Company Name'] == 'Supplier 5':
            raise ValueError('Invalid supplier test row')
        return value
    monkeypatch.setattr(jobs, '_apply_group', fail)
    drain(sessions)
    partial = client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()
    assert partial['status'] == 'completed_with_errors'
    assert partial['new_suppliers_saved'] == 11 and partial['failed'] == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 11
        assert db.scalar(select(func.count()).select_from(ContactCapability)) == 11
    report = client.get(f"/bulk/{queued['batch_id']}/errors")
    assert report.status_code == 200
    assert load_workbook(BytesIO(report.content)).active.max_row == 2
    monkeypatch.setattr(jobs, '_apply_group', original)
    assert client.post(f"/bulk/suppliers/jobs/{queued['batch_id']}/retry").status_code == 202
    drain(sessions)
    final = client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()
    assert final['new_suppliers_saved'] == 12 and final['failed'] == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 12
        assert db.scalar(select(func.count()).select_from(Contact)) == 12
        assert db.scalar(select(func.count()).select_from(ContactCapability)) == 12


def test_database_timeout_rolls_back_only_current_chunk_and_can_resume(system, monkeypatch):
    client, sessions, admin, app = system
    queued = enqueue(client, workbook(12))
    with sessions() as db:
        jobs.process_supplier_jobs(db)
    original = jobs._apply_group
    def timeout(db, batch, item, user):
        original(db, batch, item, user)
        raise OperationalError('test', {}, RuntimeError('statement timeout'))
    monkeypatch.setattr(jobs, '_apply_group', timeout)
    with sessions() as db:
        jobs.process_supplier_jobs(db)
    partial = client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()
    assert partial['new_suppliers_saved'] == 10
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 10
    monkeypatch.setattr(jobs, '_apply_group', original)
    drain(sessions)
    assert client.get(f"/bulk/suppliers/jobs/{queued['batch_id']}").json()['new_suppliers_saved'] == 12


def test_real_auth_dependencies_protect_all_job_operations(system):
    client, sessions, admin, app = system
    app.dependency_overrides.pop(current_user)
    assert client.post('/bulk/suppliers/jobs', files={'file': ('x.xlsx', workbook())}).status_code == 401
    assert client.get('/bulk/suppliers/jobs/unknown').status_code == 401
    assert client.post('/bulk/suppliers/jobs/unknown/retry').status_code == 401
    admin.is_admin = False
    app.dependency_overrides[current_user] = lambda: admin
    assert client.post('/bulk/suppliers/jobs', files={'file': ('x.xlsx', workbook())}).status_code == 403
    assert client.get('/bulk/suppliers/jobs/unknown').status_code == 403
    assert client.post('/bulk/suppliers/jobs/unknown/retry').status_code == 403


def test_bad_excel_and_file_limit(system):
    client, *_ = system
    assert client.post('/bulk/suppliers/jobs', files={'file': ('test.xlsx', b'not a workbook')}).status_code == 422
    assert client.post('/bulk/suppliers/jobs', files={'file': ('test.xlsx', b'x' * 8_000_001)}).status_code == 413


def test_multi_material_contact_and_changed_file_reuse_existing_records(system):
    client, sessions, *_ = system
    with sessions() as db:
        main = Category(code='M-MAIN', name_en='Main', normalized_name='main')
        db.add(main); db.flush()
        sub = Category(code='M-LIGHT', name_en='Lighting', normalized_name='lighting', parent_id=main.id)
        db.add(sub); db.flush()
        materials = [Material(code=f'M-{i}', name_en=name, normalized_description=name.casefold(), unit='PCS',
            category_id=sub.id, subcategory_id=sub.id, main_category_id=main.id) for i, name in enumerate(['Cable', 'Lamp'])]
        db.add_all(materials); db.commit()
        material_ids = {m.id for m in materials}
    book = load_workbook(BytesIO(workbook()))
    sheet = book.active
    sheet.cell(2, bulk.SUPPLIER_HEADERS.index('Material') + 1, 'Cable; Lamp')
    stream = BytesIO(); book.save(stream)
    first = enqueue(client, stream.getvalue())
    drain(sessions)
    # A different file fingerprint must still reuse business identities.
    sheet.title = 'Second file'
    stream = BytesIO(); book.save(stream)
    second = enqueue(client, stream.getvalue())
    assert first['batch_id'] != second['batch_id']
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{second['batch_id']}").json()
    assert result['new_suppliers_saved'] == 0 and result['existing_suppliers_reused'] == 1
    assert result['contacts_created'] == result['category_assignments_created'] == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 1
        assert db.scalar(select(func.count()).select_from(Contact)) == 1
        assert db.scalar(select(func.count()).select_from(ContactCapability)) == 1
        assert set(db.scalar(select(Capability)).material_ids) == material_ids


def test_conflicting_contact_cannot_modify_unrelated_company(system):
    client, sessions, *_ = system
    with sessions() as db:
        company = Organization(name='Protected Company', name_normalized='protected company',
            is_supplier=True, is_buyer=False, business_email='sales0@example.com', city='Jeddah', source='registered')
        db.add(company); db.commit(); company_id = company.id
    job = enqueue(client, workbook())
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{job['batch_id']}").json()
    assert result['failed'] == 0
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 2
        company = db.get(Organization, company_id)
        assert company.name == 'Protected Company' and company.city == 'Jeddah'
        assert db.scalar(select(func.count()).select_from(Contact)) == 1


def test_contact_update_counts_are_distinct_records(system):
    client, sessions, *_ = system
    book = load_workbook(BytesIO(workbook(1, repeat_contacts=True)))
    sheet = book.active
    designation = bulk.SUPPLIER_HEADERS.index('Contact Designation') + 1
    sheet.cell(2, designation, 'Sales manager')
    sheet.cell(3, designation, 'Sales manager')
    stream = BytesIO(); book.save(stream)
    first = enqueue(client, stream.getvalue())
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{first['batch_id']}").json()
    assert result['contacts_created'] == 1 and result['contacts_updated'] == 0
    phone_column = bulk.SUPPLIER_HEADERS.index('Contact Phone') + 1
    sheet.cell(2, phone_column, '+966500000001')
    sheet.cell(3, phone_column, '+966500000002')
    stream = BytesIO(); book.save(stream)
    second = enqueue(client, stream.getvalue())
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{second['batch_id']}").json()
    assert result['contacts_created'] == 0 and result['contacts_updated'] == 1


def test_postgres_concurrent_uploads_and_workers_do_not_duplicate(system):
    client, sessions, *_ = system
    if sessions.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires isolated PostgreSQL fixture')
    from concurrent.futures import ThreadPoolExecutor
    content = workbook(28)
    with ThreadPoolExecutor(max_workers=2) as executor:
        uploads = list(executor.map(lambda _: enqueue(client, content), range(2)))
    assert uploads[0]['batch_id'] == uploads[1]['batch_id']
    def worker(_):
        with sessions() as db:
            return jobs.process_supplier_jobs(db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(worker, range(2)))
    drain(sessions)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
        assert db.scalar(select(func.count()).select_from(Organization)) == 28
        assert db.scalar(select(func.count()).select_from(Contact)) == 28


def test_postgres_real_query_timeout_preserves_checkpoint(system, monkeypatch):
    client, sessions, *_ = system
    if sessions.kw['bind'].dialect.name != 'postgresql':
        pytest.skip('Requires isolated PostgreSQL fixture')
    from sqlalchemy import text
    job = enqueue(client, workbook(12))
    with sessions() as db:
        jobs.process_supplier_jobs(db)
    original = jobs._apply_group
    def slow(db, *args):
        result = original(db, *args)
        db.execute(text("SET LOCAL statement_timeout = '100ms'"))
        db.execute(text('SELECT pg_sleep(0.3)'))
        return result
    monkeypatch.setattr(jobs, '_apply_group', slow)
    with sessions() as db:
        jobs.process_supplier_jobs(db)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 10
        assert db.get(ImportBatch, job['batch_id']).completed_rows == 10
    monkeypatch.setattr(jobs, '_apply_group', original)
    drain(sessions)
    assert client.get(f"/bulk/suppliers/jobs/{job['batch_id']}").json()['new_suppliers_saved'] == 12


def test_postgres_process_exit_before_commit_and_restart(system):
    client, sessions, *_ = system
    engine = sessions.kw['bind']
    if engine.dialect.name != 'postgresql':
        pytest.skip('Requires isolated PostgreSQL fixture')
    import subprocess
    import sys
    job = enqueue(client, workbook(12))
    with sessions() as db:
        jobs.process_supplier_jobs(db)
    code = '''
import os
from app.database import SessionLocal
from app.modules.imports import supplier_jobs as jobs
original = jobs._apply_group
def crash(*args):
    original(*args)
    os._exit(91)
jobs._apply_group = crash
with SessionLocal() as db:
    jobs.process_supplier_jobs(db)
'''
    environment = dict(os.environ, DATABASE_URL=engine.url.render_as_string(hide_password=False), APP_ENV='test')
    process = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, timeout=30)
    assert process.returncode == 91
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 10
        assert db.get(ImportBatch, job['batch_id']).completed_rows == 10
    drain(sessions)
    result = client.get(f"/bulk/suppliers/jobs/{job['batch_id']}").json()
    assert result['new_suppliers_saved'] == 12 and result['status'] == 'completed_with_errors'
