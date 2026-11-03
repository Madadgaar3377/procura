"""Outbox helpers for consent-gated transactional delivery."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AccountPreference, Member, Notification, Outbox, User
from app.modules.notifications.crypto import encrypt_delivery_secret

_NOTICE_TEXT = {
    'rfq_invitation': 'A supplier RFQ invitation is available in your account.',
    'quotation_submitted': 'A new quotation is available in your account.',
    'verification_updated': 'Your company verification status was updated.',
    'deadline_reminder': 'An RFQ response deadline is approaching.',
}


def queue_delivery(
    db: Session,
    *,
    user_id: str,
    channel: str,
    template: str,
    context: dict | None = None,
    secret: str | None = None,
    dedupe_key: str,
) -> Outbox:
    existing = db.scalar(select(Outbox).where(Outbox.dedupe_key == dedupe_key))
    if existing:
        return existing
    outbox = Outbox(
        event='notification.delivery',
        payload={'user_id': user_id, 'channel': channel, 'template': template, 'context': context or {}},
        dedupe_key=dedupe_key,
        secret_ciphertext=encrypt_delivery_secret(secret) if secret is not None else None,
    )
    db.add(outbox)
    db.flush()
    if template not in {'phone_otp', 'email_otp', 'password_reset'}:
        db.add(Notification(
            recipient_user_id=user_id,
            event=template,
            message=_NOTICE_TEXT.get(template, 'You have a new account notification.'),
            channel=channel,
            status='queued',
            delivery_outbox_id=outbox.id,
        ))
    return outbox


def enqueue_org_notice(db: Session, *, org_id: str, template: str, context: dict, source_key: str) -> int:
    """Queue only for verified, explicitly opted-in account channels."""
    queued = 0
    members = db.scalars(select(Member).where(Member.organization_id == org_id)).all()
    for member in members:
        user = db.get(User, member.user_id)
        if not user or not user.is_active:
            continue
        prefs=db.get(AccountPreference,user.id)
        if prefs and not prefs.events.get(template,True):continue
        if user.sms_notifications_enabled and user.phone_verified:
            queue_delivery(
                db,
                user_id=user.id,
                channel='sms',
                template=template,
                context=context,
                dedupe_key=f'{source_key}:{user.id}:sms',
            )
            queued += 1
        if user.email_notifications_enabled and user.email_verified and user.email:
            queue_delivery(
                db,
                user_id=user.id,
                channel='email',
                template=template,
                context=context,
                dedupe_key=f'{source_key}:{user.id}:email',
            )
            queued += 1
    return queued


def update_delivery_notification(db: Session, outbox_id: str, status: str) -> None:
    item = db.scalar(select(Notification).where(Notification.delivery_outbox_id == outbox_id))
    if item:
        item.status = status
