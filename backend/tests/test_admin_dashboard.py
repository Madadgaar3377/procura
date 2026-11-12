from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Organization, User, RFQ, Outbox, Verification, Invitation
from app.modules.admin.analytics import dashboard_analytics, router
from app.security import current_user


def setup():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI(); app.include_router(router)
    def db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = db
    return engine, sessions, app


def test_empty_dashboard_permissions_and_ranges():
    engine, sessions, app = setup()
    try:
        with TestClient(app) as anonymous:
            assert anonymous.get('/admin/dashboard/analytics').status_code == 401
        app.dependency_overrides[current_user] = lambda: User(is_admin=False)
        with TestClient(app) as client:
            assert client.get('/admin/dashboard/analytics').status_code == 403
            app.dependency_overrides[current_user] = lambda: User(is_admin=True)
            for days in (7, 30, 90):
                response = client.get(f'/admin/dashboard/analytics?days={days}')
                assert response.status_code == 200, response.text
                result = response.json()
                assert len(result['series']) == days
                assert all(value == 0 for value in result['kpis'].values())
                assert result['activity'] == []
                assert result['statuses'] == []
                assert response.headers['cache-control'] == 'no-store'
            assert client.get('/admin/dashboard/analytics?days=14').status_code == 422
            first = client.get('/admin/dashboard/analytics').json()['updated_at']
            assert client.get('/admin/dashboard/analytics').json()['updated_at'] >= first
    finally:
        engine.dispose()


def test_lifetime_totals_daily_history_and_completed_matching_only():
    engine, sessions, app = setup()
    now = datetime.now(UTC)
    try:
        with sessions() as db:
            org = Organization(name='Supplier',name_normalized='supplier',is_supplier=True,verified_status='verified',created_at=now-timedelta(days=45))
            db.add(org); db.flush()
            old = RFQ(reference='OLD',buyer_org_id=org.id,title='Old',city='Riyadh',response_deadline=now+timedelta(days=1),status='closed',created_at=now-timedelta(days=45),published_at=now-timedelta(days=44))
            new = RFQ(reference='NEW',buyer_org_id=org.id,title='New',city='Riyadh',response_deadline=now+timedelta(days=1),status='published')
            db.add_all([old,new]); db.flush()
            db.add_all([Verification(org_id=org.id,status='approved',reviewed_at=now),
                        Verification(org_id=org.id,status='approved',reviewed_at=now),
                        Outbox(event='rfq.published',payload={'rfq_id':old.id},status='processed'),
                        Outbox(event='rfq.published',payload={'rfq_id':new.id},status='failed'),
                        Invitation(rfq_id=new.id,supplier_org_id=org.id,status='accepted')])
            db.commit()
            a = dashboard_analytics(db, 7); b = dashboard_analytics(db, 90)
            assert a['kpis'] == b['kpis']
            assert a['kpis']['active_rfqs'] == 1
            assert sum(row['rfqs_created'] for row in a['series']) == 1
            assert sum(row['rfqs_created'] for row in b['series']) == 2
            assert sum(row['suppliers_verified'] for row in a['series']) == 1
            assert sum(row['suppliers_created'] for row in a['series']) == 0
            assert a['matching']['completed'] == 1
            assert a['matching']['zero_eligible'] == 1
            assert a['matching']['accepted'] == 1
            assert next(x for x in a['alerts'] if x['key']=='failed_matching')['count'] == 1
            assert all('email' not in event and 'phone' not in event for event in a['activity'])
    finally:
        engine.dispose()
