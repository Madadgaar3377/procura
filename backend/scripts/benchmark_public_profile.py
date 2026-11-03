"""Public-profile timings on disposable synthetic SQLite only, never the configured DB."""
import json
import os
import sys
from pathlib import Path
from statistics import median
from time import perf_counter

os.environ.update(APP_ENV='test',DATABASE_URL='sqlite:///:memory:',REDIS_URL='',
 MOCK_NOTIFICATIONS='true',AUTH_DELIVERY_AUTO_DISPATCH='false',JWT_SECRET='profile-benchmark-local-only-secret-12345')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Capability, Category, Organization, PublicProfileConsent

assert str(engine.url)=='sqlite:///:memory:'
Base.metadata.create_all(engine)
with SessionLocal() as db:
    main=Category(code='BENCH-E',name_en='Electrical');db.add(main);db.flush()
    tools=Category(code='BENCH-T',name_en='Tools',parent_id=main.id);db.add(tools);db.flush()
    first=None
    for i in range(500):
        org=Organization(name=f'Profile benchmark {i:04d}',name_normalized=f'profile benchmark {i:04d}',
            city='Dammam',is_supplier=True,verified_status='verified',business_phone='+966500001234',
            business_email='sales@example.test',description='Synthetic benchmark introduction')
        db.add(org);db.flush()
        if first is None:first=org.id
        db.add_all([Capability(org_id=org.id,category_id=tools.id,city=city,confirmed=True) for city in ['Riyadh','Jeddah']])
    db.add(PublicProfileConsent(org_id=first,fields=['description','business_phone','business_email']));db.commit()
queries=[];sql=[]
@event.listens_for(engine,'before_cursor_execute')
def before(conn,cursor,statement,parameters,context,executemany):
    context._profile_started=perf_counter()
    queries.append(statement.split()[0])
@event.listens_for(engine,'after_cursor_execute')
def after(conn,cursor,statement,parameters,context,executemany):
    sql.append((perf_counter()-context._profile_started)*1000)
result={'environment':'disposable SQLite, in-process TestClient, no network/PostgreSQL/Redis','suppliers':500,'capabilities':1000,'iterations':50}
with TestClient(app) as client:
    for name,path in [('detail',f'/api/v1/directory/suppliers/{first}?include_profile=true&include_categories=true'),('related',f'/api/v1/directory/suppliers/{first}/related?limit=4')]:
        timings=[];counts=[];sql_times=[]
        for i in range(55):
            queries.clear();sql.clear();started=perf_counter();response=client.get(path);elapsed=(perf_counter()-started)*1000
            assert response.status_code==200,response.text
            if name=='related':assert len(response.json())==4
            if i>=5:timings.append(elapsed);counts.append(len(queries));sql_times.append(sum(sql))
        result[name]={'p50_ms':round(median(timings),3),'p95_ms':round(sorted(timings)[47],3),
            'max_database_statements_including_schema_checks':max(counts),'sql_execute_p50_ms':round(median(sql_times),3)}
print(json.dumps(result,indent=2))
