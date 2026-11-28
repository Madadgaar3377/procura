"""Email delivery feedback: bounce reports (RFC 3464 DSN), signed provider webhooks.

SMTP acceptance only means the provider took the message. What happened next arrives
later as a delivery status notification ("Mail Delivery Subsystem" / "Undelivered Mail
Returned to Sender") in the envelope-sender mailbox, or as a provider webhook. This
module turns either into one normalized feedback record and applies it to the original
outbox delivery job:

- ``delivered``   confirmed delivery (only from a DSN/webhook, never from SMTP 250).
- ``delayed``     the provider is still retrying; informational.
- ``soft_bounce`` temporary failure after acceptance (mailbox full, 4.x.x): resent with
                  exponential backoff up to ``email_soft_bounce_max_retries``.
- ``hard_bounce`` permanent failure. Only a confirmed invalid recipient (5.1.x/5.2.1 or
                  equivalent wording) marks the address invalid and suppresses further
                  automatic sending; sender-account/policy/quota rejections never
                  suppress a supplier address.
- ``complaint``   the recipient reported the message; the address is opted out.

Correlation: the ``X-Procura-Delivery-Id`` header (outbox id) and the deterministic
``Message-ID`` stored as ``outbox_events.provider_message_id``; the report's recipient
must match the job's destination. Every report is stored once per (source, external_id)
so re-processing a mailbox or a webhook retry is idempotent. Raw addresses are never
logged; reports store only a destination hash and the sanitized diagnostic.
"""
from __future__ import annotations

import email
import hashlib
import hmac
import logging
import re
from datetime import UTC, datetime, timedelta
from email import policy

from sqlalchemy import func, select

from app.config import get_settings
from app.models import ContactChannel, Contact, DeliveryRestriction, EmailDeliveryEvent, Outbox
from app.modules.notifications.smtp_diagnostics import CATEGORY, ENHANCED_RE, classify, sanitize_smtp_text

log = logging.getLogger('uvicorn.error')

DELIVERY_HEADER = 'X-Procura-Delivery-Id'
FEEDBACK_TYPES = {'delivered', 'delayed', 'soft_bounce', 'hard_bounce', 'complaint'}
_ADDRESS_RE = re.compile(r'[\w.+%-]+@[\w-]+(?:\.[\w-]+)+')
_MSGID_RE = re.compile(r'^\s*message-id\s*:\s*(<[^>\s]+>)', re.I | re.M)
_DELIVERY_ID_RE = re.compile(r'^\s*x-procura-delivery-id\s*:\s*([0-9a-f-]{36})', re.I | re.M)


def destination_hash(address: str | None) -> str | None:
    return hashlib.sha256(address.strip().casefold().encode()).hexdigest() if address else None


def _text(part) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001 - malformed parts are ignored, never fatal
        payload = None
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ''
    return payload.decode(part.get_content_charset() or 'utf-8', 'replace')


def _strip_type(value: str | None) -> str:
    """'rfc822; user@example.com' -> 'user@example.com'."""
    value = str(value or '').strip()
    return value.split(';', 1)[1].strip() if ';' in value else value


