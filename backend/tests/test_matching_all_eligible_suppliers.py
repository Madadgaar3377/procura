"""Matching must return every eligible supplier (RFQ-F14D3711EC55 stopped at five).

Isolated SQLite fixtures mirroring the production taxonomy of the four-material RFQ:
imported suppliers with subcategory capabilities and no products are subcategory
matches; blanket imports and main-category capabilities are broad (shown, never
auto-invited); unrelated suppliers are not matched; counts are database totals.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (Capability, Category, ImportedSupplierTrade, Invitation, Material, Member, Organization,
                        Outbox, RFQ, RFQItem, RFQMatch, User)
from app.modules.matching.router import router as matching_router
from app.modules.matching.service import match_and_invite, run_matching_job
from app.security import current_user

MECH = 'Mechanical - Multi-System (Plumbing / Fire / HVAC)'


def org(db, key, city='Riyadh', **kw):
    db.add(Organization(id=key, name=key, name_normalized=key, city=city, is_supplier=True, is_buyer=False,
                        verified_status=kw.pop('verified_status', 'verified'), **kw))


def cap(db, org_id, category, city='', materials=(), source='admin_import', **kw):
    db.add(Capability(org_id=org_id, category_id=category, city=city, material_ids=list(materials), confirmed=True,
                      accepts_rfqs=True, eligibility_status='enabled', mapping_source=source, **kw))


@pytest.fixture
def env():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add_all([Category(id='mech', code='MECH', name_en=MECH), Category(id='elec', code='ELEC', name_en='Electrical'),
                    Category(id='civil', code='CIV', name_en='Civil')])
        db.flush()
        db.add_all([Category(id='fittings', code='M1', name_en='Fittings', parent_id='mech'),
                    Category(id='pipes', code='M2', name_en='Pipes', parent_id='mech'),
                    Category(id='valves', code='M3', name_en='Valves', parent_id='mech'),
                    Category(id='consumable', code='E1', name_en='Consumable Material', parent_id='elec'),
                    Category(id='wiring', code='E2', name_en='Wiring Devices', parent_id='elec'),
                    Category(id='lighting', code='E3', name_en='Lighting', parent_id='elec'),
                    Category(id='concrete', code='C1', name_en='Concrete', parent_id='civil')])
        db.flush()
        for mid, name, main, sub in [('plug-mi', 'Plug MI 1/2 inch', 'mech', 'fittings'), ('gi-pipe', 'GI Pipe 2 inch', 'mech', 'pipes'),
                                     ('female-plug', 'Female Plug 13A', 'elec', 'wiring'),
                                     ('3way', '3 Way Female Plug Socket', 'elec', 'wiring')]:
            db.add(Material(id=mid, code=mid.upper(), name_en=name, category_id=sub, main_category_id=main,
                            subcategory_id=sub, unit='PCS', active=True))
        db.add(Organization(id='buyer', name='3D International Technical Services Est.', name_normalized='3d intl',
                            is_buyer=True, is_supplier=False))
        db.add(User(id='buyer-user', username='buyer-user', phone='+966500000011', password_hash='x', is_active=True))
        db.flush()
        db.add(Member(user_id='buyer-user', organization_id='buyer', role='owner'))

        # Product evidence (portal supplier linked to the Material Master).
        org(db, 'exact-fittings'); cap(db, 'exact-fittings', 'fittings', materials=['plug-mi'], source=None)
        # Imported suppliers: subcategory capability only, no products, no prices, no members.
        org(db, 'imp-fittings'); cap(db, 'imp-fittings', 'fittings')
        org(db, 'imp-pipes'); cap(db, 'imp-pipes', 'pipes')
        org(db, 'imp-consumable'); cap(db, 'imp-consumable', 'consumable')
        # Material Master subcategory of the plugs (buyer chose Consumable Material).
        org(db, 'imp-wiring'); cap(db, 'imp-wiring', 'wiring')
        # Fuzzy description evidence from imported product wording.
        org(db, 'text-plugs'); cap(db, 'text-plugs', 'lighting')
        db.add(ImportedSupplierTrade(org_id='text-plugs', import_batch_id='b1', raw_trade='Electrical accessories',
                                     raw_products='Female plugs 13A; 3-way female plug adaptor', raw_type='Electrical',
                                     raw_location='Riyadh', mapping_status='mapped'))
        # Blanket import: every Mechanical subcategory -> broad only, never invited.
        org(db, 'blanket-mech')
        for sub in ('fittings', 'pipes', 'valves'):
            cap(db, 'blanket-mech', sub)
        # Main-category capability only -> broad recommendation.
        org(db, 'main-elec'); cap(db, 'main-elec', 'elec', source=None)
        # Unrelated: same discipline, different subcategory; and another discipline.
        org(db, 'valves-only'); cap(db, 'valves-only', 'valves', source=None)
        org(db, 'civil-only'); cap(db, 'civil-only', 'concrete')
        # Excluded by status.
        org(db, 'suspended-fittings', verified_status='suspended'); cap(db, 'suspended-fittings', 'fittings')
        db.add(RFQ(id='rfq4', reference='RFQ-F14D3711EC55', buyer_org_id='buyer', title='Materials',
                   project_name='3D International Technical Services Est.', city='', status='published',
                   response_deadline=datetime.now(UTC) + timedelta(days=5)))
        db.flush()
        for iid, desc, main, sub, qty, unit in [('i-plug', 'Plug Mi', 'mech', 'fittings', 1000, 'PCS'),
                                                ('i-pipe', 'Pipe', 'mech', 'pipes', 100, 'PIPE'),
                                                ('i-3way', '3way Female Plug', 'elec', 'consumable', 100, 'PCS'),
                                                ('i-female', 'Female Plug', 'elec', 'consumable', 100, 'PCS')]:
            db.add(RFQItem(id=iid, rfq_id='rfq4', description=desc, specifications='', main_category_id=main,
                           subcategory_id=sub, unit=unit, quantity=Decimal(qty)))
        db.commit()
    app = FastAPI()
    app.include_router(matching_router, prefix='/api/v1')

    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with sessions() as db:
        user = db.get(User, 'buyer-user')
    app.dependency_overrides[current_user] = lambda: user
    yield sessions, TestClient(app), engine
    engine.dispose()


def statuses(db, rfq_id='rfq4'):
    return dict(db.execute(select(RFQMatch.supplier_org_id, RFQMatch.status).where(RFQMatch.rfq_id == rfq_id)).all())


def reasons(db, supplier, rfq_id='rfq4'):
    return db.scalar(select(RFQMatch.reasons).where(RFQMatch.rfq_id == rfq_id, RFQMatch.supplier_org_id == supplier))


def invited(db, rfq_id='rfq4'):
    return set(db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq_id)))


def bulk_suppliers(sessions, count, sub='consumable', city=''):
    with sessions() as db:
        for n in range(count):
            key = f'bulk-{sub}-{n:04d}'
            org(db, key)
            cap(db, key, sub, city=city)
        db.commit()


def test_four_material_rfq_matches_every_eligible_supplier(env):
    sessions, client, _ = env
    with sessions() as db:
        result = match_and_invite(db, db.get(RFQ, 'rfq4'), bounded=True, limit=3)
        db.commit()
        found = statuses(db)
        matched = {k for k, v in found.items() if v in {'eligible', 'partial'}}
        assert matched == {'exact-fittings', 'imp-fittings', 'imp-pipes', 'imp-consumable', 'imp-wiring',
                           'text-plugs', 'blanket-mech', 'main-elec'}, found
        assert result['eligible_suppliers'] == 8
        # Unrelated suppliers are never matched solely by discipline.
        assert found.get('valves-only') in {None, 'ineligible'} and 'civil-only' not in found
        assert found['suspended-fittings'] == 'suspended'
        # Strongest reason per supplier and line; fuzzy/category never labelled exact.
        # "Plug Mi" is a one-word product after normalization ("mi" is noise): too vague for
        # a fuzzy product match, so the linked supplier is a subcategory match, not exact.
        assert 'exact_subcategory_item:i-plug' in reasons(db, 'exact-fittings')
        assert 'exact_subcategory_item:i-plug' in reasons(db, 'imp-fittings')
        assert not any(r.startswith('exact_material_item:') for r in reasons(db, 'imp-fittings'))
        assert {'exact_subcategory_item:i-3way', 'exact_subcategory_item:i-female'} <= set(reasons(db, 'imp-consumable'))
        assert 'exact_subcategory_item:i-female' in reasons(db, 'imp-wiring')
        assert any(r.startswith(('family_item:i-female', 'alias_item:i-female')) for r in reasons(db, 'text-plugs'))
        assert 'broad_only' in reasons(db, 'blanket-mech') and 'broad_only' in reasons(db, 'main-elec')
        assert 'delivery location not specified; city coverage not used' in reasons(db, 'imp-pipes')
        # One invitation per supplier; broad matches are not invited.
        assert invited(db) == matched - {'blanket-mech', 'main-elec'}
        assert db.scalar(select(Invitation.matched_item_ids).where(Invitation.supplier_org_id == 'imp-consumable')) == ['i-3way', 'i-female']
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event == 'rfq.invitation')) == 6

    data = client.get('/api/v1/matching/rfqs/rfq4/results').json()
    assert data['matched_suppliers'] == 8 and data['invited_suppliers'] == 6
    assert data['subcategory_matches'] == 5 and data['main_category_matches'] == 2
    assert data['exact_material_matches'] == 0 and data['product_description_matches'] == 1
    assert data['excluded_by_status'].get('suspended') == 1 and data['excluded_suppliers'] == data['suppliers_evaluated'] - 8
    # Matched suppliers come before excluded ones on the first page.
    page = [row['status'] for row in data['matches']]
    assert all(status in {'eligible', 'partial'} for status in page[:8])
    assert not any(status in {'eligible', 'partial'} for status in page[8:])


def test_exact_material_id_match_is_exact(env):
    sessions, _, _ = env
    with sessions() as db:
        db.add(RFQ(id='rfq-exact', reference='RFQ-EXACT', buyer_org_id='buyer', title='Exact', city='', status='published',
                   response_deadline=datetime.now(UTC) + timedelta(days=5)))
        db.flush()
        db.add(RFQItem(id='e1', rfq_id='rfq-exact', material_id='plug-mi', description='Plug MI 1/2 inch', specifications='',
                       main_category_id='mech', subcategory_id='fittings', unit='PCS', quantity=Decimal(5)))
        db.commit()
        match_and_invite(db, db.get(RFQ, 'rfq-exact'))
        db.commit()
        assert 'exact_material_item:e1' in reasons(db, 'exact-fittings', 'rfq-exact')
        assert 'exact_subcategory_item:e1' in reasons(db, 'imp-fittings', 'rfq-exact')


def test_delivery_city_and_multiple_service_cities(env):
    sessions, _, _ = env
    with sessions() as db:
        org(db, 'multi-city'); cap(db, 'multi-city', 'pipes', city='Jeddah, Riyadh')
        org(db, 'jeddah-only'); cap(db, 'jeddah-only', 'pipes', city='Jeddah')
        db.get(RFQ, 'rfq4').city = 'Riyadh'
        db.commit()
        match_and_invite(db, db.get(RFQ, 'rfq4'))
        db.commit()
        found = statuses(db)
        assert found['multi-city'] in {'eligible', 'partial'}
        assert 'delivery coverage unconfirmed' not in reasons(db, 'multi-city')
        assert found['jeddah-only'] == 'location_mismatch'
        # Blank capability city: included, coverage clearly marked unconfirmed.
        assert found['imp-pipes'] in {'eligible', 'partial'}
        assert 'delivery coverage unconfirmed' in reasons(db, 'imp-pipes')


def test_optional_and_mandatory_brands(env):
    sessions, _, _ = env
    with sessions() as db:
        org(db, 'abb-dealer'); cap(db, 'abb-dealer', 'consumable', brands=['ABB'])
        db.get(RFQItem, 'i-female').specifications = 'Brand: ABB'
        db.commit()
        match_and_invite(db, db.get(RFQ, 'rfq4'))
        db.commit()
        # Optional brand: nobody excluded; unconfirmed brands are labelled as such.
        assert statuses(db)['imp-consumable'] in {'eligible', 'partial'}
        assert any(r.startswith('Brand compliance is unverified') for r in reasons(db, 'imp-consumable'))
        assert any(r.startswith('Brand requirement matches') for r in reasons(db, 'abb-dealer'))

        db.get(RFQItem, 'i-female').specifications = 'Brand: ABB only'
        db.get(RFQItem, 'i-3way').specifications = 'Brand: ABB only'
        db.commit()
        match_and_invite(db, db.get(RFQ, 'rfq4'))
        db.commit()
        found = statuses(db)
        assert found['abb-dealer'] in {'eligible', 'partial'}
        assert found['imp-consumable'] == 'brand_mismatch'
        assert 'Mandatory brand not confirmed' in reasons(db, 'imp-consumable')[0]


@pytest.mark.parametrize('count', [100, 500, 1000])
def test_every_eligible_supplier_is_returned_and_paginated(env, count):
    sessions, client, _ = env
    bulk_suppliers(sessions, count)
    with sessions() as db:
        started = time.perf_counter()
        result = match_and_invite(db, db.get(RFQ, 'rfq4'), bounded=True, limit=100)
        elapsed = time.perf_counter() - started
        db.commit()
        assert result['eligible_suppliers'] == count + 8
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.supplier_org_id.like('bulk-%'))) == count
        assert elapsed < 60, elapsed
    first = client.get('/api/v1/matching/rfqs/rfq4/results?limit=25&offset=0').json()
    assert first['matched_suppliers'] == count + 8 and len(first['matches']) == 25
    seen = set()
    for offset in range(0, count + 8, 25):
        page = client.get(f'/api/v1/matching/rfqs/rfq4/results?limit=25&offset={offset}').json()['matches']
        seen.update(row['supplier_org_id'] for row in page if row['status'] in {'eligible', 'partial'})
    assert len(seen) == count + 8


def test_duplicate_matching_requests_are_idempotent(env):
    sessions, _, _ = env
    with sessions() as db:
        match_and_invite(db, db.get(RFQ, 'rfq4'))
        db.commit()
        counts = [db.scalar(select(func.count()).select_from(model)) for model in (RFQMatch, Invitation, Outbox)]
        again = match_and_invite(db, db.get(RFQ, 'rfq4'))
        db.commit()
        assert again['new_invitations'] == 0
        assert counts == [db.scalar(select(func.count()).select_from(model)) for model in (RFQMatch, Invitation, Outbox)]


def test_interrupted_matching_resumes_from_cursor_without_duplicates(env):
    sessions, client, engine = env
    bulk_suppliers(sessions, 120)
    with sessions() as db:
        job = Outbox(event='rfq.published', payload={'rfq_id': 'rfq4'}, dedupe_key='rfq-published:rfq4', status='pending')
        db.add(job)
        db.commit()
        # First worker page, then the worker "dies" leaving only the cursor behind.
        first = match_and_invite(db, db.get(RFQ, 'rfq4'), bounded=True, single_batch=True, limit=25)
        assert first['next_cursor']
        job = db.get(Outbox, job.id)
        job.payload = {**job.payload, 'cursor': first['next_cursor'],
                       'progressed_at': (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
        db.commit()
        job_id = job.id
    # A stopped worker is reported, never left "in progress" forever.
    stalled = client.get('/api/v1/matching/rfqs/rfq4/results').json()
    assert stalled['matching_status'] == 'failed' and 'resumes automatically' in stalled['explanation']
    # Restarted worker continues from the cursor until no candidates remain.
    while True:
        with sessions() as db:
            if db.get(Outbox, job_id).status != 'pending':
                break
        run_matching_job(engine, job_id, time_budget=None)
    with sessions() as db:
        assert db.get(Outbox, job_id).status == 'processed'
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.supplier_org_id.like('bulk-%'))) == 120
        assert db.scalar(select(func.count()).select_from(Invitation)) == len(invited(db))
    done = client.get('/api/v1/matching/rfqs/rfq4/results').json()
    assert done['matching_status'] == 'completed' and done['matched_suppliers'] == 128
