"""Manual reminders (POST /rfqs/{id}/reminders) and buyer-facing results.

Production returned 422 because the buyer page offered reminders from the persisted
matching result while the endpoint re-ran live matching and rejected any supplier the
two disagreed on. Both now use one persisted query. Mock providers only.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.models import Invitation, Organization, Outbox, Quotation, RFQMatch
from app.modules.matching import service as matching
from app.notification_worker import process_batch
from tests.test_matching_all_eligible_suppliers import env  # noqa: F401  (shared fixture)
from tests.test_rfq_publish_automatic_equals_repair import (  # noqa: F401  (autouse mock providers)
    SENT, buyer_app, create_and_publish, mock_providers, run_worker)


def published(sessions, contacts=('text-plugs', 'exact-fittings')):
    """Granada RFQ via the buyer API; listed suppliers have a company email, imp-pipes has none."""
    with sessions() as db:
        for key in contacts:
            db.get(Organization, key).business_email = f'Sales@{key}.example'
        db.commit()
    client = buyer_app(sessions)
    rfq_id, outcome = create_and_publish(client)
    assert outcome['matching_status'] == 'completed'
    run_worker(sessions)
    with sessions() as db:
        invitations = {inv.supplier_org_id: inv.id for inv in db.scalars(select(Invitation).where(Invitation.rfq_id == rfq_id))}
    assert set(invitations) == {'exact-fittings', 'imp-pipes', 'text-plugs'}
    return client, rfq_id, invitations


def remind(client, rfq_id, body=None):
    return client.post(f'/api/v1/rfqs/{rfq_id}/reminders', json=body if body is not None else {})


def test_every_supplier_offered_a_reminder_is_accepted(env):
    sessions, _, _ = env
    client, rfq_id, invitations = published(sessions)
    page = client.get(f'/api/v1/matching/rfqs/{rfq_id}/results').json()
    offered = [row['id'] for row in page['invitations'] if row['reminder_eligible']]
    assert sorted(offered) == sorted(invitations.values()) == sorted(page['pending_invitation_ids'])
    response = remind(client, rfq_id, {'invitation_ids': offered})
    # Previously 422; now every offered supplier is processed and each outcome reported.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['requested'] == 3 and body['queued'] == 2 and body['failed'] == 0
    assert body['skipped'] == {'not_pending': 0, 'cooldown': 0, 'no_valid_contact': 1}
    assert '2 reminder(s) queued.' in body['message'] and 'no valid email or mobile contact' in body['message']


def test_individual_selected_and_all_pending_reminders_with_cooldown(env):
    sessions, _, _ = env
    client, rfq_id, invitations = published(sessions)
    single = remind(client, rfq_id, {'invitation_ids': [invitations['text-plugs']]}).json()
    assert single['queued'] == 1 and single['queued_invitation_ids'] == [invitations['text-plugs']]
    # Same supplier again: cooldown, reported as skipped (not 409/422), nothing queued.
    again = remind(client, rfq_id, {'invitation_ids': [invitations['text-plugs']]}).json()
    assert again['queued'] == 0 and again['skipped']['cooldown'] == 1 and again['next_available_at']
    # By supplier organization id (selected reminders).
    by_org = remind(client, rfq_id, {'supplier_org_ids': ['exact-fittings']}).json()
    assert by_org['queued'] == 1
    # All pending: one cooled down twice over, one without a usable contact.
    everyone = remind(client, rfq_id).json()
    assert everyone['requested'] == 3 and everyone['queued'] == 0
    assert everyone['skipped'] == {'not_pending': 0, 'cooldown': 2, 'no_valid_contact': 1}
    rows = {row['supplier_org_id']: row for row in client.get(f'/api/v1/matching/rfqs/{rfq_id}/results').json()['invitations']}
    assert rows['text-plugs']['reminder_eligible'] is False and rows['text-plugs']['next_reminder_at']
    assert rows['text-plugs']['reminder_count'] == 1
    # Durable outbox requests only for queued suppliers; the worker creates per-channel deliveries.
    with sessions() as db:
        requests = db.scalars(select(Outbox).where(Outbox.event == 'rfq.deadline.reminder')).all()
        assert sorted(len(r.payload['invitation_ids']) for r in requests) == [1, 1]
    run_worker_until_quiet(sessions)
    with sessions() as db:
        deliveries = db.scalars(select(Outbox).where(Outbox.event == 'notification.delivery',
                                                      Outbox.payload['template'].as_string() == 'deadline_reminder')).all()
        destinations = {d.payload.get('destination') for d in deliveries if d.payload.get('channel') == 'email'}
        # Normalized, one per supplier, never the supplier without a contact.
        assert destinations == {'sales@text-plugs.example', 'sales@exact-fittings.example'}
        email = [d for d in deliveries if d.payload.get('channel') == 'email']
        assert {d.status for d in email} == {'processed'}, [(d.status, d.last_error) for d in email]
        # Without a mobile number the SMS row is recorded as skipped with its reason.
        assert {d.delivery_status for d in deliveries if d.payload.get('channel') == 'sms'} <= {'no_contact', 'invalid_contact'}
    # Mock providers only: each supplier address got its invitation and one reminder, no duplicates.
    assert sorted(address for channel, address in SENT if channel == 'email') == [
        'sales@exact-fittings.example', 'sales@exact-fittings.example', 'sales@text-plugs.example', 'sales@text-plugs.example']
    # After the cooldown the supplier can be reminded again (configurable window).
    with sessions() as db:
        db.get(Invitation, invitations['text-plugs']).last_reminded_at = datetime.now(UTC) - timedelta(
            hours=get_settings().rfq_reminder_cooldown_hours + 1)
        db.commit()
    assert remind(client, rfq_id, {'invitation_ids': [invitations['text-plugs']]}).json()['queued'] == 1


def _session(sessions):
    with sessions() as db:
        yield db


def run_worker_until_quiet(sessions, polls=20):
    for _ in range(polls):
        with sessions() as db:
            process_batch(db)
        with sessions() as db:
            if not db.scalar(select(func.count()).select_from(Outbox).where(Outbox.status.in_({'pending', 'retry', 'processing'}))):
                return


def test_responded_declined_and_cancelled_suppliers_are_not_reminded(env):
    sessions, _, _ = env
    client, rfq_id, invitations = published(sessions)
    with sessions() as db:
        db.add(Quotation(rfq_id=rfq_id, invitation_id=invitations['exact-fittings'], supplier_org_id='exact-fittings',
                         status='submitted'))
        db.get(Invitation, invitations['text-plugs']).status = 'declined'
        db.commit()
    body = remind(client, rfq_id, {'invitation_ids': [invitations['exact-fittings'], invitations['text-plugs']]}).json()
    assert body['queued'] == 0 and body['skipped']['not_pending'] == 2
    with sessions() as db:
        db.get(Invitation, invitations['imp-pipes']).status = 'cancelled'
        db.commit()
    rows = client.get(f'/api/v1/matching/rfqs/{rfq_id}/results').json()
    assert rows['pending_suppliers'] == 0 and not any(row['reminder_eligible'] for row in rows['invitations'])
    assert 'imp-pipes' not in {row['supplier_org_id'] for row in rows['invitations']}


def test_invalid_reminder_payloads_are_rejected_with_field_errors(env):
    sessions, _, _ = env
    client, rfq_id, invitations = published(sessions)
    empty = remind(client, rfq_id, {'invitation_ids': []})
    assert empty.status_code == 422
    both = remind(client, rfq_id, {'invitation_ids': [invitations['text-plugs']], 'supplier_org_ids': ['text-plugs']})
    assert both.status_code == 422
    unknown = remind(client, rfq_id, {'invitation_ids': ['not-an-invitation']})
    assert unknown.status_code == 422 and unknown.json()['detail'][0]['loc'] == ['body', 'invitation_ids']
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event == 'rfq.deadline.reminder')) == 0
    # A closed RFQ cannot be reminded.
    client.post(f'/api/v1/rfqs/{rfq_id}/close')
    assert remind(client, rfq_id).status_code == 409


def test_buyer_refreshes_read_saved_results_and_never_rematch(env, monkeypatch, tmp_path):
    sessions, _, engine = env
    client, rfq_id, _ = published(sessions)
    calls = []
    original = matching._match_batch
    monkeypatch.setattr(matching, '_match_batch', lambda *a, **k: calls.append(1) or original(*a, **k))
    first = client.get(f'/api/v1/matching/rfqs/{rfq_id}/results').json()

    # 100 concurrent buyer requests (20 threads) need a database with real per-thread
    # connections: copy the in-memory database to a file and serve it from a pool.
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import get_db
    target = tmp_path / 'load.db'
    raw = engine.raw_connection()
    with sqlite3.connect(target) as copy:
        raw.driver_connection.backup(copy)
    raw.close()
    file_engine = create_engine(f'sqlite:///{target}', connect_args={'check_same_thread': False, 'timeout': 30})
    file_sessions = sessionmaker(bind=file_engine, expire_on_commit=False)

    def database():
        with file_sessions() as db:
            yield db
    client.app.dependency_overrides[get_db] = database

    def read(_):
        response = client.get(f'/api/v1/matching/rfqs/{rfq_id}/results')
        assert response.status_code == 200, response.text
        return response.json()['matched_suppliers']
    with ThreadPoolExecutor(max_workers=20) as pool:
        counts = list(pool.map(read, range(100)))
    assert counts == [first['matched_suppliers']] * 100 == [3] * 100
    file_engine.dispose()
    client.app.dependency_overrides[get_db] = lambda: (yield from _session(sessions))
    for path in ('quotations-summary', 'notifications-status'):
        assert client.get(f'/api/v1/matching/rfqs/{rfq_id}/{path}').status_code == 200
    # The completed RFQ's dispatch call returns the stored result; nothing is re-evaluated.
    dispatched = client.post(f'/api/v1/matching/rfqs/{rfq_id}/dispatch').json()
    assert dispatched['already_completed'] is True and dispatched['eligible_suppliers'] == 3
    assert calls == []
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id == rfq_id,
                                                                        RFQMatch.status.in_(('eligible', 'partial')))) == 3


def test_duplicate_publish_creates_one_matching_job(env):
    sessions, _, _ = env
    client, rfq_id, _ = published(sessions)
    # Publishing again is idempotent: same status, no second job, no new invitations.
    again = client.post(f'/api/v1/rfqs/{rfq_id}/publish')
    assert again.status_code == 200 and again.json()['status'] == 'collecting_quotes'
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Outbox).where(
            Outbox.event == 'rfq.published', Outbox.payload['rfq_id'].as_string() == rfq_id)) == 1
        assert db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id == rfq_id)) == 3
