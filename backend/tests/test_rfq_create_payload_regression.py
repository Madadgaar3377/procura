"""Regression: buyer RFQ create returned 422 for payloads the RFQ form really sends.

Blank select values ("" for material/subcategory) and null specifications are
normalized; genuinely invalid lines still fail with a line-located 422.
"""
from datetime import UTC, datetime, timedelta
import uuid

from sqlalchemy import func, select

from app.models import Capability, Category, Contact, Invitation, Material, Organization, Member, Outbox, RFQ, RFQItem, User
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


def test_edit_draft_preserves_material_ids_and_saved_fields(env):
    e = env; seed_disciplines(e)
    created = e.client.post('/api/v1/rfqs', json=rfq([
        line(description='Ready mix concrete C40', specifications='Pump delivery\nBrand approved', quantity='12.500'),
        line(material_id='cable-1', main_category_id='el', subcategory_id='el-sub',
             description='XLPE Cable 4x16mm', unit='M', quantity='8'),
    ]))
    assert created.status_code == 201, created.text
    rfq_id = created.json()['rfq']['id']
    original = created.json()['items']
    updated = e.client.put(f'/api/v1/rfqs/{rfq_id}', json=rfq([
        line(id=original[0]['id'], main_category_id='me', subcategory_id='me-sub',
             description='Custom gate valve assembly', specifications='Pressure 16 bar\nBrand: approved',
             unit='NO.', quantity='2.375'),
        line(description='Ready mix concrete C40', specifications='New line', quantity='1.250'),
    ]))
    assert updated.status_code == 200, updated.text
    reopened = e.client.get(f'/api/v1/rfqs/{rfq_id}')
    assert reopened.status_code == 200, reopened.text
    rows = reopened.json()['items']
    assert len(rows) == 2
    kept = next(row for row in rows if row['id'] == original[0]['id'])
    assert (kept['main_category_id'], kept['subcategory_id'], kept['unit'], kept['quantity']) == ('me', 'me-sub', 'NO.', '2.375')
    assert kept['description'] == 'Custom gate valve assembly'
    assert kept['specifications'] == 'Pressure 16 bar\nBrand: approved'
    assert original[1]['id'] not in {row['id'] for row in rows}
    assert any(row['specifications'] == 'New line' for row in rows)


def test_edit_draft_rejects_foreign_material_id(env):
    e = env; seed_disciplines(e)
    first = e.client.post('/api/v1/rfqs', json=rfq([line()])).json()
    second = e.client.post('/api/v1/rfqs', json=rfq([line()])).json()
    rfq_id = first['rfq']['id']
    response = e.client.put(f'/api/v1/rfqs/{rfq_id}', json=rfq([
        line(id=second['items'][0]['id'], description='Unauthorized row change'),
    ]))
    assert response.status_code == 422
    reopened = e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    assert reopened['items'][0]['id'] == first['items'][0]['id']


def test_published_revision_preserves_rfq_and_material_ids(env):
    e = env; seed_disciplines(e)
    created = e.client.post('/api/v1/rfqs', json=rfq([line()]))
    assert created.status_code == 201, created.text
    rfq_id = created.json()['rfq']['id']
    item = created.json()['items'][0]
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/publish').status_code == 200
    before = e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    body = rfq([line(id=item['id'], quantity='17.250', specifications='Pump delivery')],
               title='Site materials revised', client_request_id=None)
    staged = e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions', json={
        'expected_revision': before['rfq']['revision'], 'rfq': body})
    assert staged.status_code == 201, staged.text
    unchanged = e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    assert unchanged['rfq']['title'] == before['rfq']['title']
    revision_id = staged.json()['id']
    published = e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions/{revision_id}/publish')
    assert published.status_code == 200, published.text
    after = e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    assert after['rfq']['id'] == rfq_id and after['rfq']['reference'] == before['rfq']['reference']
    assert after['rfq']['revision'] == before['rfq']['revision'] + 1
    assert after['items'][0]['id'] == item['id']
    assert after['items'][0]['quantity'] == '17.250'
    assert after['items'][0]['specifications'] == 'Pump delivery'
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions/{revision_id}/publish').status_code == 200
    history = e.client.get(f'/api/v1/rfqs/{rfq_id}/revisions')
    assert history.status_code == 200 and history.json()[0]['number'] == after['rfq']['revision']
    stale = e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions', json={
        'expected_revision': before['rfq']['revision'], 'rfq': body})
    assert stale.status_code == 409


