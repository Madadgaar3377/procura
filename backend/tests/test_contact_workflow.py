from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.schemas import ContactInquiryCreate, ContactInquiryNote
from app.worker import _deliver_contact_inquiry


def valid_payload(**overrides):
    return {
        'name': '  Buyer Contact  ',
        'email': ' Buyer@Example.SA ',
        'phone': '+966 50 123 4567',
        'company': '  Example LLC  ',
        'topic': 'buyer',
        'user_type': 'buyer',
        'category': 'buyer_support',
        'subject': 'Help with an RFQ',
        'message': 'Please help us prepare an RFQ for our upcoming project.',
        'consent': True,
        **overrides,
    }


def test_contact_payload_normalizes_and_checks_fields():
    payload = ContactInquiryCreate(**valid_payload())
    assert payload.name == 'Buyer Contact'
    assert payload.email == 'buyer@example.sa'
    assert payload.company == 'Example LLC'

    for change in (
        {'email': 'not-an-email'},
        {'phone': 'call me'},
        {'subject': 'bad\nsubject'},
        {'message': 'x' * 5001},
        {'consent': False},
    ):
        with pytest.raises(ValidationError):
            ContactInquiryCreate(**valid_payload(**change))


def test_internal_notes_reject_blank_and_oversized_values():
    assert ContactInquiryNote(note='  Reviewed identity details  ').note == 'Reviewed identity details'
    with pytest.raises(ValidationError):
        ContactInquiryNote(note='  ')
    with pytest.raises(ValidationError):
        ContactInquiryNote(note='x' * 5001)


def test_anonymous_submission_with_same_key_returns_the_existing_ticket_and_queues_once():
    from starlette.requests import Request
    from app.models import ContactInquiry
    from app.modules.contact.router import submit

    class FakeSession:
        def __init__(self): self.rows=[]; self.inquiry=None
        def scalar(self, statement): return self.inquiry
        def scalars(self, statement): return SimpleNamespace(all=lambda: [])
        def add(self, item):
            self.rows.append(item)
            if isinstance(item, ContactInquiry):
                item.id=str(uuid4()); item.created_at=datetime.now(timezone.utc); self.inquiry=item
        def flush(self): pass
        def commit(self): pass

    scope={'type':'http','method':'POST','path':'/api/v1/contact','headers':[], 'client':('127.0.0.1',12345),'server':('test',80),'scheme':'http','query_string':b''}
    request=Request(scope)
    payload=ContactInquiryCreate(**valid_payload(submission_key=uuid4()))
    db=FakeSession()
    first=submit(payload,request,None,db)
    second=submit(payload,request,None,db)
    assert first['reference']==second['reference']
    assert sum(type(item).__name__=='ContactInquiry' for item in db.rows)==1
    assert sum(type(item).__name__=='Outbox' for item in db.rows)==2


def test_contact_admin_endpoints_require_backend_admin_dependency():
    from app.modules.contact.router import router
    from app.security import admin_user

    admin_routes=[route for route in router.routes if '/admin/' in route.path]
    assert admin_routes
    for route in admin_routes:
        assert any(dependency.call is admin_user for dependency in route.dependant.dependencies), route.path


def test_contact_workflow_migration_backfills_existing_rows_and_adds_admin_fields():
    migration_path=Path(__file__).parents[1]/'alembic'/'versions'/'x74a19c02b6de_contact_workflow_fields.py'
    spec=importlib.util.spec_from_file_location('contact_workflow_migration',migration_path)
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE contact_inquiries (id VARCHAR(36) PRIMARY KEY, name VARCHAR(150) NOT NULL, email VARCHAR(200) NOT NULL, company VARCHAR(220), phone VARCHAR(30), topic VARCHAR(40) NOT NULL, message TEXT NOT NULL, status VARCHAR(30) NOT NULL, handled_by VARCHAR(36), handled_at DATETIME, created_at DATETIME NOT NULL)'))
        connection.execute(text("INSERT INTO contact_inquiries (id,name,email,topic,message,status,created_at) VALUES ('old-row-id','Legacy','legacy@example.sa','other','Old request','new','2026-01-01')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        row=connection.execute(text('SELECT ticket_reference, user_type, category, subject, internal_notes FROM contact_inquiries WHERE id=:id'),{'id':'old-row-id'}).one()
        assert row.ticket_reference.startswith('PM-LEGACY-')
        assert row.user_type=='other'
        assert row.category=='general_inquiry'
        assert row.subject=='General inquiry'
        assert row.internal_notes is None
        assert 'ticket_reference' in {column['name'] for column in inspect(connection).get_columns('contact_inquiries')}
    engine.dispose()


@pytest.mark.parametrize('kind,recipient', [
    ('contact.inquiry.support', 'support@procumaterial.com'),
    ('contact.inquiry.ack', 'buyer@example.sa'),
])
def test_contact_outbox_delivery_uses_existing_email_provider(monkeypatch, kind, recipient):
    import app.worker as worker

    inquiry = SimpleNamespace(
        id='inquiry-id', ticket_reference='PM-20261109-ABC12345', name='Buyer <Contact>',
        email='buyer@example.sa', company='Example LLC', user_type='buyer', category='buyer_support',
        subject='Help with an RFQ', message='Please help.', created_at=datetime.now(timezone.utc),
    )
    event = SimpleNamespace(
        event=kind, payload={'inquiry_id': inquiry.id}, status='processing', attempts=0,
        provider_message_id=None, delivery_status=None, next_attempt_at=datetime.now(timezone.utc), id='outbox-id',
    )
    captured = {}

    def send_email(address, subject, plain, html, key):
        captured.update(address=address, subject=subject, plain=plain, html=html, key=key)
        return 'provider-id'

    monkeypatch.setattr(worker, 'send_email', send_email)
    _deliver_contact_inquiry(SimpleNamespace(get=lambda model, key: inquiry), event)

    assert captured['address'] == recipient
    assert captured['key'] == event.id
    assert inquiry.ticket_reference in captured['plain']
    assert '&lt;Contact&gt;' in captured['html']
    assert event.status == 'processed'
    assert event.delivery_status == 'accepted'
    assert event.next_attempt_at is None
