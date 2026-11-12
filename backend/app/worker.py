"""Leased transactional outbox worker for procurement notifications."""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import ContactInquiry, EmailOTP, Invitation, Member, Organization, Outbox, PhoneOTP, RFQ, User
from app.modules.matching.service import match_and_invite
from app.modules.notifications.crypto import decrypt_delivery_secret
from app.modules.notifications.providers import DeliveryError, get_sms_status, send_email, send_sms
from app.modules.notifications.service import enqueue_org_notice, update_delivery_notification
from app.modules.notifications.templates import render_message

log = logging.getLogger(__name__)
LEASE_SECONDS = 90
STATUS_POLL_SECONDS = 30


class PermanentOutboxError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def schedule_deadline_reminders(db: Session, now: datetime | None = None) -> int:
    now = _utc(now or datetime.now(timezone.utc))
    upper = now + timedelta(hours=24)
    due = db.scalars(select(RFQ).where(
        RFQ.status.in_({'published', 'collecting_quotes'}),
        RFQ.response_deadline > now,
        RFQ.response_deadline <= upper,
    )).all()
    queued = 0
    for rfq in due:
        key = f'rfq-deadline-24h:{rfq.id}'
        if db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
            continue
        db.add(Outbox(event='rfq.deadline.reminder', payload={'rfq_id': rfq.id}, dedupe_key=key))
        queued += 1
    if queued:
        db.commit()
    return queued


def _queue_rfq_recipients(db: Session, rfq: RFQ, source_key: str, template: str) -> None:
    context = {'reference': rfq.reference, 'title': rfq.title}
    enqueue_org_notice(db, org_id=rfq.buyer_org_id, template=template, context=context, source_key=source_key)
    supplier_ids = db.scalars(select(Member.organization_id).join(
        Invitation, Invitation.supplier_org_id == Member.organization_id,
    ).where(Invitation.rfq_id == rfq.id).distinct()).all()
    for org_id in supplier_ids:
        enqueue_org_notice(db, org_id=org_id, template=template, context=context, source_key=f'{source_key}:{org_id}')


def _queue_organization_update(db: Session, event: Outbox) -> None:
    org_id = event.payload.get('org_id')
    if not org_id:
        raise PermanentOutboxError('Organization notification has no organization id')
    org = db.get(Organization, org_id)
    if not org:
        raise PermanentOutboxError('Organization notification target no longer exists')
    enqueue_org_notice(
        db,
        org_id=org_id,
        template='verification_updated',
        context={'status': str(event.payload.get('status', 'updated'))},
        source_key=f'verification-update:{event.id}',
    )


def _prepare_notification(db: Session, event: Outbox) -> None:
    rfq_id = event.payload.get('rfq_id')
    rfq = db.get(RFQ, rfq_id) if rfq_id else None
    if not rfq:
        raise PermanentOutboxError('RFQ notification target no longer exists')
    deadline=_utc(rfq.response_deadline)
    if event.event in {'rfq.invitation','rfq.deadline.reminder'} and (
        rfq.status not in {'published','collecting_quotes'} or deadline<=datetime.now(timezone.utc)
    ):
        event.status='skipped'
        event.delivery_status='rfq_closed_or_expired'
        return
    template = 'deadline_reminder'
    if event.event == 'rfq.invitation':
        org_id = event.payload.get('supplier_org_id')
        if not org_id or not db.get(Organization, org_id):
            raise PermanentOutboxError('RFQ invitation target no longer exists')
        enqueue_org_notice(db, org_id=org_id, template='rfq_invitation', context={'reference': rfq.reference, 'title': rfq.title}, source_key=f'rfq-invitation:{event.payload.get("invitation_id", event.id)}')
        return
    if event.event == 'quotation.submitted':
        enqueue_org_notice(
            db,
            org_id=rfq.buyer_org_id,
            template='quotation_submitted',
            context={'reference': rfq.reference, 'title': rfq.title},
            source_key=f'quotation-submitted:{event.payload.get("quotation_id", event.id)}',
        )
        return
    _queue_rfq_recipients(db, rfq, f'rfq-deadline-reminder:{rfq.id}', template)


def _set_notification_status(db: Session, event: Outbox, status: str) -> None:
    update_delivery_notification(db, event.id, status)


def _remaining_code_ttl(db: Session, template: str, context: dict, now: datetime) -> int | None:
    if template == 'phone_otp':
        code = db.get(PhoneOTP, context.get('otp_id'))
    elif template in {'email_otp', 'password_reset'}:
        code = db.get(EmailOTP, context.get('otp_id'))
    else:
        return 3600
    if not code or code.used:
        return None
    seconds = int((_utc(code.expires_at) - now).total_seconds())
    return seconds if seconds > 0 else None


