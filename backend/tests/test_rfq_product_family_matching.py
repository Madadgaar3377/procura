"""Regression for the Granada Park "RGB Flood Light" incident, on an isolated database.

Shape of the production data: imported suppliers hold blanket approved capabilities in
every Electrical subcategory; flood-light products live in the Material Master under
Electrical / Third Fix; the buyer filed the line under Electrical / Second Fix. No
supplier communications are sent.

Strict rule: an "RGB Flood Light" line is met only by RGB flood-light product evidence.
Ordinary daylight floodlights, switches, other lighting and category capability alone
are retained for admin review and never shown, counted or invited.
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
                        Outbox, PriceBookItem, Quotation, RFQ, RFQItem, RFQMatch, User)
from app.modules.matching.router import router as matching_router
from app.modules.matching.service import match_and_invite
from app.modules.rfqs.material_matching import _LOCAL_CACHE, classify_material_description
from app.security import current_user

SPECS = ('60 RGB LEDs, 10,000 lm. Optic 26 mm lenses. Tilt Manual. Control USITT DMX 512, RS-485. '
         'Features dedicated white balance channel, master/slave, smooth dimming. IP66. Power supply 100-240V')


def _org(db, key, city='Riyadh', **kw):
    db.add(Organization(id=key, name=key, name_normalized=key, city=city, is_supplier=True, is_buyer=False,
                        verified_status=kw.pop('verified_status', 'verified'), **kw))


def _cap(db, org, category, city='Riyadh', materials=(), source='admin_import', **kw):
    db.add(Capability(org_id=org, category_id=category, city=city, material_ids=list(materials), confirmed=True,
                      accepts_rfqs=True, eligibility_status='enabled', mapping_source=source, **kw))


@pytest.fixture
def incident():
    _LOCAL_CACHE.clear()
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add_all([Category(id='elec', code='ELEC', name_en='Electrical'),
                    Category(id='civil', code='CIVIL', name_en='Civil - Architectural Insulation & Air/Vapor Control'),
                    Category(id='hvac', code='HVAC', name_en='Mechanical - HVAC')])
        db.flush()
        db.add_all([Category(id='first', code='E1', name_en='First Fix', parent_id='elec'),
                    Category(id='second', code='E2', name_en='Second Fix', parent_id='elec'),
                    Category(id='third', code='E3', name_en='Third Fix', parent_id='elec'),
                    Category(id='vapor', code='C1', name_en='Air & Vapor Control Layers', parent_id='civil'),
                    Category(id='hvac-light', code='H1', name_en='Lighting', parent_id='hvac')])
        db.flush()
        materials = {
            'flood-day': ('Flood Light LED Type Day Light', 'third'),
            'flood-made': ('LED Flood Light Made In', 'third'),
            'flood-rgb': ('RGB LED Floodlight DMX 100W', 'third'),
            'emergency': ('Lighting System Emergency Light LED Twin-Head', 'third'),
            'switch': ('Lighting System Way Light Switch Gang Masterseal Wp', 'second'),
            'tie': ('Cable Tie 200mm', 'second'),
        }
        for mid, (name, sub) in materials.items():
            db.add(Material(id=mid, code=mid.upper(), name_en=name, category_id=sub, main_category_id='elec',
                            subcategory_id=sub, unit='No.', discipline='Electrical'))
        db.add(Organization(id='buyer', name='Granada Buyer', name_normalized='granada buyer', is_buyer=True, is_supplier=False))
        # Product evidence through approved Material Master links.
        for key, city in [('flood-riyadh', 'Riyadh'), ('flood-multi', 'Riyadh, Jeddah'), ('flood-blank', ''), ('flood-jeddah', 'Jeddah')]:
            _org(db, key)
            for sub in ('first', 'second'):
                _cap(db, key, sub, city=city)
            _cap(db, key, 'third', city=city, materials=['flood-day', 'flood-made', 'switch'])
        # Genuine RGB flood-light product evidence (linked Material Master product).
        for key, org_city, city in [('rgb-riyadh', 'Riyadh', 'Riyadh'), ('rgb-multi', 'Dammam', 'Riyadh, Jeddah'),
                                    ('rgb-blank', 'Dammam', ''), ('rgb-jeddah', 'Jeddah', 'Jeddah')]:
            _org(db, key, city=org_city)
            _cap(db, key, 'third', city=city, materials=['flood-rgb'])
        db.add(PriceBookItem(org_id='rgb-riyadh', material_id='flood-rgb', main_category_id='elec', subcategory_id='third',
                             supplier_description='RGB LED Floodlight DMX 100W', unit='No.', unit_price=Decimal('540.00'), active=True))
        # Saved private price for a daylight flood light (not the requested product).
        db.add(PriceBookItem(org_id='flood-riyadh', material_id='flood-day', main_category_id='elec', subcategory_id='third',
                             supplier_description='Flood Light LED Type Day Light', unit='No.', unit_price=Decimal('320.00'), active=True))
        # Blanket electrical supplier: every subcategory, but no flood-light product.
        _org(db, 'blanket-electrical')
        for sub in ('first', 'second', 'third'):
            _cap(db, 'blanket-electrical', sub, materials=['switch', 'tie'])
        # Lighting, but a different product (emergency light) must not be the same family.
        _org(db, 'emergency-only')
        _cap(db, 'emergency-only', 'third', materials=['emergency'])
        # Source product wording only; the material link is still pending review.
        _org(db, 'text-only')
        _cap(db, 'text-only', 'third')
        db.add(ImportedSupplierTrade(org_id='text-only', import_batch_id='b1', raw_trade='Disconnect Switch; Outdoor RGB Floodlights 100W',
                                     raw_type='Electrical / Third Fix', raw_location='Riyadh', mapping_status='material_pending_review'))
        # A supplier-declared, approved subcategory capability without listed products.
        _org(db, 'declared-third')
        _cap(db, 'declared-third', 'third', source=None)
        # The buyer's stale Second Fix snapshot must not invite an unrelated
        # supplier when Material Master resolves the product to Third Fix.
        _org(db, 'declared-second')
        _cap(db, 'declared-second', 'second', source=None)
        # Product evidence but suspended.
        _org(db, 'suspended-flood', verified_status='suspended')
        _cap(db, 'suspended-flood', 'third', materials=['flood-day'])
        db.add(User(id='buyer-user', username='buyer-user', phone='+966500000001', password_hash='x', is_active=True))
        db.add(User(id='admin-user', username='admin-user', phone='+966500000002', password_hash='x', is_active=True, is_admin=True))
        db.flush()
        db.add(Member(user_id='buyer-user', organization_id='buyer', role='owner'))
        db.add(RFQ(id='granada', reference='RFQ-GRANADA', buyer_org_id='buyer', title='Granada Park', project_name='Granada Park',
                   city='Riyadh', status='published', response_deadline=datetime.now(UTC) + timedelta(days=3)))
        db.flush()
        db.add(RFQItem(id='rgb', rfq_id='granada', description='T-68 - RGB Flood Light', specifications=SPECS,
                       main_category_id='elec', subcategory_id='second', unit='UNIT', quantity=Decimal('66')))
        db.commit()
    app = FastAPI()
    app.include_router(matching_router, prefix='/api/v1')

    def dependency():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = dependency

    def actor(key):
        with sessions() as db:
            user = db.get(User, key)
        app.dependency_overrides[current_user] = lambda: user
    actor('buyer-user')
    yield sessions, TestClient(app), actor
    engine.dispose()


def _statuses(db):
    return dict(db.execute(select(RFQMatch.supplier_org_id, RFQMatch.status).where(RFQMatch.rfq_id == 'granada')).all())


def test_product_family_beats_misfiled_subcategory_and_blanket_capabilities(incident):
    sessions, client, _ = incident
    with sessions() as db:
        result = match_and_invite(db, db.get(RFQ, 'granada'), bounded=True, limit=3)
        db.commit()
        statuses = _statuses(db)
        eligible = {key for key, status in statuses.items() if status in {'eligible', 'partial'}}
        # RGB flood-light product evidence only (Material Master link or own product wording).
        assert eligible == {'rgb-riyadh', 'rgb-multi', 'text-only'}, statuses
        # Ordinary daylight floodlights, other lighting, switches and category capability
        # alone (including the misfiled Second Fix and blanket imports) are not matches.
        for supplier in ('flood-riyadh', 'flood-multi', 'flood-blank', 'emergency-only', 'declared-third',
                         'declared-second', 'blanket-electrical'):
            assert statuses[supplier] == 'insufficient_evidence', (supplier, statuses[supplier])
        assert statuses['flood-jeddah'] == 'location_mismatch' and statuses['rgb-jeddah'] == 'location_mismatch'
        # RGB product, but service area undeclared and office outside Riyadh.
        assert statuses['rgb-blank'] == 'location_unconfirmed'
        assert statuses['suspended-flood'] == 'suspended'
        invited = set(db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == 'granada')))
        assert invited == eligible and result['new_invitations'] == 3
        reasons = db.scalar(select(RFQMatch.reasons).where(RFQMatch.supplier_org_id == 'rgb-riyadh'))
        assert 'family_item:rgb' in reasons and 'taxonomy:rgb=elec/third' in reasons
        assert 'Product evidence does not confirm technical compliance; confirm in the quotation' in reasons
        assert any(r.startswith(('family_item:rgb', 'alias_item:rgb')) for r in
                   db.scalar(select(RFQMatch.reasons).where(RFQMatch.supplier_org_id == 'text-only')))
        # Imported suppliers without members, contacts or saved prices still participate.
        assert db.scalar(select(func.count()).select_from(Member).where(Member.organization_id == 'rgb-multi')) == 0

    response = client.get('/api/v1/matching/rfqs/granada/results')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['matched_suppliers'] == 3 and data['matching_status'] in {'completed', 'queued'}
    rows = {row['supplier_org_id']: row for row in data['matches']}
    assert set(rows) == eligible
    top = rows['rgb-riyadh']
    assert top['match_strength'] == 'product' and top['matched_item_count'] == 1
    assert top['matched_items'][0]['main_category'] == 'Electrical' and top['matched_items'][0]['subcategory'] == 'Third Fix'
    assert top['saved_price_available'] is True and rows['rgb-multi']['saved_price_available'] is False
    assert rows['rgb-multi']['service_coverage_status'] == 'covered'
    assert not any(r.startswith(('item:', 'taxonomy:', 'family_item:')) for row in data['matches'] for r in row['reasons'])


def test_repeated_matching_is_idempotent_and_preserves_quotations(incident):
    sessions, client, actor = incident
    with sessions() as db:
        match_and_invite(db, db.get(RFQ, 'granada'))
        db.commit()
        invitation = db.scalar(select(Invitation).where(Invitation.supplier_org_id == 'rgb-riyadh'))
        db.add(Quotation(rfq_id='granada', invitation_id=invitation.id, supplier_org_id='rgb-riyadh', status='submitted'))
        invitation.status = 'accepted'
        db.commit()
        counts = (db.scalar(select(func.count()).select_from(RFQMatch)), db.scalar(select(func.count()).select_from(Invitation)),
                  db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event == 'rfq.invitation')))
        for _ in range(3):
            again = match_and_invite(db, db.get(RFQ, 'granada'))
            db.commit()
            assert again['new_invitations'] == 0
        assert counts == (db.scalar(select(func.count()).select_from(RFQMatch)), db.scalar(select(func.count()).select_from(Invitation)),
                          db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event == 'rfq.invitation')))
        assert db.scalar(select(Quotation.status)) == 'submitted'
        assert db.scalar(select(Invitation.status).where(Invitation.supplier_org_id == 'rgb-riyadh')) == 'accepted'
    # Admin repair rerun on an already matched RFQ is also duplicate-free.
    actor('admin-user')
    rerun = client.post('/api/v1/matching/admin/rfqs/granada/rerun')
    assert rerun.status_code == 200 and rerun.json()['job_status'] == 'processed'
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Invitation)) == counts[1]


def test_admin_diagnostics_explain_classification_conflict_and_exclusions(incident):
    sessions, client, actor = incident
    with sessions() as db:
        match_and_invite(db, db.get(RFQ, 'granada'))
        db.commit()
    actor('admin-user')
    data = client.get('/api/v1/matching/admin/rfqs/granada/diagnostics').json()
    item = data['item_diagnostics'][0]
    assert item['subcategory_name'] == 'Second Fix'
    assert item['material_master_subcategory_name'] == 'Third Fix' and item['classification_conflict'] is True
    assert 'Third Fix' in item['classification_note']
    # Daylight floodlights are not RGB floodlights: not product candidates for this line.
    assert set(item['product_candidates']) == {'RGB LED Floodlight DMX 100W'}
    assert item['level_counts'].get('product family', 0) >= 2
    assert data['product_matches'] == 3 and data['invitations_created'] == 3
    excluded = {row['supplier_org_id']: row for row in data['excluded_suppliers']}
    assert "supplier serves 'Jeddah'" in excluded['rgb-jeddah']['reasons'][0]
    assert excluded['suspended-flood']['status'] == 'suspended'
    assert excluded['flood-riyadh']['status'] == 'insufficient_evidence'
    funnel = data['funnel']
    assert funnel['final_eligible_suppliers'] == 3 and funnel['fuzzy_product_matches'] == 3
    assert funnel['insufficient_product_evidence'] == 7 and funnel['location_exclusions'] == 3
    assert funnel['initial_candidates'] >= funnel['main_category_qualified'] >= funnel['subcategory_qualified']
    assert funnel['invitations_active'] == 3


@pytest.mark.parametrize('description', ['RGB Flood Light', 'RGB Floodlight', 'LED RGB Flood Light',
                                         'Outdoor RGB Floodlight', 'DMX RGB Flood Light', 'T-68 - RGB Flood Light'])
def test_classification_variants_resolve_to_material_master_family(incident, description):
    sessions, _, _ = incident
    with sessions() as db:
        result = classify_material_description(db, description, specifications=SPECS, ai_enabled=False)
        assert (result['main_category_id'], result['subcategory_id']) == ('elec', 'third'), result
        # A day-light flood light is not an RGB flood light: never assign a specific material.
        assert result['material_id'] is None
        assert result['category_candidates'][0]['main_category_name'] == 'Electrical'


def test_multi_item_rfq_groups_lines_per_supplier_and_category_only_is_not_matched(incident):
    sessions, client, _ = incident
    with sessions() as db:
        db.add(RFQItem(id='tie', rfq_id='granada', description='Cable Tie 200mm', specifications='',
                       main_category_id='elec', subcategory_id='second', unit='No.', quantity=Decimal('500')))
        db.add(RFQItem(id='misc', rfq_id='granada', description='Electrical consumables', specifications='',
                       main_category_id='elec', unit='LOT', quantity=Decimal('1')))
        _org(db, 'root-only')
        _cap(db, 'root-only', 'elec', source=None, trade_keywords='consumables wholesale')
        db.commit()
        match_and_invite(db, db.get(RFQ, 'granada'))
        db.commit()
        statuses = _statuses(db)
        # blanket-electrical carries the Cable Tie product: it qualifies for that line only.
        blanket = db.scalar(select(Invitation).where(Invitation.supplier_org_id == 'blanket-electrical'))
        assert statuses['blanket-electrical'] == 'partial' and blanket.matched_item_ids == ['tie']
        blanket_reasons = db.scalar(select(RFQMatch.reasons).where(RFQMatch.supplier_org_id == 'blanket-electrical'))
        assert any(r in blanket_reasons for r in ('exact_material_item:tie', 'alias_item:tie'))
        assert 'item:rgb' not in blanket_reasons and 'coverage:1/3' in blanket_reasons
        # One supplier, its own qualifying line only.
        assert db.scalar(select(Invitation.matched_item_ids).where(Invitation.supplier_org_id == 'rgb-riyadh')) == ['rgb']
        # A root-category capability for a specific description is not a match or invitation.
        assert statuses['root-only'] == 'insufficient_evidence'
        assert db.scalar(select(Invitation.id).where(Invitation.supplier_org_id == 'root-only')) is None
    rows = {row['supplier_org_id']: row for row in client.get('/api/v1/matching/rfqs/granada/results?limit=50').json()['matches']}
    assert 'root-only' not in rows and rows['blanket-electrical']['matched_item_count'] == 1


def test_notification_failure_does_not_block_matching_results(incident, monkeypatch):
    sessions, client, _ = incident
    with sessions() as db:
        match_and_invite(db, db.get(RFQ, 'granada'))
        db.commit()
    import app.modules.notifications.procurement as procurement
    monkeypatch.setattr(procurement, 'prepare', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('provider down')))
    from app.notification_worker import process_batch
    with sessions() as db:
        process_batch(db, limit=50)
    with sessions() as db:
        failed = db.scalars(select(Outbox.status).where(Outbox.event == 'rfq.invitation')).all()
        assert failed and set(failed) <= {'retry', 'failed'}
    results = client.get('/api/v1/matching/rfqs/granada/results')
    assert results.status_code == 200 and results.json()['matched_suppliers'] == 3
    status = client.get('/api/v1/matching/rfqs/granada/notifications-status')
    assert status.status_code == 200 and status.json()['rfq_id'] == 'granada'


def test_thousands_of_blanket_imports_and_daylight_floodlights_are_never_matched(incident):
    sessions, _, _ = incident
    with sessions() as db:
        for number in range(3000):
            key = f'bulk-{number:04d}'
            db.add(Organization(id=key, name=key, name_normalized=key, city='Riyadh', is_supplier=True, is_buyer=False,
                                verified_status='verified'))
            for sub in ('first', 'second', 'third'):
                db.add(Capability(org_id=key, category_id=sub, city='Riyadh', material_ids=['switch'], confirmed=True,
                                  accepts_rfqs=True, eligibility_status='enabled', mapping_source='admin_import'))
        for number in range(200):
            key = f'rgbsup-{number:03d}'
            db.add(Organization(id=key, name=key, name_normalized=key, city='Riyadh', is_supplier=True, is_buyer=False,
                                verified_status='verified'))
            db.add(Capability(org_id=key, category_id='third', city='Riyadh', material_ids=['flood-rgb'], confirmed=True,
                              accepts_rfqs=True, eligibility_status='enabled', mapping_source='admin_import'))
        for number in range(100):
            key = f'daylight-{number:03d}'
            db.add(Organization(id=key, name=key, name_normalized=key, city='Riyadh', is_supplier=True, is_buyer=False,
                                verified_status='verified'))
            db.add(Capability(org_id=key, category_id='third', city='Riyadh', material_ids=['flood-made'], confirmed=True,
                              accepts_rfqs=True, eligibility_status='enabled', mapping_source='admin_import'))
        db.commit()
        started = time.perf_counter()
        result = match_and_invite(db, db.get(RFQ, 'granada'), bounded=True, limit=100)
        elapsed = time.perf_counter() - started
        db.commit()
        # 200 RGB product suppliers + 3 fixture suppliers. 3,000 blanket imports and 100
        # daylight flood-light suppliers are evaluated but none is a match.
        assert result['eligible_suppliers'] == 203
        bulk = db.scalars(select(RFQMatch.status).where(RFQMatch.supplier_org_id.like('bulk-%'))).all()
        assert len(bulk) == 3000 and set(bulk) == {'insufficient_evidence'}
        assert set(db.scalars(select(RFQMatch.status).where(RFQMatch.supplier_org_id.like('daylight-%')))) == {'insufficient_evidence'}
        # Never invited, so no email/SMS goes to them.
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.supplier_org_id.like('bulk-%'))) == 0
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.supplier_org_id.like('daylight-%'))) == 0
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.supplier_org_id.like('rgbsup-%'))) == 200
        assert elapsed < 30, elapsed
