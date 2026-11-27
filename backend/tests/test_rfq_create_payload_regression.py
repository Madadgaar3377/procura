"""Regression: buyer RFQ create returned 422 for payloads the RFQ form really sends.

Blank select values ("" for material/subcategory) and null specifications are
normalized; genuinely invalid lines still fail with a line-located 422.
"""
from datetime import UTC, datetime, timedelta
import uuid

from sqlalchemy import func, select

from app.models import Capability, Category, Invitation, Material, Organization, Member, RFQ, RFQItem, User
from tests.test_rfq_material_import import env  # noqa: F401  (shared isolated fixture)


def seed_disciplines(e):
    with e.db() as db:
        for key, name, sub in [('el', 'Electrical', 'Cables'), ('cv', 'Civil', 'Concrete'), ('me', 'Mechanical', 'Valves')]:
            db.add(Category(id=key, code=key.upper(), name_en=name, normalized_name=name.lower()))
            db.flush()
            db.add(Category(id=f'{key}-sub', code=f'{key.upper()}S', name_en=sub, parent_id=key, normalized_name=sub.lower()))
        db.flush()
        db.add_all([
            Material(id='cable-1', code='CBL-1', name_en='XLPE Cable 4x16mm', normalized_description='xlpe cable 4x16mm',
                     category_id='el-sub', main_category_id='el', subcategory_id='el-sub', unit='M', active=True),
            Material(id='valve-1', code='VLV-1', name_en='Gate Valve DN100', normalized_description='gate valve dn100',
                     category_id='me-sub', main_category_id='me', subcategory_id='me-sub', unit='NO.', active=True),
        ])
        for i, key in enumerate(['el', 'cv', 'me']):
            db.add(User(id=f'sup-{key}', username=f'sup-{key}', phone=f'+96650222222{i}', password_hash='x', is_active=True))
            db.add(Organization(id=f'sup-{key}-org', name=f'Supplier {key}', name_normalized=f'supplier {key}', city='Riyadh',
                                is_buyer=False, is_supplier=True, verified_status='verified'))
            db.flush()
            db.add(Member(user_id=f'sup-{key}', organization_id=f'sup-{key}-org', role='supplier'))
            # Each supplier carries its discipline's product (strict matching needs product evidence).
            db.add(Capability(org_id=f'sup-{key}-org', category_id=f'{key}-sub', city='Riyadh', confirmed=True,
                              accepts_rfqs=True, eligibility_status='enabled',
                              material_ids={'el': ['cable-1'], 'me': ['valve-1']}.get(key, []),
                              trade_keywords={'cv': 'Ready mix concrete C40'}.get(key, '')))
        db.commit()


def line(**overrides):
    value = {'material_id': None, 'main_category_id': 'cv', 'subcategory_id': 'cv-sub', 'description': 'Ready mix concrete C40',
             'specifications': '', 'unit': 'M', 'quantity': '12.5'}
    value.update(overrides)
    return value


def rfq(items, **overrides):
    value = {'buyer_org_id': 'org', 'client_request_id': str(uuid.uuid4()), 'title': 'Site materials', 'city': 'Riyadh',
             'project_name': '', 'required_delivery_date': None, 'additional_instructions': '',
             'response_deadline': (datetime.now(UTC) + timedelta(days=5)).isoformat().replace('+00:00', 'Z'),
             'required_qualifications': [], 'items': items}
    value.update(overrides)
    return value


def test_form_blank_values_create_rfq_with_201(env):
    e = env; seed_disciplines(e)
    # Exactly what the browser produced: "" from cleared selects, null specs, no brand.
    response = e.client.post('/api/v1/rfqs', json=rfq([line(material_id='', specifications=None)]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['rfq']['id'] and body['rfq']['status'] == 'draft'
    assert body['items'][0]['material_id'] is None and body['items'][0]['specifications'] == ''
    listed = e.client.get('/api/v1/rfqs?org_id=org')
    assert listed.status_code == 200 and body['rfq']['id'] in {r['id'] for r in listed.json()}


def test_single_and_multi_discipline_rfqs_persist_and_match(env):
    e = env; seed_disciplines(e)
    single = e.client.post('/api/v1/rfqs', json=rfq([line(material_id='cable-1', main_category_id='el', subcategory_id='el-sub',
                                                           description='XLPE Cable 4x16mm', specifications='Brand: Riyadh Cables')]))
    assert single.status_code == 201, single.text
    single_pub = e.client.post(f"/api/v1/rfqs/{single.json()['rfq']['id']}/publish")
    assert single_pub.status_code == 200, single_pub.text
    multi = e.client.post('/api/v1/rfqs', json=rfq([
        line(material_id='cable-1', main_category_id='el', subcategory_id='el-sub', description='XLPE Cable 4x16mm'),
        line(),  # manual civil description, no Material Master record
        line(material_id='valve-1', main_category_id='me', subcategory_id='me-sub', description='Gate Valve DN100', unit='NO.', quantity='4'),
    ]))
    assert multi.status_code == 201, multi.text
    multi_id = multi.json()['rfq']['id']
    with e.db() as db:
        assert db.scalar(select(func.count()).select_from(RFQItem).where(RFQItem.rfq_id == multi_id)) == 3
    published = e.client.post(f'/api/v1/rfqs/{multi_id}/publish')
    assert published.status_code == 200, published.text
    with e.db() as db:
        invited = set(db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == multi_id)))
        assert {'sup-el-org', 'sup-cv-org', 'sup-me-org'} <= invited, invited
        assert db.get(RFQ, multi_id).status in {'published', 'collecting_quotes'}
    # A published RFQ is never edited as a draft.
    assert e.client.put(f'/api/v1/rfqs/{multi_id}', json=rfq([line()])).status_code == 409


def test_duplicate_submission_returns_same_rfq(env):
    e = env; seed_disciplines(e)
    payload = rfq([line()])
    first = e.client.post('/api/v1/rfqs', json=payload)
    second = e.client.post('/api/v1/rfqs', json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()['rfq']['id'] == second.json()['rfq']['id']
    with e.db() as db:
        assert db.scalar(select(func.count()).select_from(RFQ).where(RFQ.client_request_id == payload['client_request_id'])) == 1


def test_invalid_line_still_rejected_with_field_location(env):
    e = env; seed_disciplines(e)
    missing_sub = e.client.post('/api/v1/rfqs', json=rfq([line(), line(subcategory_id='')]))
    assert missing_sub.status_code == 422
    assert missing_sub.json()['detail'][0]['loc'] == ['body', 'items', 1, 'subcategory_id']
    wrong_path = e.client.post('/api/v1/rfqs', json=rfq([line(material_id='cable-1')]))
    assert wrong_path.status_code == 422 and wrong_path.json()['detail'][0]['loc'][-1] == 'description'
    too_precise = e.client.post('/api/v1/rfqs', json=rfq([line(quantity='1.2345')]))
    assert too_precise.status_code == 422 and too_precise.json()['detail'][0]['loc'][-1] == 'quantity'
    with e.db() as db:
        assert db.scalar(select(func.count()).select_from(RFQ)) == 0