def _deliver(db: Session, event: Outbox, now: datetime) -> None:
    payload = event.payload
    channel = payload.get('channel')
    if channel not in {'sms', 'email'}:
        raise PermanentOutboxError('Unsupported notification channel')
    user = db.get(User, payload.get('user_id'))
    if not user or not user.is_active:
        event.status = 'skipped'
        event.delivery_status = 'recipient_inactive'
        _set_notification_status(db, event, 'skipped')
        return

    template = str(payload.get('template', ''))
    authentication_message = template in {'phone_otp', 'email_otp', 'password_reset'}
    ttl_seconds = 3600
    if authentication_message:
        ttl_seconds = _remaining_code_ttl(db, template, payload.get('context') or {}, now)
        if ttl_seconds is None:
            event.status = 'skipped'
            event.delivery_status = 'code_expired'
            event.secret_ciphertext = None
            return
    if channel == 'sms':
        if not user.phone or (not authentication_message and (not user.phone_verified or not user.sms_notifications_enabled)):
            event.status = 'skipped'
            event.delivery_status = 'recipient_not_verified_or_opted_in'
            _set_notification_status(db, event, 'skipped')
            return
        recipient = user.phone
    else:
        if not user.email or (not authentication_message and (not user.email_verified or not user.email_notifications_enabled)):
            event.status = 'skipped'
            event.delivery_status = 'recipient_not_verified_or_opted_in'
            _set_notification_status(db, event, 'skipped')
            return
        recipient = user.email

    secret = decrypt_delivery_secret(event.secret_ciphertext) if event.secret_ciphertext else None
    subject, plain_text, html_body = render_message(template, payload.get('context') or {}, secret)
    if channel == 'sms':
        if event.provider_message_id:
            result = get_sms_status(event.provider_message_id)
            event.status_checks += 1
            state = str(result.get('state', 'Unknown'))
            event.delivery_status = state
            event.last_error = 'Provider reported delivery failure' if state == 'Failed' else None
            if state == 'Delivered':
                event.status = 'processed'
                event.next_attempt_at = None
                _set_notification_status(db, event, 'delivered')
            elif state in {'Failed', 'Cancelled'}:
                event.status = 'failed'
                event.next_attempt_at = None
                _set_notification_status(db, event, 'failed')
            elif state == 'Sent':
                event.status = 'processed'
                event.next_attempt_at = None
                _set_notification_status(db, event, 'sent')
            elif event.status_checks >= get_settings().delivery_status_max_polls:
                event.status = 'failed'
                event.delivery_status = f'{state}_unconfirmed'[:40]
                event.next_attempt_at = None
                event.last_error = 'Provider delivery status did not become final before the polling limit'
                _set_notification_status(db, event, 'failed')
            else:
                event.status = 'awaiting_status'
                event.next_attempt_at = now + timedelta(seconds=STATUS_POLL_SECONDS)
            return

        result = send_sms(recipient, plain_text, event.id, ttl_seconds=ttl_seconds)
        event.attempts += 1
        event.provider_message_id = result['id']
        state = str(result.get('state', 'Pending'))
        event.delivery_status = state
        if state == 'Delivered':
            event.status = 'processed'
            _set_notification_status(db, event, 'delivered')
        elif state in {'Failed', 'Cancelled'}:
            event.status = 'failed'
            _set_notification_status(db, event, 'failed')
        elif state == 'Sent':
            event.status = 'processed'
            _set_notification_status(db, event, 'sent')
        else:
            event.status = 'awaiting_status'
            event.next_attempt_at = now + timedelta(seconds=STATUS_POLL_SECONDS)
        event.secret_ciphertext = None
        return

    event.provider_message_id = send_email(recipient, subject, plain_text, html_body, event.id)
    event.attempts += 1
    event.delivery_status = 'accepted'
    event.status = 'processed'
    event.next_attempt_at = None
    event.secret_ciphertext = None
    _set_notification_status(db, event, 'sent')


def _process_event(db: Session, event: Outbox, now: datetime) -> None:
    if event.event == 'cache.public.invalidate':
        from app import catalog_cache
        if not catalog_cache.invalidate():
            # Retry without exhausting notification delivery attempts.
            event.status = 'retry'
            event.next_attempt_at = now + timedelta(seconds=15)
    elif event.event == 'rfq.published':
        rfq = db.scalar(select(RFQ).where(RFQ.id==event.payload.get('rfq_id')).with_for_update())
        if not rfq or rfq.status not in {'published', 'collecting_quotes'}:
            raise PermanentOutboxError('RFQ is not dispatchable')
        match_and_invite(db, rfq)
    elif event.event in {'rfq.invitation', 'quotation.submitted', 'rfq.deadline.reminder'}:
        _prepare_notification(db, event)
    elif event.event == 'company.verification_updated':
        _queue_organization_update(db, event)
    elif event.event == 'notification.delivery':
        _deliver(db, event, now)
    elif event.event in {'contact.inquiry.support', 'contact.inquiry.ack'}:
        _deliver_contact_inquiry(db, event)
    else:
        raise PermanentOutboxError('Unsupported outbox event')