def test_published_revision_adds_and_removes_without_duplicate_invitation(env):
    e=env;seed_disciplines(e)
    created=e.client.post('/api/v1/rfqs',json=rfq([line(),line(material_id='cable-1',main_category_id='el',subcategory_id='el-sub',description='XLPE Cable 4x16mm')])).json()
    rfq_id=created['rfq']['id'];old_ids=[item['id'] for item in created['items']]
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/publish').status_code==200
    with e.db() as db:
        before_invites=db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq_id))
    payload=rfq([line(id=old_ids[0],quantity='20.000'),line(material_id='valve-1',main_category_id='me',subcategory_id='me-sub',description='Gate Valve DN100',unit='NO.')],client_request_id=None)
    staged=e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions',json={'expected_revision':0,'rfq':payload})
    assert staged.status_code==201,staged.text
    revision_id=staged.json()['id']
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions/{revision_id}/publish').status_code==200
    reopened=e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    assert len(reopened['items'])==2 and old_ids[0] in {item['id'] for item in reopened['items']}
    with e.db() as db:
        removed=db.get(RFQItem,old_ids[1]);assert removed and not removed.is_active
        assert db.scalar(select(func.count()).select_from(RFQ).where(RFQ.id==rfq_id))==1
        jobs=db.scalars(select(Outbox).where(Outbox.dedupe_key==f'rfq-revision-match:{rfq_id}:1')).all()
        assert len(jobs)==1 and jobs[0].payload['matching_item_ids']
        job_id=jobs[0].id
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq_id))>=before_invites
    from app.modules.matching.service import run_matching_job
    run_matching_job(e.engine,job_id,time_budget=None)
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions/{revision_id}/publish').status_code==200
    with e.db() as db:
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.dedupe_key==f'rfq-revision-match:{rfq_id}:1'))==1
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq_id))<=3


