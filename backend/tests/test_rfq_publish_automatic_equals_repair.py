"""Automatic matching after publication must equal the manual repair run.

RFQ-C9B00690ACA7 recorded 5 matches after publication and 635 after the manual
rematch script. Publication, the outbox worker and the script now share one runner
(run_matching_job). These tests create and publish the RFQ through the buyer API,
let only the worker continue, and compare the stored result with an unbounded
evaluation of the same data and rules. The repair script is never used to pass.
"""
from datetime import UTC, datetime, timedelta
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import notification_worker
from app.config import get_settings
from app.database import get_db
from app.models import Invitation, MeasurementUnit, Organization, Outbox, RFQ, RFQMatch, User
from app.modules.matching import service as matching
from app.modules.matching.router import router as matching_router
from app.modules.rfqs.router import router as rfq_router
from app.notification_worker import process_batch
from app.security import current_user
from tests.test_matching_all_eligible_suppliers import bulk_suppliers, env  # noqa: F401  (shared fixture)

SENT: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def mock_providers(monkeypatch):
    """Never reach a real SMTP/SMS provider, whatever the local .env configures."""
    SENT.clear()
    settings = get_settings().model_copy(update={'mock_notifications': True, 'notification_redirect_email': '',
                                                 'notification_redirect_phone': ''})
    monkeypatch.setattr(notification_worker, 'get_settings', lambda: settings)
    monkeypatch.setattr(notification_worker, 'send_email',
                        lambda address, *args, **kwargs: SENT.append(('email', address)) or '<mock-id>')
    monkeypatch.setattr(notification_worker, 'send_sms', lambda phone, *args, **kwargs: SENT.append(('sms', phone)) or {
        'id': 'mock-sms', 'state': 'Pending', 'http_status': 202})
    monkeypatch.setattr(notification_worker, 'get_sms_status', lambda key: {'id': key, 'state': 'Delivered', 'lookup': True})


LINES = [('Plug Mi', 'mech', 'fittings', 'plug-mi'), ('Pipe', 'mech', 'pipes', None),
         ('3way Female Plug', 'elec', 'consumable', None), ('Female Plug', 'elec', 'consumable', None)]


def buyer_app(sessions):
    app = FastAPI()
    app.include_router(rfq_router, prefix='/api/v1')
    app.include_router(matching_router, prefix='/api/v1')

    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with sessions() as db:
        db.add(MeasurementUnit(code='PCS', name_en='Pieces', active=True))
        db.commit()
        user = db.get(User, 'buyer-user')
    app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


def create_and_publish(client):
    """Exactly what the buyer RFQ form sends, followed by Publish."""
    payload = {'buyer_org_id': 'buyer', 'client_request_id': str(uuid.uuid4()), 'title': 'Granada Park materials',
               'city': '', 'project_name': 'Granada Park', 'required_delivery_date': None, 'additional_instructions': '',
               'response_deadline': (datetime.now(UTC) + timedelta(days=5)).isoformat().replace('+00:00', 'Z'),
               'required_qualifications': [],
               'items': [{'material_id': material or '', 'main_category_id': main, 'subcategory_id': sub,
                          'description': 'Plug MI 1/2 inch' if material else description, 'specifications': None,
                          'unit': 'PCS', 'quantity': '100'} for description, main, sub, material in LINES]}
    created = client.post('/api/v1/rfqs', json=payload)
    assert created.status_code == 201, created.text
    rfq_id = created.json()['rfq']['id']
    published = client.post(f'/api/v1/rfqs/{rfq_id}/publish')
    assert published.status_code == 200, published.text
    return rfq_id, published.json()


def run_worker(sessions, polls=60):
    """The production worker loop: outbox polls only, no manual matching."""
    for _ in range(polls):
        with sessions() as db:
            process_batch(db)
        with sessions() as db:
            open_jobs = db.scalar(select(func.count()).select_from(Outbox).where(
                Outbox.status.in_({'pending', 'retry', 'processing'}),
                Outbox.event.in_({'rfq.published', 'rfq.invitation'})))
        if not open_jobs:
            return
    raise AssertionError('worker did not finish the queued matching and invitation jobs')


def stored(db, rfq_id):
    matched = set(db.scalars(select(RFQMatch.supplier_org_id).where(
        RFQMatch.rfq_id == rfq_id, RFQMatch.status.in_({'eligible', 'partial'}))))
    invited = set(db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq_id)))
    return matched, invited


