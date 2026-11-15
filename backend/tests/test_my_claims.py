from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import CompanyClaim, Organization, Member, User
from app.modules.organizations.router import router
from app.security import current_user


def test_my_claims_are_private_and_do_not_create_memberships_or_companies():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        with sessions() as db:
            users = [User(username=f'claimant{i}', phone=f'+96650000000{i}', phone_e164=f'+96650000000{i}', password_hash='unused') for i in range(2)]
            org = Organization(name='Existing supplier', name_normalized='existing supplier', is_supplier=True)
            db.add_all([*users, org]); db.flush()
            ids = [u.id for u in users]
            db.add_all([CompanyClaim(org_id=org.id, claimant_id=u.id, evidence=f'Private evidence {i}') for i, u in enumerate(users)])
            db.commit()
        with TestClient(app) as client:
            assert client.get('/organizations/claims/mine').status_code == 401
            for user_id in ids:
                app.dependency_overrides[current_user] = lambda: User(id=user_id)
                result = client.get('/organizations/claims/mine')
                assert result.status_code == 200, result.text
                assert len(result.json()) == 1
                assert result.json()[0]['claimant_id'] == user_id
                assert result.json()[0]['status'] == 'pending'
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(Organization)) == 1
            assert db.scalar(select(func.count()).select_from(Member)) == 0
    finally:
        engine.dispose()
