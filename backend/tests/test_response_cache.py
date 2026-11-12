"""Cache integration against isolated SQLAlchemy data and a deterministic Redis double.

No configured PostgreSQL/Redis instance or real organization is accessed.
"""
import math
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import catalog_cache
from app.database import Base, get_db
from app.models import Category, Capability, Material, Organization, Outbox, PublicProfileConsent, User
from app.modules.admin.analytics import router as admin_router
from app.modules.capabilities.directory import router as directory_router
from app.modules.database.router import router as database_router
from app.modules.imports.materials import router as material_import_router
from app.modules.taxonomy.router import router as taxonomy_router
from app.security import current_user


class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.expires = {}
        self.broken = False
        self.reads = []

    def get(self, key):
        if self.broken:
            raise ConnectionError('test outage')
        self.reads.append(key)
        if self.expires.get(key, math.inf) <= monotonic():
            self.data.pop(key, None)
        return self.data.get(key)

    def set(self, key, value, nx=False):
        if nx and self.get(key) is not None:
            return False
        if self.broken:
            raise ConnectionError('test outage')
        self.data[key] = str(value)
        return True

    def incr(self, key):
        value = int(self.get(key) or 0) + 1
        self.set(key, value)
        return value

    def setex(self, key, ttl, value):
        self.set(key, value)
        self.expires[key] = monotonic() + ttl

    def ttl(self, key):
        return max(0, math.floor(self.expires[key] - monotonic())) if key in self.expires else -1


