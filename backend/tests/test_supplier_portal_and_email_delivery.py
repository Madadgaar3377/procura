"""Supplier invitation portal, enterprise notification queue and email bounce handling.

Workflow under test: RFQ publish -> supplier matching -> persisted eligible suppliers ->
notification queue -> secure invitation link -> restricted portal session -> quotation
submission -> buyer notification; plus SMTP 554 classification, DSN hard/soft bounces,
webhooks, duplicate prevention, provider limits and worker restart recovery.

Every provider call is replaced by an in-memory fake (``providers`` fixture): no email
or SMS can leave this process, whatever the local .env says.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from smtplib import SMTPDataError, SMTPRecipientsRefused

import pytest
from sqlalchemy import func, select

from app import notification_worker
from app.config import get_settings
from app.models import (Contact, ContactChannel, DeliveryRestriction, EmailDeliveryEvent, Invitation, Notification,
                        Organization, Outbox, Quotation, RFQ, RFQItem, RFQMatch, SupplierAccessToken,
                        SupplierPortalSession, User)
from app.modules.notifications import bounces
from app.modules.notifications.crypto import decrypt_delivery_secret
from app.modules.notifications.smtp_diagnostics import classify, describe, describe_recipients
from app.modules.procurement.portal import issue_link
from app.security import current_user
from test_rfq_publish_matching_fallback import setup_matching_app

SUPPLIER_EMAIL = 'sales@example.com'


@pytest.fixture
def providers(monkeypatch):
    settings = get_settings()
    for key, value in {'mock_notifications': False, 'smtp_username': 'orders@procumaterial.com',
                       'smtp_password': 'test-only', 'smtp_from_email': 'Procumaterial <orders@procumaterial.com>',
                       'smtp_reply_to': 'support@procumaterial.com', 'notification_redirect_email': '',
                       'notification_redirect_phone': '', 'sms_gateway_enabled': False, 'whatsapp_enabled': False,
                       'email_send_limit_per_minute': 0, 'email_send_limit_per_hour': 0,
                       'sms_send_limit_per_minute': 0, 'smtp_envelope_from': ''}.items():
        monkeypatch.setattr(settings, key, value)
    sent = {'email': [], 'sms': []}

    def send_email(address, subject, plain, html, key, *, attachments=None, headers=None, message_id=None):
        from app.modules.notifications.delivery import email_message_id
        sent['email'].append({'to': address, 'subject': subject, 'plain': plain, 'headers': headers or {},
                              'message_id': message_id or email_message_id(key)})
        return message_id or email_message_id(key)

    def send_sms(phone, text, key, **kwargs):
        sent['sms'].append({'to': phone, 'text': text})
        return {'id': f'sms-{key}', 'state': 'Pending', 'http_status': 202}

    monkeypatch.setattr(notification_worker, 'send_email', send_email)
    monkeypatch.setattr(notification_worker, 'send_sms', send_sms)
    monkeypatch.setattr(notification_worker, 'get_sms_status', lambda pid, **kw: {'id': pid, 'state': 'Delivered'})
    notification_worker._EMAIL_PAUSE.update({'until': None, 'reason': None, 'data_rejections': 0})
    return sent


@pytest.fixture
def env(providers):
    from app.modules.notifications.admin_deliveries import router as admin_router
    from app.modules.notifications.router import router as notifications_router
    from app.modules.procurement.portal_session import router as portal_router
    client, sessions, engine, ids = setup_matching_app('')
    for router in (portal_router, admin_router, notifications_router):
        client.app.include_router(router, prefix='/api/v1')
    with sessions() as db:
        org = db.get(Organization, ids['supplier'])
        org.business_email = SUPPLIER_EMAIL
        db.commit()
    yield client, sessions, ids, providers
    engine.dispose()


def drain(sessions, rounds=6):
    for _ in range(rounds):
        with sessions() as db:
            if not notification_worker.process_batch(db):
                return


def publish(client, sessions, ids):
    response = client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
    assert response.status_code == 200, response.text
    drain(sessions)


def email_jobs(db, template='rfq_invitation'):
    return db.scalars(select(Outbox).where(Outbox.event == 'notification.delivery',
                                           Outbox.payload['channel'].as_string() == 'email',
                                           Outbox.payload['template'].as_string() == template)).all()


def new_link(sessions, ids, destination=SUPPLIER_EMAIL):
    with sessions() as db:
        inv = db.scalar(select(Invitation).where(Invitation.rfq_id == ids['rfq']))
        token = issue_link(db, inv, db.get(RFQ, ids['rfq']), channel='email', destination=destination)
        db.commit()
        return token, inv.id


def open_portal(client, token):
    response = client.post('/api/v1/supplier-portal/session', json={'token': token})
    assert response.status_code == 200, response.text
    return response.json()


def as_admin(client, sessions, ids):
    with sessions() as db:
        user = db.get(User, ids['user'])
        user.is_admin = True
        db.commit()
    client.app.dependency_overrides[current_user] = lambda: user


def quote_body(item_id, price='12.50', quantity='10000'):
    return {'lines': [{'rfq_item_id': item_id, 'quantity': quantity, 'unit_price': price, 'lead_days': 5,
                       'compliance': 'compliant'}], 'vat_rate': '0.15'}


# --------------------------------------------------------------------- end to end
def test_publish_to_portal_submission_and_buyer_notification(env):
    client, sessions, ids, sent = env
    publish(client, sessions, ids)
    with sessions() as db:
        jobs = email_jobs(db)
        assert len(jobs) == 1 and jobs[0].status == 'processed' and jobs[0].delivery_status == 'accepted'
        # Accepted by provider is never reported as delivered.
        assert jobs[0].delivery_status != 'delivered' and jobs[0].submitted_at is not None
    assert len(sent['email']) == 1
    message = sent['email'][0]
    # The invitation link is the dedicated portal route with the token in the fragment.
    assert '/supplier-portal#token=' in message['plain']
    assert message['headers']['X-Procura-Delivery-Id'] == jobs[0].id
    token = message['plain'].split('/supplier-portal#token=', 1)[1].split()[0]

    opened = open_portal(client, token)
    portal = opened['portal']
    invitation_id = opened['invitation_id']
    assert token not in json.dumps(opened)
    assert portal['supplier']['name'] == 'Supplier Tech' and portal['rfq']['reference'] == 'RFQ-TEST-MATCH'
    assert portal['buyer']['name'] == 'Procumaterial buyer'  # Buyer identity not authorized for display.
    item = portal['items'][0]
    assert item['matched'] and item['main_category'] == 'Architectural Specialties'
    assert item['subcategory'] == 'Toilet Partitions, Mirrors & Accessories' and item['unit'] == 'EA'
    assert portal['invitation_status'] == 'invited' and portal['can_accept']
    headers = {'X-Portal-Invitation': invitation_id}
    assert client.get('/api/v1/supplier-portal/me', headers=headers).status_code == 200
    # Without the invitation header the restricted session is not usable.
    assert client.get('/api/v1/supplier-portal/me').status_code == 401

    accepted = client.post('/api/v1/supplier-portal/me/accept', headers=headers)
    assert accepted.status_code == 200 and accepted.json()['invitation_status'] == 'accepted'
    draft = client.post('/api/v1/supplier-portal/me/draft', headers=headers,
                        json={'quotation': quote_body(item['id'])})
    assert draft.status_code == 200, draft.text
    assert draft.json()['quotation']['status'] == 'draft'
    assert Decimal(draft.json()['quotation']['totals']['vat']) > 0

    body = {'quotation': quote_body(item['id']), 'confirm_submission': True}
    refused = client.post('/api/v1/supplier-portal/me/submit', headers=headers, json=body)
    assert refused.status_code == 403 and refused.json()['detail']['code'] == 'verification_required'

    requested = client.post('/api/v1/supplier-portal/me/verification/request', headers=headers)
    assert requested.status_code == 200, requested.text
    assert requested.json()['channel'] == 'email' and '@example.com' in requested.json()['sent_to']
    assert SUPPLIER_EMAIL not in requested.json()['sent_to']
    with sessions() as db:
        job = db.scalar(select(Outbox).where(Outbox.payload['template'].as_string() == 'portal_verification_code'))
        code = decrypt_delivery_secret(job.secret_ciphertext)
    drain(sessions)
    assert any(code in m['plain'] and m['to'] == SUPPLIER_EMAIL for m in sent['email'])
    with sessions() as db:
        assert db.get(Outbox, job.id).secret_ciphertext is None  # Code erased after sending.
    wrong = client.post('/api/v1/supplier-portal/me/verification/confirm', headers=headers,
                        json={'code': '000000' if code != '000000' else '111111'})
    assert wrong.status_code == 422
    assert client.post('/api/v1/supplier-portal/me/verification/confirm', headers=headers,
                       json={'code': code}).json() == {'verified': True}

    zero = client.post('/api/v1/supplier-portal/me/submit', headers=headers,
                       json={'quotation': quote_body(item['id'], price='0'), 'confirm_submission': True})
    assert zero.status_code == 422 and zero.json()['detail']['code'] == 'price_required'
    submitted = client.post('/api/v1/supplier-portal/me/submit', headers=headers, json=body)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()['quotation']['status'] == 'submitted' and submitted.json()['can_download_quotation']
    assert client.post('/api/v1/supplier-portal/me/draft', headers=headers,
                       json={'quotation': quote_body(item['id'])}).status_code == 409
    pdf = client.get('/api/v1/supplier-portal/me/quotation.pdf', headers=headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
    rfq_pdf = client.get('/api/v1/supplier-portal/me/rfq.pdf?lang=en', headers=headers)
    assert rfq_pdf.status_code == 200 and b'Buyer Company' not in rfq_pdf.content
    drain(sessions)
    with sessions() as db:
        quote = db.scalar(select(Quotation).where(Quotation.rfq_id == ids['rfq']))
        assert quote.status == 'submitted' and quote.submission_source == 'magic_link'
        submitted_job = db.scalar(select(Outbox).where(Outbox.event == 'quotation.submitted'))
        assert submitted_job.status == 'processed'
        # The buyer is notified in-app (buyer account) after successful submission.
        assert db.scalar(select(func.count()).select_from(Notification).where(
            Notification.event == 'quotation_submitted')) >= 1
        # No raw token or session secret is stored anywhere.
        row = db.scalar(select(SupplierAccessToken).where(SupplierAccessToken.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        assert row is not None and row.use_count >= 1


def test_invalid_expired_revoked_and_closed_links(env):
    client, sessions, ids, _ = env
    publish(client, sessions, ids)
    bad = client.post('/api/v1/supplier-portal/session', json={'token': 'x' * 43})
    assert bad.status_code == 401 and bad.json()['detail']['code'] == 'invalid_link'
    malformed = client.post('/api/v1/supplier-portal/session', json={'token': 'a b' * 10})
    assert malformed.status_code == 401 and malformed.json()['detail']['code'] == 'invalid_link'

    token, invitation_id = new_link(sessions, ids)
    with sessions() as db:
        row = db.scalar(select(SupplierAccessToken).order_by(SupplierAccessToken.created_at.desc()))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    expired = client.post('/api/v1/supplier-portal/session', json={'token': token})
    assert expired.status_code == 401 and expired.json()['detail']['code'] == 'link_expired'

    token, invitation_id = new_link(sessions, ids)
    open_portal(client, token)
    headers = {'X-Portal-Invitation': invitation_id}
    with sessions() as db:
        from app.modules.procurement.portal import revoke_links
        revoke_links(db, invitation_id)
        db.commit()
    # Revoking links ends sessions exchanged from them too.
    assert client.get('/api/v1/supplier-portal/me', headers=headers).json()['detail']['code'] == 'link_revoked'
    revoked = client.post('/api/v1/supplier-portal/session', json={'token': token})
    assert revoked.status_code == 401 and revoked.json()['detail']['code'] == 'link_revoked'

    token, invitation_id = new_link(sessions, ids)
    open_portal(client, token)
    with sessions() as db:
        db.get(RFQ, ids['rfq']).status = 'closed'
        db.commit()
    view = client.get('/api/v1/supplier-portal/me', headers=headers)
    assert view.status_code == 200 and view.json()['editable'] is False and view.json()['rfq_open'] is False
    closed = client.post('/api/v1/supplier-portal/me/draft', headers=headers,
                         json={'quotation': quote_body(view.json()['items'][0]['id'])})
    assert closed.status_code == 409 and closed.json()['detail']['code'] == 'rfq_closed'
    assert client.post('/api/v1/supplier-portal/me/verification/request', headers=headers).status_code == 409


def test_session_is_bound_to_one_invitation_and_cancelled_invitations_are_refused(env):
    client, sessions, ids, _ = env
    publish(client, sessions, ids)
    token, invitation_id = new_link(sessions, ids)
    open_portal(client, token)
    with sessions() as db:
        # A second invitation of another supplier for another RFQ.
        other_org = Organization(name='Other Supplier', name_normalized='other supplier', is_supplier=True,
                                 verified_status='verified')
        db.add(other_org)
        db.flush()
        other = Invitation(rfq_id=ids['rfq'], supplier_org_id=other_org.id, status='invited', matched_item_ids=[])
        db.add(other)
        db.commit()
        other_id = other.id
    # The cookie of invitation A never authorizes invitation B.
    denied = client.get('/api/v1/supplier-portal/me', headers={'X-Portal-Invitation': other_id})
    assert denied.status_code == 401
    with sessions() as db:
        db.get(Invitation, invitation_id).status = 'cancelled'
        db.commit()
    withdrawn = client.get('/api/v1/supplier-portal/me', headers={'X-Portal-Invitation': invitation_id})
    assert withdrawn.status_code == 403 and withdrawn.json()['detail']['code'] == 'invitation_withdrawn'
    assert client.post('/api/v1/supplier-portal/session', json={'token': token}).json()['detail']['code'] == 'invitation_withdrawn'


def test_decline_revokes_link_and_notifies_buyer(env):
    client, sessions, ids, _ = env
    publish(client, sessions, ids)
    token, invitation_id = new_link(sessions, ids)
    open_portal(client, token)
    headers = {'X-Portal-Invitation': invitation_id}
    assert client.post('/api/v1/supplier-portal/me/decline', headers=headers, json={'reason': 'No stock'}).json() == {'status': 'declined'}
    assert client.post('/api/v1/supplier-portal/session', json={'token': token}).status_code == 401
    with sessions() as db:
        assert db.get(Invitation, invitation_id).status == 'declined'
        assert db.scalar(select(Outbox).where(Outbox.event == 'rfq.invitation.responded')).payload['action'] == 'decline'
        assert db.scalar(select(func.count()).select_from(SupplierPortalSession).where(
            SupplierPortalSession.revoked_at.is_(None))) == 0


def test_imported_supplier_without_account_uses_the_portal(env):
    client, sessions, ids, sent = env
    with sessions() as db:
        from app.models import Member
        for member in db.scalars(select(Member).where(Member.organization_id == ids['supplier'])):
            db.delete(member)
        db.commit()
    publish(client, sessions, ids)
    assert len(sent['email']) == 1
    token = sent['email'][0]['plain'].split('#token=', 1)[1].split()[0]
    opened = open_portal(client, token)
    assert opened['portal']['editable'] is True
    with sessions() as db:
        # No user account was created from the invitation link.
        assert db.scalar(select(func.count()).select_from(User)) == 1


# ------------------------------------------------------------- matching is not re-run
def test_notification_processing_reads_persisted_matching(env, monkeypatch):
    client, sessions, ids, sent = env
    response = client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
    assert response.status_code == 200
    from app.modules.matching import service as matching
    calls = []
    original = matching._match_batch
    monkeypatch.setattr(matching, '_match_batch', lambda *a, **k: calls.append(k) or original(*a, **k))
    drain(sessions)
    assert calls == [] and len(sent['email']) == 1
    with sessions() as db:
        invitation = db.scalar(select(Invitation))
        assert invitation.match_evidence and set(invitation.match_evidence) == set(invitation.matched_item_ids)


def test_closed_rfq_and_unqualified_supplier_are_not_notified(env):
    client, sessions, ids, sent = env
    client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
    with sessions() as db:
        db.get(RFQ, ids['rfq']).status = 'cancelled'
        db.commit()
    drain(sessions)
    assert sent['email'] == []
    with sessions() as db:
        rfq = db.get(RFQ, ids['rfq'])
        rfq.status = 'collecting_quotes'
        job = db.scalar(select(Outbox).where(Outbox.event == 'rfq.invitation'))
        job.status = 'pending'
        db.scalar(select(RFQMatch)).status = 'insufficient_evidence'  # matching result superseded
        db.commit()
    drain(sessions)
    assert sent['email'] == []
    with sessions() as db:
        assert db.scalar(select(Outbox).where(Outbox.event == 'rfq.invitation')).delivery_status == 'eligibility_review_required'


# ------------------------------------------------------------- SMTP classification
@pytest.mark.parametrize('code,text,stage,expected', [
    (554, '5.7.1 Message rejected: sender is not owned by authenticated user', 'data', ('sender_rejected', 'permanent_rejection')),
    (554, '5.7.0 Daily sending limit exceeded', 'data', ('rate_limited', 'rate_limit')),
    (554, '5.7.1 Message rejected as spam by content filter', 'data', ('policy_rejected', 'spam_rejection')),
    (552, '5.2.2 Mailbox full', 'rcpt_to', ('mailbox_full', 'mailbox_full')),
    (550, '5.1.1 user unknown', 'rcpt_to', ('recipient_rejected', 'invalid_recipient')),
    (451, '4.3.0 Temporary local problem, try again later', 'data', ('rate_limited', 'rate_limit')),
    (421, '4.4.2 Connection dropped', 'data', ('temporary', 'temporary_rejection')),
    (550, 'Sender domain does not exist', 'data', ('sender_rejected', 'permanent_rejection')),
])
def test_smtp_rejections_are_differentiated(code, text, stage, expected):
    from app.modules.notifications.smtp_diagnostics import CATEGORY
    failure, retryable, provider_level = classify(code, text, stage)
    assert (failure, CATEGORY[failure]) == expected
    if failure in {'sender_rejected', 'policy_rejected', 'rate_limited'}:
        assert provider_level  # Account-level: pauses the lane, never suppresses a supplier.


def test_mailbox_full_at_rcpt_is_soft_and_never_suppresses():
    detail = describe_recipients(SMTPRecipientsRefused({'a@example.com': (552, b'5.2.2 mailbox full')}))
    assert detail['retryable'] and not detail['permanent_recipient'] and detail['category'] == 'mailbox_full'
    detail = describe(SMTPDataError(554, b'5.7.1 Service unavailable; client host blocked using Spamhaus'), 'data')
    assert detail['category'] == 'spam_rejection' and detail['provider_level'] and not detail['retryable']


def test_smtp_554_provider_rejection_does_not_suppress_supplier(env, monkeypatch):
    client, sessions, ids, _ = env
    from app.modules.notifications import delivery

    def reject(*args, **kwargs):
        raise delivery.smtp_rejection(describe(SMTPDataError(554, b'5.7.1 Message rejected as spam'), 'data'))
    monkeypatch.setattr(notification_worker, 'send_email', reject)
    publish(client, sessions, ids)
    with sessions() as db:
        job = email_jobs(db)[0]
        assert job.status == 'failed' and job.delivery_status == 'spam_rejection'
        assert job.payload['provider_response']['enhanced_code'] == '5.7.1'
        assert db.scalar(select(func.count()).select_from(DeliveryRestriction)) == 0


def test_smtp_invalid_recipient_marks_address_and_flags_contact(env, monkeypatch):
    client, sessions, ids, _ = env
    from app.modules.notifications import delivery
    with sessions() as db:
        contact = Contact(org_id=ids['supplier'], name='Sales', email=SUPPLIER_EMAIL)
        db.add(contact)
        db.commit()

    def refuse(*args, **kwargs):
        detail = describe_recipients(SMTPRecipientsRefused({SUPPLIER_EMAIL: (550, b'5.1.1 user unknown')}))
        error = delivery.smtp_rejection(detail)
        error.permanent_recipient = detail['permanent_recipient']
        raise error
    monkeypatch.setattr(notification_worker, 'send_email', refuse)
    publish(client, sessions, ids)
    with sessions() as db:
        restriction = db.scalar(select(DeliveryRestriction))
        assert restriction.reason == 'invalid_recipient' and restriction.destination == SUPPLIER_EMAIL
        assert db.scalar(select(Contact)).needs_review is True
        assert db.get(Organization, ids['supplier']).is_active  # Supplier preserved.
        assert email_jobs(db)[0].delivery_status == 'invalid_recipient'


# ------------------------------------------------------------------ bounces
def dsn(message_id, delivery_id, recipient, status, diagnostic, action='failed'):
    return (f"""From: Mail Delivery Subsystem <MAILER-DAEMON@mail.example.net>