def parse_dsn(raw: bytes) -> dict | None:
    """Parse a bounce report. Returns None when it is not a delivery report.

    Supports RFC 3464 multipart/report (Postfix, Exim, Exchange, most providers) and
    falls back to scanning plain-text bounces for the original correlation headers.
    """
    message = email.message_from_bytes(raw, policy=policy.compat32)
    recipients: list[dict] = []
    original_headers = ''
    is_report = message.get_content_type() == 'multipart/report'
    for part in message.walk():
        ctype = part.get_content_type()
        if ctype == 'message/delivery-status':
            blocks = part.get_payload()
            blocks = blocks if isinstance(blocks, list) else []
            if not blocks:
                # Some MTAs send the status as an unparsed text block.
                text = _text(part)
                blocks = [email.message_from_string(chunk) for chunk in re.split(r'\n\s*\n', text) if chunk.strip()]
            for block in blocks:
                if block.get('Final-Recipient') or block.get('Original-Recipient') or block.get('Action'):
                    recipients.append({
                        'recipient': _strip_type(block.get('Final-Recipient') or block.get('Original-Recipient')),
                        'action': str(block.get('Action') or '').strip().lower(),
                        'status': str(block.get('Status') or '').strip(),
                        'diagnostic': _strip_type(block.get('Diagnostic-Code')),
                    })
        elif ctype == 'text/rfc822-headers':
            original_headers += _text(part)
        elif ctype == 'message/rfc822':
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            if hasattr(inner, 'items'):
                original_headers += ''.join(f'{k}: {v}\n' for k, v in inner.items())
    body = '' if recipients else '\n'.join(_text(p) for p in message.walk() if p.get_content_maintype() == 'text')
    haystack = original_headers or body
    delivery_id = next(iter(_DELIVERY_ID_RE.findall(haystack)), None)
    message_id = next(iter(_MSGID_RE.findall(haystack)), None)
    if not recipients and body:
        # Plain-text bounce: "<user@example.com>: host ... said: 550 5.1.1 ..."
        status = ENHANCED_RE.search(body)
        address = _ADDRESS_RE.search(body.split('\n\n', 1)[-1])
        if status and (delivery_id or message_id):
            recipients.append({'recipient': address.group(0) if address else '', 'action': 'failed',
                               'status': status.group(0), 'diagnostic': body[:500]})
    if not recipients:
        return None
    if not (is_report or delivery_id or message_id):
        return None
    return {'recipients': recipients, 'delivery_id': delivery_id, 'message_id': message_id}


def classify_report(entry: dict) -> tuple[str, str, bool]:
    """(feedback type, category, suppress_address) for one DSN recipient block."""
    action = entry.get('action') or ''
    status = entry.get('status') or ''
    diagnostic = sanitize_smtp_text(entry.get('diagnostic') or '')
    if action in {'delivered', 'relayed', 'expanded'}:
        return 'delivered', 'confirmed_delivery', False
    if action == 'delayed':
        return 'delayed', 'temporary_rejection', False
    code_match = re.search(r'\b([245]\d\d)\b', diagnostic)
    code = int(code_match.group(1)) if code_match else None
    text = ' '.join(filter(None, (status, diagnostic)))
    failure, retryable, provider_level = classify(code, text, 'rcpt_to' if not diagnostic else 'data')
    if failure == 'mailbox_full' or (retryable and not provider_level) or status.startswith('4.'):
        return 'soft_bounce', CATEGORY.get(failure, 'temporary_rejection'), False
    # Only a confirmed invalid recipient suppresses the address. A rejection about the
    # sending account, its reputation or quota is never the supplier address's fault.
    suppress = failure == 'recipient_rejected' and not provider_level
    return 'hard_bounce', CATEGORY.get(failure, 'permanent_rejection'), suppress


def _find_job(db, delivery_id: str | None, message_id: str | None):
    job = None
    if delivery_id:
        job = db.get(Outbox, delivery_id)
    if job is None and message_id:
        job = db.scalar(select(Outbox).where(Outbox.provider_message_id == message_id).limit(1))
    if job is not None and (job.event != 'notification.delivery' or job.payload.get('channel') != 'email'):
        return None
    return job


def _job_destination(job) -> str | None:
    payload = job.payload or {}
    if payload.get('redirected_to_test_recipient'):
        return get_settings().notification_redirect_email or payload.get('destination')
    # Account emails resolve their address at send time; see _account_destination.
    return payload.get('destination')


def _account_destination(db, job) -> str | None:
    from app.models import User
    user = db.get(User, job.payload.get('user_id')) if job.payload.get('user_id') else None
    return user.email if user else None


def suppress_address(db, destination: str, *, reason: str, detail: str, now: datetime) -> DeliveryRestriction:
    """Mark an email address invalid/opted-out for automatic sending. Supplier records kept."""
    from app.modules.notifications.recipients import normalize_destination
    try:
        destination = normalize_destination('email', destination)
    except ValueError:
        destination = destination.strip().casefold()
    row = db.scalar(select(DeliveryRestriction).where(DeliveryRestriction.channel == 'email',
                                                      DeliveryRestriction.destination == destination).with_for_update())
    if row is None:
        row = DeliveryRestriction(channel='email', destination=destination, reason=reason, detail=detail[:500],
                                  bounce_count=0)
        db.add(row)
    elif row.cleared_at is not None or row.reason != 'opted_out':
        row.reason = reason
        row.detail = detail[:500]
    row.cleared_at = None
    row.cleared_by_user_id = None
    row.bounce_count = (row.bounce_count or 0) + 1
    row.last_event_at = now
    # Flag the contact for admin correction; the contact and supplier are never deleted.
    for channel in db.scalars(select(ContactChannel).where(ContactChannel.kind == 'email',
                                                           ContactChannel.normalized_value == destination)):
        channel.needs_review = True
        channel.is_verified = False
    for contact in db.scalars(select(Contact).where(func.lower(Contact.email) == destination)):
        contact.needs_review = True
    db.flush()
    return row