@pytest.fixture
def cached_api(monkeypatch):
    redis = MemoryRedis()
    monkeypatch.setattr(catalog_cache, 'get_redis', lambda: redis)
    monkeypatch.setattr(catalog_cache, 'get_settings', lambda: SimpleNamespace(redis_url='redis://test-only'))
    catalog_cache.invalidate()
    engine = create_engine('sqlite:///:memory:', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(Category(id='cat', code='CAT', name_en='Electrical', active=True))
        db.add(Organization(id='supplier', name='Supplier', name_normalized='supplier',
                            city='Riyadh', is_supplier=True, is_active=True, verified_status='verified',
                            business_email='private@example.test', business_phone='+966501234567'))
        db.flush()
        db.add(Capability(org_id='supplier', category_id='cat', city='Riyadh', confirmed=True,
                          accepts_rfqs=True, eligibility_status='enabled'))
        db.add(Material(id='mat', code='M1', name_en='Copper cable', category_id='cat', unit='M', active=True))
        db.commit()
    app = FastAPI()
    for router in (directory_router, taxonomy_router, admin_router, database_router):
        app.include_router(router)
    app.include_router(material_import_router, prefix='/imports')

    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    queries = []
    @event.listens_for(engine, 'before_cursor_execute')
    def track(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=sessions, redis=redis, queries=queries, app=app)
    catalog_cache.invalidate()
    engine.dispose()


def test_first_reads_sql_second_reads_redis_and_contract_is_identical(cached_api):
    t = cached_api
    t.queries.clear()
    first = t.client.get('/directory/suppliers?paginated=true&limit=10')
    assert first.status_code == 200 and t.queries
    assert set(first.json()) == {'items', 'total', 'page', 'limit'}
    catalog_cache._L1.clear()  # Simulate another API worker, forcing L2.
    t.queries.clear()
    hits = catalog_cache.metrics()['redis_hits']
    second = t.client.get('/directory/suppliers?limit=10&paginated=true')
    assert first.json() == second.json()
    assert not t.queries
    assert catalog_cache.metrics()['redis_hits'] == hits + 1


@pytest.mark.parametrize('query', ['page=2', 'limit=1', 'q=missing', 'city=Jeddah',
    'main_category_id=missing', 'subcategory_id=missing', 'verified=false', 'include_categories=true',
    'include_coverage=true', 'paginated=true'])
def test_filters_have_distinct_keys(cached_api, query):
    t = cached_api
    t.client.get('/directory/suppliers')
    before = set(t.redis.data)
    assert t.client.get('/directory/suppliers?' + query).status_code == 200
    assert set(t.redis.data) - before


def test_supplier_mutations_invalidate_only_after_commit(cached_api):
    t = cached_api
    path = '/directory/suppliers/supplier'
    assert t.client.get(path).json()['name'] == 'Supplier'
    with t.db() as db:
        org = db.get(Organization, 'supplier')
        org.name = 'Renamed'
        db.flush()
        assert t.client.get(path).json()['name'] == 'Supplier'
        db.commit()
    assert t.client.get(path).json()['name'] == 'Renamed'
    with t.db() as db:
        db.execute(update(Organization).where(Organization.id == 'supplier').values(is_active=False))
        db.commit()
    assert t.client.get(path).status_code == 404
    assert t.client.get('/directory/suppliers').json() == []


def test_rollback_retains_generation_and_import_batch_commit_exposes_supplier(cached_api):
    t = cached_api
    t.client.get('/directory/suppliers')
    epoch = catalog_cache.version()
    with t.db() as db:
        db.get(Organization, 'supplier').name = 'Rolled back'
        db.flush()
        db.rollback()
    assert catalog_cache.version() == epoch
    with t.db() as db:
        db.add(Organization(id='imported', name='Imported', name_normalized='imported',
                            is_supplier=True, is_active=True, verified_status='verified'))
        db.flush()  # Import batches flush rows before committing.
        assert catalog_cache.version() == epoch
        db.commit()
    assert {row['id'] for row in t.client.get('/directory/suppliers').json()} == {'supplier', 'imported'}


def test_redis_outage_falls_back_and_recovery_invalidates(cached_api):
    t = cached_api
    path = '/directory/suppliers/supplier'
    t.client.get(path)
    t.redis.broken = True
    with t.db() as db:
        db.get(Organization, 'supplier').name = 'During outage'
        db.commit()
    t.queries.clear()
    response = t.client.get(path)
    assert response.status_code == 200 and response.json()['name'] == 'During outage'
    assert t.queries
    t.redis.broken = False
    assert t.client.get(path).json()['name'] == 'During outage'


def test_private_contacts_never_enter_public_cache_and_consent_revocation_invalidates(cached_api):
    t = cached_api
    path = '/directory/suppliers/supplier?include_profile=true'
    assert 'private@example.test' not in t.client.get(path).text
    assert all('private@example.test' not in str(value) for value in t.redis.data.values())
    with t.db() as db:
        db.add(PublicProfileConsent(org_id='supplier', fields=['business_email']))
        db.commit()
    assert t.client.get(path).json()['profile']['business_email'] == 'private@example.test'
    with t.db() as db:
        db.get(PublicProfileConsent, 'supplier').fields = []
        db.commit()
    assert 'private@example.test' not in t.client.get(path).text


def test_admin_authentication_runs_before_cache_and_keys_are_scoped(cached_api):
    t = cached_api
    t.app.dependency_overrides[current_user] = lambda: User(id='admin-a', is_admin=True)
    path = '/admin/dashboard/analytics?days=7'
    first = t.client.get(path)
    assert first.status_code == 200
    t.queries.clear()
    assert t.client.get(path).json() == first.json()
    assert not t.queries
    t.app.dependency_overrides[current_user] = lambda: User(id='admin-b', is_admin=True)
    assert t.client.get(path).status_code == 200 and t.queries
    before = len(t.redis.reads)
    t.app.dependency_overrides[current_user] = lambda: User(id='ordinary', is_admin=False)
    assert t.client.get(path).status_code == 403
    assert t.client.get('/db/performance').status_code == 403
    assert len(t.redis.reads) == before
    t.app.dependency_overrides.pop(current_user)
    assert t.client.get(path).status_code == 401
    assert t.client.get('/db/performance').status_code == 401


def test_material_partial_search_is_paginated_and_cached(cached_api):
    t = cached_api
    path = '/taxonomy/materials?q=opp&limit=1&offset=0'
    first = t.client.get(path)
    assert first.status_code == 200 and first.json()[0]['name_en'] == 'Copper cable'
    t.queries.clear()
    assert t.client.get(path).json() == first.json() and not t.queries
    assert t.client.get('/taxonomy/materials?q=opp&limit=1&offset=1').json() == []


def test_l1_never_outlives_requested_ttl_or_remaining_redis_ttl(cached_api, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(catalog_cache, 'monotonic', lambda: clock[0])
    epoch = catalog_cache.version()
    catalog_cache.set('short', {}, {'ok': True}, ttl=30, epoch=epoch)
    key = catalog_cache._key('short', {}, epoch)
    assert catalog_cache._L1[key][0] == 1030
    catalog_cache._L1.clear()
    monkeypatch.setattr(cached_api.redis, 'ttl', lambda key: 2)
    assert catalog_cache.get('short', {}, epoch) == {'ok': True}
    assert catalog_cache._L1[key][0] == 1002


def test_durable_invalidation_survives_writer_exit(cached_api, monkeypatch):
    from app import cache_invalidation
    from app.worker import _process_event
    t = cached_api
    monkeypatch.setattr(cache_invalidation, 'get_settings', lambda: SimpleNamespace(redis_url='redis://test-only'))
    with t.db() as db:
        db.get(Organization, 'supplier').name = 'Committed'
        t.redis.broken = True
        db.commit()
        row = db.scalar(select(Outbox).where(Outbox.event == 'cache.public.invalidate'))
        assert row is not None
        catalog_cache._PENDING_INVALIDATION = False  # Writer exited.
        _process_event(db, row, datetime.now(UTC))
        assert row.status == 'retry'
        t.redis.broken = False
        before = catalog_cache.version()
        _process_event(db, row, datetime.now(UTC))
        assert catalog_cache.version() > before


def test_actual_supplier_import_commits_invalidate_warmed_directory(cached_api):
    from app.models import ImportBatch, ImportedRow
    from app.modules.imports.router import process_queued_supplier_imports
    t = cached_api
    t.client.get('/directory/suppliers')
    with t.db() as db:
        admin = User(id='import-admin', username='import-admin', phone='+966500000020', password_hash='x', is_admin=True)
        db.add(admin)
        batch = ImportBatch(kind='suppliers', filename='synthetic.xlsx', status='queued', stage='apply',
                            rows=1, initiated_by=admin.id)
        db.add(batch)
        db.flush()
        db.add(ImportedRow(batch_id=batch.id, row_number=2, status='valid',
                           raw={'supplier name': 'Imported supplier', 'trade': 'Electrical',
                                'contact person': 'Test', 'email': 'import-private@example.test'}))
        db.commit()
        process_queued_supplier_imports(db, batch_limit=1, chunk_size=1)
    response = t.client.get('/directory/suppliers')
    assert any(row['name'] == 'Imported supplier' for row in response.json())
    assert 'import-private@example.test' not in response.text


@pytest.mark.parametrize('change', ['verification', 'capability', 'category', 'material'])
def test_related_mutations_advance_namespace(cached_api, change):
    t = cached_api
    before = catalog_cache.version()
    with t.db() as db:
        if change == 'verification':
            db.get(Organization, 'supplier').verified_status = 'unverified'
        elif change == 'capability':
            db.scalar(select(Capability)).confirmed = False
        elif change == 'category':
            db.get(Category, 'cat').name_en = 'Changed'
        else:
            db.get(Material, 'mat').name_en = 'Changed'
        db.commit()
    assert catalog_cache.version() > before
    if change == 'verification':
        assert t.client.get('/directory/suppliers/supplier').status_code == 404


def test_inflight_fill_cannot_repopulate_new_generation(cached_api):
    old = catalog_cache.version()
    catalog_cache.invalidate()
    catalog_cache.set('inflight', {}, {'old': True}, epoch=old)
    assert catalog_cache.get('inflight', {}) is None


def test_epoch_eviction_cannot_resurrect_old_entries(cached_api):
    old = catalog_cache.version()
    catalog_cache.set('evicted', {}, {'old': True}, epoch=old)
    cached_api.redis.data.pop(catalog_cache._EPOCH_KEY)
    assert catalog_cache.version() != old
    assert catalog_cache.get('evicted', {}) is None


def test_pool_is_shared_bounded_and_closed(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app import redis_client
    monkeypatch.setattr(redis_client, '_POOL', None)
    monkeypatch.setattr(redis_client, '_CLIENT', None)
    monkeypatch.setattr(redis_client, '_REDIS_DISABLED_UNTIL', 0.0)
    monkeypatch.setattr(redis_client, 'get_settings', lambda: SimpleNamespace(redis_url='redis://127.0.0.1:6379/15'))
    with ThreadPoolExecutor(max_workers=8) as workers:
        clients = list(workers.map(lambda _: redis_client.get_redis(), range(32)))
    assert all(client is clients[0] for client in clients)
    pool = clients[0].connection_pool
    assert pool.max_connections == 20
    assert pool.connection_kwargs['socket_timeout'] == 0.5
    redis_client.close_redis()
    assert redis_client._POOL is None and redis_client._CLIENT is None


def test_savepoint_commit_invalidates_only_with_outer_commit(cached_api, monkeypatch):
    from app import cache_invalidation
    monkeypatch.setattr(cache_invalidation, 'get_settings', lambda: SimpleNamespace(redis_url='redis://test-only'))
    before = catalog_cache.version()
    with cached_api.db() as db:
        with db.begin_nested():
            db.get(Organization, 'supplier').name = 'Savepoint'
        assert catalog_cache.version() == before
        db.commit()
        assert db.scalar(select(Outbox.id).where(Outbox.event == 'cache.public.invalidate'))
    assert catalog_cache.version() > before


def test_rolled_back_savepoint_retry_event_is_recreated(cached_api, monkeypatch):
    from app import cache_invalidation
    monkeypatch.setattr(cache_invalidation, 'get_settings', lambda: SimpleNamespace(redis_url='redis://test-only'))
    with cached_api.db() as db:
        nested = db.begin_nested()
        db.get(Organization, 'supplier').name = 'Rollback'
        db.flush()
        nested.rollback()
        db.get(Organization, 'supplier').name = 'Keep'
        db.commit()
        assert db.scalar(select(Outbox.id).where(Outbox.event == 'cache.public.invalidate'))


def test_authorized_material_master_search_checks_permissions_before_cache(cached_api):
    t = cached_api
    path = '/imports/materials?q=opp&limit=1'
    assert t.client.get(path).status_code == 401
    t.app.dependency_overrides[current_user] = lambda: User(id='admin', is_admin=True)
    first = t.client.get(path)
    assert first.status_code == 200 and first.json()['items'][0]['id'] == 'mat'
    t.queries.clear()
    assert t.client.get(path).json() == first.json() and not t.queries
    t.app.dependency_overrides[current_user] = lambda: User(id='no-org', is_admin=False)
    assert t.client.get(path).status_code == 403