To: orders@procumaterial.com
Subject: Undelivered Mail Returned to Sender
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: text/plain

This is the mail system. Your message could not be delivered.

--B
Content-Type: message/delivery-status

Reporting-MTA: dns; mail.example.net

Final-Recipient: rfc822; {recipient}
Action: {action}
Status: {status}
Diagnostic-Code: smtp; {diagnostic}

--B
Content-Type: text/rfc822-headers

Message-ID: {message_id}
X-Procura-Delivery-Id: {delivery_id}
Subject: Procumaterial | New RFQ Invitation

--B--
""").encode()


def accepted_job(client, sessions, ids):
    publish(client, sessions, ids)
    with sessions() as db:
        job = email_jobs(db)[0]
        assert job.delivery_status == 'accepted'
        return job.id, job.provider_message_id


def test_hard_bounce_marks_invalid_suppresses_future_sends_and_admin_corrects(env):
    client, sessions, ids, sent = env
    job_id, message_id = accepted_job(client, sessions, ids)
    with sessions() as db:
        db.add(Contact(org_id=ids['supplier'], name='Sales', email=SUPPLIER_EMAIL))
        db.commit()
    report = dsn(message_id, job_id, SUPPLIER_EMAIL, '5.1.1', '550 5.1.1 <sales@example.com>: Recipient address rejected: User unknown')
    with sessions() as db:
        assert bounces.process_report(db, report) == ['address_marked_invalid']
        db.commit()
        # Processing the same report again is idempotent.
        assert bounces.process_report(db, report) == ['duplicate']
        job = db.get(Outbox, job_id)
        assert job.status == 'failed' and job.delivery_status == 'hard_bounced'
        assert db.scalar(select(DeliveryRestriction)).reason == 'invalid_recipient'
        assert db.scalar(select(Contact)).needs_review is True
        assert db.get(Organization, ids['supplier']) is not None
        stored = db.scalar(select(EmailDeliveryEvent))
        assert stored.destination_hash and SUPPLIER_EMAIL not in (stored.diagnostic or '')

    as_admin(client, sessions, ids)
    summary = client.get('/api/v1/notifications/admin/deliveries/summary').json()
    assert summary['hard_bounced'] == 1 and summary['delivered'] == 0
    rows = client.get('/api/v1/notifications/admin/deliveries?status=hard_bounced').json()['rows']
    assert rows[0]['id'] == job_id and rows[0]['restriction']['reason'] == 'invalid_recipient'
    assert rows[0]['retry_eligible'] is False and rows[0]['correctable'] is True
    assert SUPPLIER_EMAIL not in json.dumps(rows)  # masked in the list
    detail = client.get(f'/api/v1/notifications/admin/deliveries/{job_id}').json()
    assert any(h['kind'] == 'hard_bounce' for h in detail['history'])
    assert client.post(f'/api/v1/notifications/admin/outbox/{job_id}/retry', json={}).status_code == 409

    corrected = client.post(f'/api/v1/notifications/admin/deliveries/{job_id}/correct-contact',
                            json={'email': 'procurement@example.com', 'resend': True})
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()['resent_delivery_id']
    drain(sessions)
    assert sent['email'][-1]['to'] == 'procurement@example.com'
    with sessions() as db:
        assert db.scalar(select(Contact)).email == 'procurement@example.com'
        assert db.get(Organization, ids['supplier']).business_email == 'procurement@example.com'


def test_provider_level_bounce_is_recorded_without_suppression(env):
    client, sessions, ids, _ = env
    job_id, message_id = accepted_job(client, sessions, ids)
    report = dsn(message_id, job_id, SUPPLIER_EMAIL, '5.7.1', '554 5.7.1 Message rejected due to sender reputation')
    with sessions() as db:
        assert bounces.process_report(db, report) == ['hard_bounce_recorded']
        db.commit()
        assert db.scalar(select(func.count()).select_from(DeliveryRestriction)) == 0


def test_soft_bounce_retries_with_backoff_then_stops(env, monkeypatch):
    client, sessions, ids, sent = env
    monkeypatch.setattr(get_settings(), 'email_soft_bounce_max_retries', 2)
    job_id, message_id = accepted_job(client, sessions, ids)
    for attempt in range(3):
        with sessions() as db:
            job = db.get(Outbox, job_id)
            report = dsn(job.provider_message_id, job_id, SUPPLIER_EMAIL, '4.2.2', '452 4.2.2 Mailbox full')
            result = bounces.process_report(db, report)
            db.commit()
            job = db.get(Outbox, job_id)
            if attempt < 2:
                assert result == ['soft_bounce_retry_scheduled'] and job.status == 'retry'
                delay = (job.next_attempt_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
                assert delay > get_settings().email_soft_bounce_base_delay_seconds * 2 ** attempt - 60
                job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
                db.commit()
            else:
                assert result == ['soft_bounce_retries_exhausted'] and job.status == 'failed'
        drain(sessions)
    ids_sent = [m['message_id'] for m in sent['email']]
    assert len(ids_sent) == 3 and len(set(ids_sent)) == 3  # resends carry new Message-IDs
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(DeliveryRestriction)) == 0
        assert db.get(Outbox, job_id).delivery_status == 'soft_bounced'


def test_report_for_another_recipient_cannot_change_a_job(env):
    client, sessions, ids, _ = env
    job_id, message_id = accepted_job(client, sessions, ids)
    with sessions() as db:
        report = dsn(message_id, job_id, 'someone-else@example.org', '5.1.1', '550 5.1.1 user unknown')
        assert bounces.process_report(db, report) == ['recipient_mismatch']
        db.commit()
        assert db.get(Outbox, job_id).delivery_status == 'accepted'
        assert db.scalar(select(func.count()).select_from(DeliveryRestriction)) == 0


def test_signed_webhook_confirms_delivery(env, monkeypatch):
    client, sessions, ids, _ = env
    from pydantic import SecretStr
    monkeypatch.setattr(get_settings(), 'email_webhook_secret', SecretStr('whsec-test'))
    job_id, message_id = accepted_job(client, sessions, ids)
    body = json.dumps({'events': [{'id': 'evt-1', 'type': 'delivered', 'message_id': message_id,
                                   'recipient': SUPPLIER_EMAIL}]}).encode()
    stamp = str(int(time.time()))
    signature = 'sha256=' + hmac.new(b'whsec-test', stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    unsigned = client.post('/api/v1/notifications/webhooks/email', content=body,
                           headers={'X-Procura-Timestamp': stamp, 'X-Procura-Signature': 'sha256=bad'})
    assert unsigned.status_code == 401
    ok = client.post('/api/v1/notifications/webhooks/email', content=body,
                     headers={'X-Procura-Timestamp': stamp, 'X-Procura-Signature': signature,
                              'Content-Type': 'application/json'})
    assert ok.status_code == 200 and ok.json()['results'] == ['confirmed_delivery']
    replay = client.post('/api/v1/notifications/webhooks/email', content=body,
                         headers={'X-Procura-Timestamp': stamp, 'X-Procura-Signature': signature})
    assert replay.json()['results'] == ['duplicate']
    with sessions() as db:
        assert db.get(Outbox, job_id).delivery_status == 'delivered'


# -------------------------------------------------------------- queue behaviour
def test_duplicate_recipient_is_prevented_per_rfq_and_notification_type(env):
    client, sessions, ids, sent = env
    publish(client, sessions, ids)
    assert len(sent['email']) == 1
    with sessions() as db:
        job = email_jobs(db)[0]
        # A second job for the same destination and notice (e.g. a reinstated invitation).
        clone = Outbox(event='notification.delivery', dedupe_key='clone', payload={**job.payload})
        db.add(clone)
        db.commit()
        clone_id = clone.id
    drain(sessions)
    assert len(sent['email']) == 1
    with sessions() as db:
        clone = db.get(Outbox, clone_id)
        assert clone.status == 'skipped' and clone.delivery_status == 'duplicate_suppressed'


def test_provider_limit_defers_without_consuming_attempts(env, monkeypatch):
    client, sessions, ids, sent = env
    monkeypatch.setattr(get_settings(), 'email_send_limit_per_minute', 1)
    with sessions() as db:
        db.add(Outbox(event='notification.delivery', submitted_at=datetime.now(UTC), status='processed',
                      payload={'channel': 'email', 'template': 'other'}))
        db.commit()
    publish(client, sessions, ids)
    assert sent['email'] == []
    with sessions() as db:
        job = email_jobs(db)[0]
        assert job.status == 'pending' and job.attempts == 0
        db.execute(Outbox.__table__.update().where(Outbox.payload['template'].as_string() == 'other')
                   .values(submitted_at=datetime.now(UTC) - timedelta(minutes=5)))
        db.commit()
    drain(sessions)
    assert len(sent['email']) == 1


def test_security_codes_are_not_held_behind_the_bulk_email_budget(env, monkeypatch):
    client, sessions, ids, sent = env
    publish(client, sessions, ids)
    token, invitation_id = new_link(sessions, ids)
    open_portal(client, token)
    monkeypatch.setattr(get_settings(), 'email_send_limit_per_minute', 1)  # budget already used
    requested = client.post('/api/v1/supplier-portal/me/verification/request',
                            headers={'X-Portal-Invitation': invitation_id})
    assert requested.status_code == 200
    drain(sessions)
    assert sent['email'][-1]['subject'].startswith('Procumaterial | Quotation verification code')


def test_worker_restart_recovers_leases_and_never_resends_uncertain_email(env):
    client, sessions, ids, sent = env
    client.post(f"/api/v1/rfqs/{ids['rfq']}/publish")
    with sessions() as db:
        invitation_job = db.scalar(select(Outbox).where(Outbox.event == 'rfq.invitation'))
        # Crashed worker: the job is still leased as processing with an expired lock.
        invitation_job.status = 'processing'
        invitation_job.lock_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    drain(sessions)
    assert len(sent['email']) == 1
    with sessions() as db:
        job = email_jobs(db)[0]
        # Crash after handing the message to SMTP but before recording acceptance.
        job.status, job.delivery_status, job.lock_until = 'processing', 'submitting', datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        job_id = job.id
    drain(sessions)
    assert len(sent['email']) == 1  # Not resent: the outcome must be reconciled first.
    with sessions() as db:
        assert db.get(Outbox, job_id).status == 'uncertain'


def test_fair_order_isolates_organizations():
    rows = [(f'a{i}', {'context': {'buyer_org_id': 'big'}}) for i in range(10)] + \
           [('b1', {'context': {'buyer_org_id': 'small'}}), ('c1', {'rfq_id': 'r2'})]
    picked = notification_worker.fair_order(rows, 5, 2)
    assert picked[:4] == ['a0', 'a1', 'b1', 'c1'] and len(picked) == 5


# ---------------------------------------------------------------- buyer statuses
def test_buyer_sees_channel_status_separately_from_recorded_invitation(env):
    client, sessions, ids, _ = env
    publish(client, sessions, ids)
    results = client.get(f"/api/v1/matching/rfqs/{ids['rfq']}/results").json()
    row = results['invitations'][0]
    assert row['invitation_recorded'] is True and row['email_status'] == 'accepted'
    assert row['supplier_response'] == 'invited' and row['quotation_status'] is None
    assert results['matches'][0]['email_status'] == 'accepted'