def test_publish_then_worker_matches_exactly_what_a_full_evaluation_finds(env, monkeypatch):
    sessions, _, engine = env
    bulk_suppliers(sessions, 57)                      # several pages of subcategory suppliers
    bulk_suppliers(sessions, 6, sub='valves', products='Gate valves')  # same discipline, other product: never matched
    client = buyer_app(sessions)
    # Publication evaluates a single small page; the worker must finish the rest.
    monkeypatch.setattr(get_settings(), 'matching_batch_size', 10)
    monkeypatch.setattr(get_settings(), 'matching_publish_time_budget_seconds', 0)
    monkeypatch.setattr(get_settings(), 'matching_worker_time_budget_seconds', 1)
    rfq_id, published = create_and_publish(client)
    assert published['matching_status'] == 'processing'
    assert published['matched_suppliers'] <= 10

    run_worker(sessions)

    with sessions() as db:
        job = db.scalar(select(Outbox).where(Outbox.dedupe_key == f'rfq-published:{rfq_id}'))
        assert job.status == 'processed' and 'cursor' not in job.payload and job.payload.get('completed_at')
        matched, invited = stored(db, rfq_id)
        full = matching._match_batch(db, db.get(RFQ, rfq_id), evaluate_only=True)   # unbounded, nothing written
        assert matched == set(full['eligible_ids'])
        assert invited == set(full['auto_invite_ids'])
        assert len(matched) == 3 + 57 and not any(s.startswith('bulk-valves') for s in matched)
        assert job.payload['evaluated_suppliers'] >= len(matched)
        # Strongest evidence stays distinguishable: product evidence is never reported as broad.
        strongest = {oid: levels[0] for oid, levels in full['match_levels'].items()}
        assert strongest['exact-fittings'] == matching.LEVEL_EXACT   # the line selects plug-mi
        assert strongest['text-plugs'] in {matching.LEVEL_ALIAS, matching.LEVEL_FAMILY}
        # Category-only candidates are not matched, counted or invited.
        assert 'imp-fittings' not in matched and 'main-elec' not in matched and 'main-elec' not in invited
        invitation_events = db.scalars(select(Outbox).where(Outbox.event == 'rfq.invitation')).all()
        assert len(invitation_events) == len(invited)
        assert {e.status for e in invitation_events} == {'processed'}

    results = client.get(f'/api/v1/matching/rfqs/{rfq_id}/results?page_size=25').json()
    assert results['matching_status'] == 'completed' and results['matched_suppliers'] == len(matched)
    assert results['exact_material_matches'] >= 1 and results['main_category_matches'] == 0
    assert {row['status'] for row in results['matches']} <= {'eligible', 'partial'}

    # The repair runner on the same data changes nothing and duplicates nothing.
    with sessions() as db:
        repair = Outbox(event='rfq.published', payload={'rfq_id': rfq_id}, dedupe_key=f'rfq-repair:{rfq_id}')
        db.add(repair)
        db.commit()
        repair_id = repair.id
    again = matching.run_matching_job(engine, repair_id, time_budget=None, restart=True)
    assert again['new_invitations'] == 0 and again['eligible_suppliers'] == len(matched)
    run_worker(sessions)
    with sessions() as db:
        assert stored(db, rfq_id) == (matched, invited)
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id == rfq_id)) == len(invited)
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event == 'rfq.invitation')) == len(invited)


def test_invitation_job_evaluates_once_and_records_the_exact_skip_reason(env, monkeypatch):
    sessions, _, _ = env
    client = buyer_app(sessions)
    rfq_id, published = create_and_publish(client)
    assert published['matching_status'] == 'completed'
    with sessions() as db:
        # The supplier is suspended after its invitation was queued.
        db.get(Organization, 'text-plugs').verified_status = 'suspended'
        db.commit()
    calls = []
    original = matching._match_batch

    def counting(*args, **kwargs):
        if kwargs.get('evaluate_only'):
            calls.append(kwargs.get('supplier_ids'))
        return original(*args, **kwargs)
    monkeypatch.setattr(matching, '_match_batch', counting)
    run_worker(sessions)
    with sessions() as db:
        events = db.scalars(select(Outbox).where(Outbox.event == 'rfq.invitation')).all()
        skipped = [e for e in events if e.status == 'skipped']
        assert [e.payload['supplier_org_id'] for e in skipped] == ['text-plugs']
        assert skipped[0].delivery_status == 'eligibility_review_required'
        assert skipped[0].payload['eligibility_reason'].startswith('suspended: Supplier organization is suspended')
        # Quotation draft + notification share one evaluation per invitation job.
        assert len(calls) == len(events)


def test_worker_poll_stops_claiming_after_its_time_budget(env, monkeypatch):
    sessions, _, _ = env
    client = buyer_app(sessions)
    rfq_id, _ = create_and_publish(client)
    with sessions() as db:
        queued = db.scalar(select(func.count()).select_from(Outbox).where(
            Outbox.event == 'rfq.invitation', Outbox.status == 'pending'))
    assert queued > 2
    monkeypatch.setattr(notification_worker.get_settings(), 'outbox_poll_time_budget_seconds', 0)
    with sessions() as db:
        assert len(process_batch(db)) == 1          # the rest stays queued, nothing is lost
    run_worker(sessions)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Outbox).where(
            Outbox.event == 'rfq.invitation', Outbox.status != 'processed')) == 0