def apply_feedback(db, *, source: str, external_id: str, feedback_type: str, category: str | None = None,
                   suppress: bool = False, delivery_id: str | None = None, message_id: str | None = None,
                   recipient: str | None = None, status_code: str | None = None, diagnostic: str | None = None,
                   now: datetime | None = None) -> str:
    """Apply one normalized feedback record. Returns what was done (stored on the record)."""
    from app.modules.notifications.service import update_delivery_notification
    now = now or datetime.now(UTC)
    external_id = (external_id or '')[:128]
    if feedback_type not in FEEDBACK_TYPES or not external_id:
        return 'ignored_invalid'
    if db.scalar(select(EmailDeliveryEvent.id).where(EmailDeliveryEvent.source == source,
                                                     EmailDeliveryEvent.external_id == external_id)):
        return 'duplicate'
    diagnostic = sanitize_smtp_text(diagnostic or '')
    record = EmailDeliveryEvent(source=source, external_id=external_id, provider_message_id=message_id,
                                event_type=feedback_type, destination_hash=destination_hash(recipient),
                                status_code=(status_code or '')[:20] or None, diagnostic=diagnostic or None)
    db.add(record)
    job = _find_job(db, delivery_id, message_id)
    if job is not None:
        job = db.scalar(select(Outbox).where(Outbox.id == job.id).with_for_update())
        record.outbox_id = job.id
        record.provider_message_id = record.provider_message_id or job.provider_message_id
    expected = (_job_destination(job) or _account_destination(db, job)) if job is not None else None
    if job is not None and recipient and expected and destination_hash(recipient) != destination_hash(expected):
        # A report naming another address cannot change this job (forged or misrouted).
        record.applied = 'recipient_mismatch'
        db.flush()
        return record.applied
    address = recipient or expected
    label = f"{status_code or ''} {diagnostic}".strip()[:300]
    applied = 'recorded'
    if feedback_type == 'complaint' and address:
        suppress_address(db, address, reason='opted_out', detail=f'Recipient complaint ({source})', now=now)
        applied = 'opted_out'
    if job is not None:
        payload = {**job.payload, 'feedback_category': category, 'feedback_at': now.isoformat()}
        if feedback_type == 'delivered':
            # A later success report never overrides a recorded permanent bounce.
            if not (job.status == 'failed' and job.delivery_status == 'hard_bounced'):
                job.status, job.delivery_status, job.next_attempt_at = 'processed', 'delivered', None
                job.last_error = None
                update_delivery_notification(db, job.id, 'delivered')
                applied = 'confirmed_delivery'
        elif feedback_type == 'delayed':
            payload['provider_delay'] = label
            applied = 'delay_recorded'
        elif feedback_type == 'soft_bounce':
            settings = get_settings()
            retries = int(payload.get('soft_bounce_retries') or 0)
            payload['soft_bounce_reason'] = label
            if retries < settings.email_soft_bounce_max_retries:
                delay = settings.email_soft_bounce_base_delay_seconds * 2 ** retries
                payload['soft_bounce_retries'] = retries + 1
                job.status, job.delivery_status = 'retry', 'soft_bounced'
                job.next_attempt_at = now + timedelta(seconds=delay)
                job.last_error = f'Soft bounce ({category}); resend {retries + 1}/{settings.email_soft_bounce_max_retries} scheduled: {label}'[:500]
                update_delivery_notification(db, job.id, 'queued')
                applied = 'soft_bounce_retry_scheduled'
            else:
                job.status, job.delivery_status, job.next_attempt_at = 'failed', 'soft_bounced', None
                job.last_error = f'Soft bounce ({category}); retries exhausted: {label}'[:500]
                update_delivery_notification(db, job.id, 'failed')
                applied = 'soft_bounce_retries_exhausted'
        elif feedback_type == 'hard_bounce':
            job.status, job.delivery_status, job.next_attempt_at = 'failed', 'hard_bounced', None
            job.last_error = f'Hard bounce ({category}): {label}'[:500]
            update_delivery_notification(db, job.id, 'failed')
            applied = 'hard_bounce_recorded'
        job.payload = payload
    if feedback_type == 'hard_bounce' and suppress and address:
        suppress_address(db, address, reason='invalid_recipient',
                         detail=f'Hard bounce {status_code or ""}: {diagnostic}'.strip(), now=now)
        applied = 'address_marked_invalid'
    record.applied = applied if job is not None or applied != 'recorded' else 'unmatched'
    db.flush()
    log.info('Email feedback source=%s type=%s category=%s job=%s applied=%s',
             source, feedback_type, category, job.id if job is not None else None, record.applied)
    return record.applied


