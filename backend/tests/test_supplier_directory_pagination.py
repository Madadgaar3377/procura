"""Public directory paging and filtered totals against an isolated database."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Category, Capability, Organization
from app.modules.capabilities.directory import router


@pytest.fixture
def directory_client():
    engine = create_engine('sqlite:///:memory:', poolclass=StaticPool,
        connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        electrical = Category(id='electrical', code='ELEC', name_en='Electrical', normalized_name='electrical')
        mechanical = Category(id='mechanical', code='MECH', name_en='Mechanical', normalized_name='mechanical')
        db.add_all([electrical, mechanical])
        db.flush()
        db.add(Category(id='panels', code='PANELS', name_en='Panels', normalized_name='panels', parent_id=electrical.id))
        db.flush()
        for index in range(120):
            org = Organization(id=f'supplier-{index}', name=f'Supplier {index:03}',
                name_normalized=f'supplier {index:03}', city='Office city', is_supplier=True,
                is_buyer=False, is_active=True, verified_status='verified')
            db.add(org)
            db.flush()
            for city in [('Riyadh' if index % 2 == 0 else 'Jeddah'), 'Dammam']:
                db.add(Capability(org_id=org.id, category_id='panels' if index % 2 == 0 else 'mechanical',
                    city=city, confirmed=True, accepts_rfqs=True, eligibility_status='enabled',
                    trade_keywords='Switchboards' if index % 2 == 0 else 'Pumps'))
        for name, values in [('Inactive', {'is_active':False}),
                             ('Unverified', {'verified_status':'unverified'}), ('Buyer', {'is_supplier':False})]:
            db.add(Organization(name=name, name_normalized=name.lower(), is_buyer=False,
                **{'is_active':True, 'is_supplier':True, 'verified_status':'verified', **values}))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_fifty_supplier_pages_have_accurate_totals_and_preserve_legacy_list(directory_client):
    client = directory_client
    pages = [client.get(f'/directory/suppliers?paginated=true&page={page}').json() for page in [1, 2, 3, 4]]
    assert [len(page['items']) for page in pages] == [50, 50, 20, 0]
    assert all(page['total'] == 120 and page['limit'] == 50 for page in pages)
    ids = [item['id'] for page in pages for item in page['items']]
    assert len(set(ids)) == len(ids) == 120
    assert pages[1]['items'][0]['name'] == 'Supplier 050'
    legacy = client.get('/directory/suppliers').json()
    assert isinstance(legacy, list) and len(legacy) == 50
    assert client.get('/directory/suppliers?limit=101').status_code == 422


@pytest.mark.parametrize('query,total', [
    ('service_city=Riyadh', 60), ('main_category_id=electrical', 60),
    ('subcategory_id=panels', 60), ('category_id=electrical', 60),
    ('q=Switchboards', 60), ('q=Supplier%201', 20),
    ('q=Supplier%201&main_category_id=electrical&service_city=Riyadh', 10),
    ('service_city=Jeddah&main_category_id=electrical', 0),
    ('service_city=Riy%25', 0),
])
def test_filters_and_counts_apply_to_entire_database(directory_client, query, total):
    first = directory_client.get(f'/directory/suppliers?paginated=true&limit=50&{query}').json()
    second = directory_client.get(f'/directory/suppliers?paginated=true&limit=50&page=2&{query}').json()
    assert first['total'] == second['total'] == total
    assert len(first['items']) == min(total, 50)
    assert len(second['items']) == max(total - 50, 0)
    assert not ({item['id'] for item in first['items']} & {item['id'] for item in second['items']})
    if total == 20:
        assert first['items'][0]['name'] == 'Supplier 100'
