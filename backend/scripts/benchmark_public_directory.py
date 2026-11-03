"""Repeatable disposable SQLite benchmark; never reads the configured hosted DB."""
import json
import os
import sys
from pathlib import Path
from statistics import median
from time import perf_counter

os.environ.update(APP_ENV='test',DATABASE_URL='sqlite:///:memory:',REDIS_URL='',
    MOCK_NOTIFICATIONS='true',AUTH_DELIVERY_AUTO_DISPATCH='false',
    JWT_SECRET='benchmark-local-only-secret-at-least-32-characters')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import event, or_, select

from app.database import Base, SessionLocal, engine
from app.models import Capability, Category, Organization
from app.modules.capabilities.directory import discover, public_profiles

assert str(engine.url)=='sqlite:///:memory:'
Base.metadata.create_all(engine)
with SessionLocal() as db:
    root=Category(code='BENCH-E',name_en='Electrical');db.add(root);db.flush()
    child=Category(code='BENCH-T',name_en='Tools',parent_id=root.id);db.add(child);db.flush()
    root_id=root.id
    for i in range(1000):
        org=Organization(name=f'Benchmark {i:04d}',name_normalized=f'benchmark {i:04d}',
            city='Dammam',is_supplier=True,verified_status='verified')
        db.add(org);db.flush()
        db.add_all([Capability(org_id=org.id,category_id=child.id,city=city,confirmed=True) for city in ['Riyadh','Jeddah']])
    db.commit()

sql_times=[]
query_count=0
@event.listens_for(engine,'before_cursor_execute')
def before(conn,cursor,statement,parameters,context,executemany):
    context._directory_started=perf_counter()
@event.listens_for(engine,'after_cursor_execute')
def after(conn,cursor,statement,parameters,context,executemany):
    global query_count
    sql_times.append((perf_counter()-context._directory_started)*1000)
    query_count+=1


def baseline(db):
    query=select(Organization).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),
        Organization.verified_status=='verified').join(Capability,Capability.org_id==Organization.id).join(
        Category,Category.id==Capability.category_id).where(Capability.confirmed.is_(True),
        Capability.accepts_rfqs.is_(True),Capability.eligibility_status=='enabled',Category.active.is_(True),
        or_(Category.id==root_id,Category.parent_id==root_id),Capability.city.ilike('Riyadh')).distinct()
    return public_profiles(db,db.scalars(query.order_by(Organization.name,Organization.id).limit(25)).all(),True)


def optimized(db):
    return discover(db=db,category_id=None,city=None,q='',main_category_id=root_id,subcategory_id=None,
        service_city='Riyadh',verified=None,page=1,limit=25,include_categories=True,include_coverage=False)

result={'environment':'disposable in-memory SQLite, direct synchronous handlers; not PostgreSQL or network latency',
        'suppliers':1000,'capabilities':2000,'iterations':50}
for label,fn in [('baseline',baseline),('optimized',optimized)]:
    times=[];queries=[];sql=[]
    for i in range(55):
        sql_times.clear();query_count=0
        with SessionLocal() as db:
            start=perf_counter();rows=fn(db);elapsed=(perf_counter()-start)*1000
        assert len(rows)==25
        if i>=5:
            times.append(elapsed);queries.append(query_count);sql.append(sum(sql_times))
    result[label]={'handler_p50_ms':round(median(times),3),'handler_p95_ms':round(sorted(times)[47],3),
        'sql_execute_p50_ms':round(median(sql),3),'queries_per_request':max(queries)}
from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    for name,path in [('directory','/api/v1/directory/suppliers?main_category_id='+root_id+'&service_city=Riyadh&include_categories=true&include_coverage=true&limit=12'),('categories','/api/v1/taxonomy/categories?roots_only=true')]:
        timings=[]
        for i in range(55):
            started=perf_counter();response=client.get(path);elapsed=(perf_counter()-started)*1000
            assert response.status_code==200,response.text
            if i>=5:timings.append(elapsed)
        result[name+'_testclient']={'p50_ms':round(median(timings),3),'p95_ms':round(sorted(timings)[47],3)}
print(json.dumps(result,indent=2))