def process_report(db, raw: bytes, *, source: str = 'imap', now: datetime | None = None) -> list[str]:
    """Parse and apply one bounce report. Idempotent per recipient block."""
    parsed = parse_dsn(raw)
    if not parsed:
        return []
    digest = hashlib.sha256(raw).hexdigest()
    results = []
    for index, entry in enumerate(parsed['recipients']):
        feedback, category, suppress = classify_report(entry)
        results.append(apply_feedback(
            db, source=source, external_id=f'{digest[:56]}:{index}', feedback_type=feedback, category=category,
            suppress=suppress, delivery_id=parsed['delivery_id'], message_id=parsed['message_id'],
            recipient=entry.get('recipient') or None, status_code=entry.get('status') or None,
            diagnostic=entry.get('diagnostic'), now=now))
    return results


# ------------------------------------------------------------- signed webhooks
WEBHOOK_TYPES = {
    'delivered': 'delivered', 'delivery': 'delivered',
    'deferred': 'delayed', 'delayed': 'delayed',
    'soft_bounce': 'soft_bounce', 'softbounce': 'soft_bounce',
    'hard_bounce': 'hard_bounce', 'hardbounce': 'hard_bounce', 'bounce': 'hard_bounce', 'bounced': 'hard_bounce',
    'complaint': 'complaint', 'spam_complaint': 'complaint', 'spamreport': 'complaint',
}


def verify_webhook(body: bytes, timestamp: str | None, signature: str | None, *, now: datetime | None = None) -> bool:
    secret = get_settings().email_webhook_secret.get_secret_value()
    if not secret or not timestamp or not signature:
        return False
    try:
        sent = datetime.fromtimestamp(int(timestamp), UTC)
    except (ValueError, OverflowError, OSError):
        return False
    if abs(((now or datetime.now(UTC)) - sent).total_seconds()) > 300:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix('sha256='))


def normalize_webhook_event(event: dict) -> dict | None:
    """Provider-neutral webhook event -> apply_feedback() keyword arguments."""
    kind = WEBHOOK_TYPES.get(str(event.get('type') or '').strip().lower().replace('-', '_'))
    event_id = str(event.get('id') or '')
    if not kind or not event_id:
        return None
    status = str(event.get('status') or '')
    diagnostic = str(event.get('diagnostic') or '')
    category, suppress = None, False
    if kind in {'hard_bounce', 'soft_bounce'}:
        derived, category, suppress = classify_report({'action': 'failed', 'status': status, 'diagnostic': diagnostic})
        if kind == 'hard_bounce' and derived == 'soft_bounce' and status.startswith('4.'):
            kind = 'soft_bounce'
        if kind == 'hard_bounce' and event.get('invalid_recipient') is True:
            suppress, category = True, 'invalid_recipient'
        if kind == 'soft_bounce':
            suppress = False
    return {'external_id': event_id, 'feedback_type': kind, 'category': category or
            ('confirmed_delivery' if kind == 'delivered' else None), 'suppress': suppress,
            'delivery_id': event.get('delivery_id'), 'message_id': event.get('message_id'),
            'recipient': event.get('recipient'), 'status_code': status or None, 'diagnostic': diagnostic}