def test_published_revision_stages_attachment_until_confirmation(env,monkeypatch):
    e=env;seed_disciplines(e)
    monkeypatch.setattr('app.storage.upload_file',lambda bucket,key,stream,content_type,**kwargs:{
        'size_bytes':len(stream.read()),'etag':'test-etag','checksum_sha256':'test-checksum'})
    created=e.client.post('/api/v1/rfqs',json=rfq([line()])).json()
    rfq_id=created['rfq']['id'];item_id=created['items'][0]['id']
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/publish').status_code==200
    staged=e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions',json={
        'expected_revision':0,'attachment_changes':True,
        'rfq':rfq([line(id=item_id)],client_request_id=None)})
    assert staged.status_code==201,staged.text
    revision_id=staged.json()['id']
    uploaded=e.client.post(f'/api/v1/rfqs/{rfq_id}/attachments?revision_id={revision_id}',
        files={'file':('specification.pdf',b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF','application/pdf')})
    assert uploaded.status_code==201,uploaded.text
    attachment_id=uploaded.json()['id']
    before=e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    assert attachment_id not in {entry['id'] for entry in before['attachments']}
    published=e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions/{revision_id}/publish')
    assert published.status_code==200,published.text
    after=e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    assert attachment_id in {entry['id'] for entry in after['attachments']}


def test_revision_authorization_and_closed_status(env):
    e=env;seed_disciplines(e)
    created=e.client.post('/api/v1/rfqs',json=rfq([line()])).json()
    rfq_id=created['rfq']['id'];item_id=created['items'][0]['id']
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/publish').status_code==200
    payload={'expected_revision':0,'rfq':rfq([line(id=item_id,quantity='13')],client_request_id=None)}
    e.actor[0]='other'
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions',json=payload).status_code in {403,404}
    assert e.client.get(f'/api/v1/rfqs/{rfq_id}/revisions').status_code in {403,404}
    e.actor[0]='buyer'
    staged=e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions',json=payload)
    assert staged.status_code==201,staged.text
    e.actor[0]='other'
    assert e.client.post(f"/api/v1/rfqs/{rfq_id}/revisions/{staged.json()['id']}/publish").status_code in {403,404}
    e.actor[0]='buyer'
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/close').status_code==200
    assert e.client.post(f"/api/v1/rfqs/{rfq_id}/revisions/{staged.json()['id']}/publish").status_code==409


def test_pending_revision_can_be_discarded_without_changing_live_rfq(env):
    e=env;seed_disciplines(e)
    created=e.client.post('/api/v1/rfqs',json=rfq([line()])).json()
    rfq_id=created['rfq']['id'];item_id=created['items'][0]['id']
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/publish').status_code==200
    body={'expected_revision':0,'rfq':rfq([line(id=item_id,quantity='99')],client_request_id=None)}
    staged=e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions',json=body)
    assert staged.status_code==201,staged.text
    revision_id=staged.json()['id']
    assert e.client.delete(f'/api/v1/rfqs/{rfq_id}/revisions/{revision_id}').status_code==200
    current=e.client.get(f'/api/v1/rfqs/{rfq_id}').json()
    assert current['rfq']['revision']==0 and current['items'][0]['quantity']=='12.500'
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions',json=body).status_code==201


def test_revision_migration_upgrades_and_downgrades_isolated_schema(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text
    from pathlib import Path
    engine=create_engine(f"sqlite:///{tmp_path/'migration.sqlite'}")
    with engine.begin() as connection:
        for table in ('users','rfqs','rfq_items','quotations','quotation_lines','rfq_matches','rfq_invitations'):
            extra=(', rfq_id VARCHAR(36), supplier_org_id VARCHAR(36)'
                   if table in ('rfq_matches','rfq_invitations') else
                   ', rfq_id VARCHAR(36)' if table=='rfq_items' else '')
            connection.execute(text(f'CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY{extra})'))
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('zo58e5f6a7b8')"))
        cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        cfg.attributes['connection']=connection
        command.upgrade(cfg,'zp59f6a7b8c9')
        assert 'revision' in {column['name'] for column in inspect(connection).get_columns('rfqs')}
        assert 'rfq_revisions' in inspect(connection).get_table_names()
        command.upgrade(cfg,'zq60a7b8c9d0')
        assert 'rfq_revision' in {column['name'] for column in inspect(connection).get_columns('rfq_matches')}
        assert 'rfq_revision' in {column['name'] for column in inspect(connection).get_columns('rfq_invitations')}
        command.downgrade(cfg,'zp59f6a7b8c9')
        assert 'rfq_revision' not in {column['name'] for column in inspect(connection).get_columns('rfq_matches')}
        command.downgrade(cfg,'zo58e5f6a7b8')
        assert 'revision' not in {column['name'] for column in inspect(connection).get_columns('rfqs')}
    engine.dispose()


def test_revision_notice_uses_company_contact_without_private_employee_contact(env):
    e=env;seed_disciplines(e)
    created=e.client.post('/api/v1/rfqs',json=rfq([line()])).json()
    rfq_id=created['rfq']['id'];item_id=created['items'][0]['id']
    with e.db() as db:
        org=db.get(Organization,'sup-cv-org');org.business_email='sales@example.com'
        db.add(Contact(org_id=org.id,name='Private employee',email='private@example.com',is_primary=False))
        db.commit()
    assert e.client.post(f'/api/v1/rfqs/{rfq_id}/publish').status_code==200
    staged=e.client.post(f'/api/v1/rfqs/{rfq_id}/revisions',json={
        'expected_revision':0,'rfq':rfq([line(id=item_id,quantity='20')],client_request_id=None)})
    assert staged.status_code==201,staged.text
    assert e.client.post(f"/api/v1/rfqs/{rfq_id}/revisions/{staged.json()['id']}/publish").status_code==200
    from app.modules.notifications.procurement import revision_notices,revision_notice
    with e.db() as db:
        event=db.scalar(select(Outbox).where(Outbox.dedupe_key==f'rfq-revised:{rfq_id}:1'))
        revision_notices(db,event,datetime.now(UTC));db.flush()
        notices=db.scalars(select(Outbox).where(Outbox.event=='rfq.revision.notice')).all()
        assert len(notices)==1
        revision_notice(db,notices[0]);db.flush()
        deliveries=db.scalars(select(Outbox).where(Outbox.event=='notification.delivery',
            Outbox.payload['template'].as_string()=='rfq_revision')).all()
        destinations={delivery.payload.get('destination') for delivery in deliveries}
        assert 'sales@example.com' in destinations and 'private@example.com' not in destinations
        revision_notice(db,notices[0]);db.flush()
        assert len(db.scalars(select(Outbox).where(Outbox.event=='notification.delivery',
            Outbox.payload['template'].as_string()=='rfq_revision')).all())==len(deliveries)


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


def test_discipline_and_brand_are_preserved_without_changing_material_master(env):
    e=env;seed_disciplines(e)
    created=e.client.post('/api/v1/rfqs',json=rfq([line(discipline='Civil',required_brand='Approved Brand')]))
    assert created.status_code==201,created.text
    item=created.json()['items'][0]
    assert item['discipline']=='Civil' and item['required_brand']=='Approved Brand'
    mismatch=e.client.post('/api/v1/rfqs',json=rfq([line(discipline='Electrical')]))
    assert mismatch.status_code==422 and mismatch.json()['detail'][0]['loc'][-1]=='discipline'