def _deliver_contact_inquiry(db: Session, event: Outbox) -> None:
    """Send contact notifications through the existing durable, idempotent SMTP adapter."""
    from html import escape
    inquiry = db.get(ContactInquiry, event.payload.get('inquiry_id'))
    if inquiry is None:
        raise PermanentOutboxError('Contact inquiry no longer exists')
    submitted = _utc(inquiry.created_at).strftime('%Y-%m-%d %H:%M UTC')
    if event.event.endswith('.support'):
        recipient = 'support@procumaterial.com'
        subject = f'Contact request {inquiry.ticket_reference}: {inquiry.subject}'
        plain = (f"Ticket: {inquiry.ticket_reference}\nName: {inquiry.name}\nEmail: {inquiry.email}\n"
                 f"Company: {inquiry.company or 'Not provided'}\nUser type: {inquiry.user_type}\n"
                 f"Category: {inquiry.category}\nSubject: {inquiry.subject}\nSubmitted: {submitted}\n\n{inquiry.message}")
    else:
        recipient = inquiry.email
        subject = f'We received your Procumaterial enquiry — {inquiry.ticket_reference}'
        plain = (f"Hello {inquiry.name},\n\nWe received your enquiry and recorded it under reference "
                 f"{inquiry.ticket_reference}.\n\nYou can contact support at support@procumaterial.com or 0501496516.\n\n"
                 "Procumaterial Support")
    html = '<html><body><p>' + escape(plain).replace('\n', '<br>') + '</p></body></html>'
    event.provider_message_id = send_email(recipient, subject, plain, html, event.id)
    event.attempts += 1
    event.delivery_status = 'accepted'
    event.status = 'processed'
    event.next_attempt_at = None


def _claim_batch(db: Session, limit: int, now: datetime) -> list[str]:
    due = or_(Outbox.next_attempt_at.is_(None), Outbox.next_attempt_at <= now)
    eligible = or_(
        and_(Outbox.status.in_({'pending', 'retry'}), due),
        and_(Outbox.status == 'awaiting_status', due),
        and_(Outbox.status == 'processing', or_(Outbox.lock_until.is_(None), Outbox.lock_until <= now)),
    )
    rows = db.scalars(
        select(Outbox).where(eligible).order_by(Outbox.created_at).limit(limit).with_for_update(skip_locked=True)
    ).all()
    ids = []
    for item in rows:
        item.status = 'processing'
        item.lock_until = now + timedelta(seconds=LEASE_SECONDS)
        ids.append(item.id)
    if ids:
        db.commit()
    return ids


def process_batch(db: Session, limit: int = 25, now: datetime | None = None):
    now = _utc(now or datetime.now(timezone.utc))
    schedule_deadline_reminders(db, now)
    ids = _claim_batch(db, limit, now)
    processed = []
    for event_id in ids:
        try:
            event = db.scalar(select(Outbox).where(Outbox.id == event_id).with_for_update())
            if not event:
                continue
            _process_event(db, event, now)
            event.lock_until = None
            if event.status == 'processing':
                event.status = 'processed'
            event.last_error = None
            db.commit()
            if event.status in {'processed', 'skipped', 'awaiting_status'}:
                processed.append(event_id)
        except Exception as exc:
            db.rollback()
            event = db.get(Outbox, event_id)
            if event:
                event.attempts += 1
                event.lock_until = None
                event.last_error = str(exc)[:500] if isinstance(exc, DeliveryError) else type(exc).__name__
                retryable = not isinstance(exc, PermanentOutboxError) and getattr(exc, 'retryable', True)
                if retryable and event.attempts < get_settings().delivery_max_attempts:
                    event.status = 'retry'
                    event.next_attempt_at = now + timedelta(seconds=min(900, 15 * (2 ** (event.attempts - 1))))
                else:
                    event.status = 'failed'
                    event.next_attempt_at = None
                    _set_notification_status(db, event, 'failed')
                db.commit()
            log.warning('Outbox delivery failed for event id %s (%s)', event_id, type(exc).__name__)
    return processed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    while True:
        with SessionLocal() as db:
            done = process_batch(db)
        if done:
            log.info('Processed %s outbox events', len(done))
        if args.once:
            break
        time.sleep(max(1, args.interval))


if __name__ == '__main__':
    main()
