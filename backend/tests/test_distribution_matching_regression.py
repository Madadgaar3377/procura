"""Production incident regression; isolated DB, no supplier communications."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (AuditEvent, Capability, Category, Invitation, Material, Member,
                        Organization, Outbox, RFQ, RFQItem, RFQMatch, User)
from app.modules.matching.router import router as matching_router
from app.modules.matching.service import match_and_invite
from app.modules.notifications.procurement import eligible_orgs, prepare
from app.modules.quotations.router import router as quotation_router
from app.security import current_user

RFQ_ID = 'f1eeb71e-0858-41d7-937d-1daffafecc81'


@pytest.fixture
def incident():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        buyer = Organization(id='buyer', name='Buyer', name_normalized='buyer', is_buyer=True, is_supplier=False)
        db.add(buyer)
        db.add_all([Category(id='electrical', code='ELEC', name_en='Electrical'),
                    Category(id='panels', code='PANEL', name_en='Distribution Panels', parent_id='electrical'),
                    Category(id='ups', code='UPS', name_en='UPS', parent_id='electrical')])
        db.flush()
        db.add_all([Material(id='requested', code='REQUESTED', name_en='Distribution Panels; Brands: ABB',
                            category_id='panels', main_category_id='electrical', subcategory_id='panels', unit='PCS'),
                    Material(id='supplier-panel', code='SUPPLIER-PANEL', name_en='Distribution Panels',
                            category_id='panels', main_category_id='electrical', subcategory_id='panels', unit='PCS'),
                    Material(id='supplier-ups', code='SUPPLIER-UPS', name_en='UPS',
                            category_id='ups', main_category_id='electrical', subcategory_id='ups', unit='PCS')])
        cases = [('z-exact', 'electrical', 'Jeddah', ['requested']),
                 ('z-sub', 'panels', 'Jeddah', []),
                 ('z-linked', 'electrical', 'Jeddah', ['supplier-panel']),
                 ('a-ups', 'electrical', 'Jeddah', ['supplier-ups']),
                 ('b-generic', 'electrical', 'Jeddah', []),
                 ('c-riyadh', 'panels', 'Riyadh', []),
                 ('d-unknown', 'panels', '', []),
                 ('e-unapproved', 'panels', 'Jeddah', [])]
        for key, category, city, materials in cases:
            db.add(Organization(id=key, name=key, name_normalized=key, is_supplier=True,
                                is_buyer=False, verified_status='verified', city='Jeddah'))
            db.add(Capability(org_id=key, category_id=category, city=city, material_ids=materials,
                              confirmed=key != 'e-unapproved', accepts_rfqs=True, eligibility_status='enabled'))
        for key, org in [('buyer-user', 'buyer'), ('supplier-user', 'z-linked'), ('outsider', None)]:
            db.add(User(id=key, username=key, phone='+9665' + str(len(key)).zfill(8), password_hash='test', is_active=True))
            if org:
                db.add(Member(user_id=key, organization_id=org, role='owner'))
        rfq = RFQ(id=RFQ_ID, reference='RFQ-A647A644745D', buyer_org_id='buyer', title='Distribution Panels',
                  city='Jeddah', status='published', response_deadline=datetime.now(UTC) + timedelta(days=5))
        db.add(rfq); db.flush()
        db.add(RFQItem(id='item', rfq_id=RFQ_ID, material_id='requested', main_category_id='electrical',
                       subcategory_id='panels', description='Distribution Panels; Brands: ABB', specifications='', unit='PCS', quantity=100))
        # Historical incorrect invitations must be retained and flagged, never deleted.
        for key in ['a-ups', 'b-generic']:
            db.add(RFQMatch(rfq_id=RFQ_ID, supplier_org_id=key, score=60, status='eligible', reasons=[]))
            db.add(Invitation(rfq_id=RFQ_ID, supplier_org_id=key, status='invited'))
        db.commit()
    app = FastAPI()
    app.include_router(matching_router, prefix='/api/v1')
    app.include_router(quotation_router, prefix='/api/v1')
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


def test_strict_matching_all_batches_idempotency_and_historical_audit(incident):
    sessions, client, actor = incident
    with sessions() as db:
        result = match_and_invite(db, db.get(RFQ, RFQ_ID), limit=2, bounded=True)
        db.commit()
        assert result['eligible_suppliers'] == 3
        assert result['new_invitations'] == 3
        assert {inv.supplier_org_id for inv in result['invitations']} == {'z-exact', 'z-sub', 'z-linked'}
        statuses = dict(db.execute(select(RFQMatch.supplier_org_id, RFQMatch.status)).all())
        assert statuses['a-ups'] == statuses['b-generic'] == 'ineligible'
        assert statuses['c-riyadh'] == 'location_mismatch'
        assert statuses['d-unknown'] == 'coverage_unknown'
        assert statuses['e-unapproved'] == 'pending_approval'
        assert len(db.scalars(select(AuditEvent).where(AuditEvent.action == 'rfq.match_eligibility_corrected')).all()) == 2
        again = match_and_invite(db, db.get(RFQ, RFQ_ID), limit=2)
        db.commit()
        assert again['new_invitations'] == 0
        assert len(db.scalars(select(Invitation)).all()) == 5
        assert eligible_orgs(db, db.get(RFQ, RFQ_ID), list(statuses)) == {'z-exact', 'z-sub', 'z-linked'}
        old = db.scalar(select(Invitation).where(Invitation.supplier_org_id == 'a-ups'))
        event = Outbox(event='rfq.invitation', payload={'rfq_id': RFQ_ID, 'supplier_org_id': 'a-ups', 'invitation_id': old.id})
        db.add(event); db.flush(); prepare(db, event)
        assert event.status == 'skipped'
        assert old.status == 'invited'
    response = client.get(f'/api/v1/matching/rfqs/{RFQ_ID}/results')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['matched_suppliers'] == 3
    for match in data['matches']:
        assert not any(reason.startswith(('main_category_item:', 'exact_materials:', 'item:')) for reason in match['reasons'])
        assert 'unverified' in match['brand_requirement_status']
    assert next(m for m in data['matches'] if m['supplier_org_id'] == 'a-ups')['invitation_review_required']


def test_supplier_accept_submit_and_buyer_compare_with_authorization(incident):
    sessions, client, actor = incident
    response = client.post(f'/api/v1/matching/rfqs/{RFQ_ID}/dispatch')
    assert response.status_code == 200, response.text
    actor('supplier-user')
    invitations = client.get('/api/v1/matching/invitations').json()
    assert len(invitations) == 1
    invitation_id = invitations[0]['id']
    assert client.post(f'/api/v1/matching/invitations/{invitation_id}/accept').status_code == 200
    draft = client.post(f'/api/v1/quotations/invitations/{invitation_id}/auto-draft')
    assert draft.status_code == 200, draft.text
    quote_id = draft.json()['quotation']['id']
    edited = client.put(f'/api/v1/quotations/{quote_id}/lines', json={'lines': [
        {'rfq_item_id': 'item', 'brand': 'ABB', 'compliance': 'unconfirmed', 'quantity': 100,
         'unit_price': 250, 'lead_days': 7}]})
    assert edited.status_code == 200, edited.text
    actor('buyer-user')
    assert client.get(f'/api/v1/quotations/{quote_id}').status_code == 404
    actor('supplier-user')
    submitted = client.post(f'/api/v1/quotations/{quote_id}/submit')
    assert submitted.status_code == 200, submitted.text
    actor('buyer-user')
    quote = client.get(f'/api/v1/quotations/{quote_id}')
    assert quote.status_code == 200 and quote.json()['quotation']['status'] == 'submitted'
    compared = client.get(f'/api/v1/quotations/rfqs/{RFQ_ID}/comparison')
    assert compared.status_code == 200, compared.text
    assert quote_id in compared.text and 'ABB' in compared.text
    actor('outsider')
    assert client.get(f'/api/v1/quotations/{quote_id}').status_code == 404
    assert client.get(f'/api/v1/matching/rfqs/{RFQ_ID}/results').status_code == 404


def test_brand_evidence_requires_relevant_approved_product_metadata(incident):
    sessions, client, _ = incident
    with sessions() as db:
        product = db.get(Material, 'supplier-panel')
        product.specs_template = {'Brand': 'ABB'}
        db.commit()
    assert client.post(f'/api/v1/matching/rfqs/{RFQ_ID}/dispatch').status_code == 200
    rows = client.get(f'/api/v1/matching/rfqs/{RFQ_ID}/results').json()['matches']
    assert 'saved product data' in next(m for m in rows if m['supplier_org_id'] == 'z-linked')['brand_requirement_status']
    assert 'unverified' in next(m for m in rows if m['supplier_org_id'] == 'z-exact')['brand_requirement_status']
    assert any('authorization require quotation confirmation' in reason for m in rows
               if m['supplier_org_id'] == 'z-linked' for reason in m['reasons'])


def test_coverage_cannot_be_borrowed_from_an_unrelated_product(incident):
    sessions, _, _ = incident
    with sessions() as db:
        cap = db.scalar(select(Capability).where(Capability.org_id == 'z-exact'))
        cap.city = 'Riyadh'
        db.add(Capability(org_id='z-exact', category_id='ups', city='Jeddah', confirmed=True,
                          material_ids=['supplier-ups'], eligibility_status='enabled'))
        db.flush()
        result = match_and_invite(db, db.get(RFQ, RFQ_ID))
        assert 'z-exact' not in {i.supplier_org_id for i in result['invitations']}
        assert db.scalar(select(RFQMatch.status).where(RFQMatch.supplier_org_id == 'z-exact')) == 'location_mismatch'


def test_explicit_taxonomy_repair_is_audited_idempotent_and_preserves_material_id(incident):
    from scripts.correct_rfq_matching import reclassify
    sessions, _, _ = incident
    with sessions() as db:
        product = db.get(Material, 'supplier-panel')
        product.category_id = 'electrical'
        product.subcategory_id = None
        original_version = product.version
        db.flush()
        before = match_and_invite(db, db.get(RFQ, RFQ_ID))
        assert 'z-linked' not in {i.supplier_org_id for i in before['invitations']}
        assert reclassify(db, product.id, 'panels')
        db.flush()
        assert not reclassify(db, product.id, 'panels')
        assert product.id == 'supplier-panel' and product.version == original_version + 1
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == 'material.rfq_taxonomy_correction'))
        after = match_and_invite(db, db.get(RFQ, RFQ_ID))
        assert {i.supplier_org_id for i in after['invitations']} == {'z-linked'}
