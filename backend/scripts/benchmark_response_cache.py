"""Reproducible local cache comparison; never connects to configured services."""
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch

os.environ.update(APP_ENV='test', DATABASE_URL='sqlite:///:memory:', REDIS_URL='',
                  MOCK_NOTIFICATIONS='true', AUTH_DELIVERY_AUTO_DISPATCH='false')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import catalog_cache
from app.database import Base, SessionLocal, engine, get_db
from app.models import Capability, Category, Material, Organization, User
from app.modules.admin.analytics import router as admin_router
from app.modules.capabilities.directory import router as directory_router
from app.modules.taxonomy.router import router as taxonomy_router
from app.security import current_user

assert str(engine.url) == 'sqlite:///:memory:'
Base.metadata.create_all(engine)
with SessionLocal() as db:
    db.add(Category(id='cat', code='C', name_en='Electrical'))
    db.flush()
    for i in range(1000):
        db.add(Organization(id=f's{i}', name=f'Supplier {i:04}', name_normalized=f'supplier {i:04}',
                            is_supplier=True, is_active=True, verified_status='verified', city='Riyadh'))
        db.add(Material(id=f'm{i}', code=f'M{i:04}', name_en=f'Copper cable {i:04}', category_id='cat', unit='M'))
    db.flush()
    db.add_all(Capability(org_id=f's{i}', category_id='cat', city=city, confirmed=True,
                          accepts_rfqs=True, eligibility_status='enabled')
               for i in range(1000) for city in ('Riyadh', 'Jeddah'))
    db.commit()

app = FastAPI()
for router in (directory_router, taxonomy_router, admin_router):
    app.include_router(router)

def database():
    with SessionLocal() as db:
        yield db
app.dependency_overrides[get_db] = database
app.dependency_overrides[current_user] = lambda: User(id='benchmark-admin', is_admin=True)

queries = []
@event.listens_for(engine, 'before_cursor_execute')
def count(conn, cursor, statement, parameters, context, executemany):
    if statement.lstrip().upper().startswith('SELECT'):
        queries.append(statement)

result = {'environment': 'Synthetic isolated SQLite + TestClient + actual in-process L1. '
          'Not PostgreSQL, Redis network, Nginx, or VPS timings. Admin authentication overridden.',
          'suppliers': 1000, 'capabilities': 2000, 'materials': 1000, 'samples': 30, 'endpoints': {}}
paths = {
    'directory': '/directory/suppliers?paginated=true&limit=25&include_categories=true&city=Riyadh',
    'profile': '/directory/suppliers/s0?include_categories=true&include_coverage=true',
    'categories': '/taxonomy/categories',
    'materials': '/taxonomy/materials?q=copper&limit=25',
    'analytics': '/admin/dashboard/analytics?days=30',
}
with TestClient(app) as client:
    for name, path in paths.items():
        measurements = {}
        original = None
        for mode in ('uncached', 'cached'):
            times, counts = [], []
            with ExitStack() as stack:
                if mode == 'uncached':
                    stack.enter_context(patch.object(catalog_cache, 'get', return_value=None))
                    stack.enter_context(patch.object(catalog_cache, 'set', return_value=None))
                    stack.enter_context(patch.object(catalog_cache, 'version', return_value=0))
                else:
                    catalog_cache.invalidate()
                for i in range(35):
                    queries.clear()
                    started = perf_counter()
                    response = client.get(path)
                    elapsed = (perf_counter() - started) * 1000
                    assert response.status_code == 200, response.text
                    # Analytics includes a fill timestamp; all other bodies match.
                    if original is None:
                        original = response.json()
                    if name != 'analytics':
                        assert response.json() == original
                    if i >= 5:
                        times.append(elapsed)
                        counts.append(len(queries))
            measurements[mode] = {'p50_ms': round(median(times), 3),
                                  'p95_ms': round(sorted(times)[28], 3),
                                  'selects_per_request': max(counts)}
        result['endpoints'][name] = measurements
print(json.dumps(result, indent=2))
