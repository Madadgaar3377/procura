"""Admin notification dashboard: delivery state, failure reasons, history and recovery.

Every status shown here is derived from stored facts. "Accepted by provider" (SMTP 250
or SMS gateway acceptance) is never reported as delivered; "Confirmed delivered" needs a
provider delivery report (SMS status lookup, DSN or webhook).
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (Contact, ContactChannel, DeliveryRestriction, EmailDeliveryEvent, Invitation, Organization,
                        Outbox, RFQ, User)
from app.security import admin_user, audit

router = APIRouter(prefix='/notifications', tags=['Notifications & Outbox'])

SUPPRESSED = ('suppressed', 'invalid_recipient', 'opted_out')
SUMMARY_LABELS = ('queued', 'processing', 'retry_scheduled', 'accepted', 'sent', 'delivered', 'failed',
                  'hard_bounced', 'soft_bounced', 'suppressed', 'skipped', 'uncertain', 'simulated')


def delivery_label(status: str | None, provider: str | None) -> str:
    """One display status per delivery job (superset of the buyer/notification labels)."""
    provider_l = (provider or '').lower()
    if provider_l == 'hard_bounced':
        return 'hard_bounced'
    if provider_l == 'soft_bounced':
        return 'soft_bounced'
    if status == 'skipped' and provider_l in SUPPRESSED:
        return 'suppressed'
    from app.modules.notifications.router import display_delivery_status
    label = display_delivery_status(SimpleNamespace(status=status, delivery_status=provider))
    return 'sending' if label == 'sending' else label


def _filters(rfq_id, org_id, channel, date_from, date_to):
    conditions = [Outbox.event == 'notification.delivery']
    if rfq_id:
        conditions.append(Outbox.payload['context']['rfq_id'].as_string() == rfq_id)
    if org_id:
        conditions.append(Outbox.payload['context']['org_id'].as_string() == org_id)
    if channel:
        if channel not in {'email', 'sms', 'whatsapp'}:
            raise HTTPException(422, 'channel must be email, sms or whatsapp')
        conditions.append(Outbox.payload['channel'].as_string() == channel)
    if date_from:
        conditions.append(Outbox.created_at >= date_from)
    if date_to:
        conditions.append(Outbox.created_at <= date_to)
    return conditions


def _status_condition(label: str):
    provider = func.lower(func.coalesce(Outbox.delivery_status, ''))
    not_bounced = provider.not_in(('hard_bounced', 'soft_bounced'))
    mapping = {
        'queued': Outbox.status == 'pending',
        'processing': Outbox.status == 'processing',
        'retry_scheduled': and_(Outbox.status == 'retry', not_bounced),
        'accepted': and_(Outbox.status.in_(('processed', 'awaiting_status')),
                         provider.in_(('accepted', 'pending', 'processed'))),
        'sent': and_(Outbox.status.in_(('processed', 'awaiting_status')), provider == 'sent'),
        'delivered': provider == 'delivered',
        'failed': and_(Outbox.status == 'failed', not_bounced),
        'hard_bounced': provider == 'hard_bounced',
        'soft_bounced': provider == 'soft_bounced',
        'suppressed': and_(Outbox.status == 'skipped', provider.in_(SUPPRESSED)),
        'skipped': and_(Outbox.status == 'skipped', provider.not_in(SUPPRESSED)),
        'uncertain': Outbox.status == 'uncertain',
        'simulated': provider == 'simulated',
        'pending_sms': and_(Outbox.payload['channel'].as_string().in_(('sms', 'whatsapp')),
                            Outbox.status.in_(('pending', 'retry', 'processing', 'awaiting_status')),
                            provider != 'delivered'),
    }
    if label not in mapping:
        raise HTTPException(422, f'Unknown status filter; use one of {", ".join(sorted(mapping))}')
    return mapping[label]


def _mask(destination: str | None) -> str:
    if not destination:
        return ''
    if '@' in destination:
        name, _, domain = destination.partition('@')
        return (name[:2] + '•••@' + domain) if len(name) > 2 else '•••@' + domain
    return destination[:4] + '•••' + destination[-3:]


def _retry_eligible(event, restricted: str | None) -> bool:
    if event.status not in {'failed', 'uncertain'}:
        return False
    if event.delivery_status in {'Failed', 'Cancelled'}:
        return False
    return not (restricted and event.payload.get('channel') == 'email')


def _row(event, rfqs, orgs, restrictions, history_counts):
    payload = event.payload or {}
    context = payload.get('context') or {}
    destination = payload.get('destination')
    restricted = restrictions.get((payload.get('channel'), destination))
    response = payload.get('provider_response') or {}
    rfq = rfqs.get(context.get('rfq_id'))
    return {
        'id': event.id, 'created_at': event.created_at, 'channel': payload.get('channel'),
        'template': payload.get('template'), 'status': delivery_label(event.status, event.delivery_status),
        'outbox_status': event.status, 'provider_status': event.delivery_status,
        'rfq_id': context.get('rfq_id'), 'rfq_reference': rfq.reference if rfq else context.get('reference'),
        'supplier_org_id': context.get('org_id'),
        'supplier_name': orgs.get(context.get('org_id')) or context.get('supplier_name'),
        'destination': _mask(destination), 'contact_name': payload.get('contact_name'),
        'recipient_source': payload.get('recipient_source'), 'attempts': event.attempts,
        'next_attempt_at': event.next_attempt_at, 'submitted_at': event.submitted_at,
        'provider_message_id': event.provider_message_id,
        'failure_category': response.get('category') or payload.get('feedback_category'),
        'failure_reason': event.last_error or payload.get('reason_detail') or payload.get('eligibility_reason'),
        'provider_response': {k: response.get(k) for k in ('stage', 'code', 'enhanced_code', 'response',
                                                            'failure_class', 'category', 'permanent')} if response else None,
        'soft_bounce_retries': payload.get('soft_bounce_retries') or 0,
        'restriction': {'id': restricted.id, 'reason': restricted.reason} if restricted else None,
        'history_count': history_counts.get(event.id, 0),
        'retry_eligible': _retry_eligible(event, restricted.reason if restricted else None),
        'correctable': bool(payload.get('procurement_recipient') and payload.get('channel') == 'email'
                            and (restricted or event.delivery_status in {'hard_bounced', 'invalid_recipient'})),
    }


@router.get('/admin/deliveries')
def deliveries(rfq_id: str | None = None, org_id: str | None = None, channel: str | None = None,
               status: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    conditions = _filters(rfq_id, org_id, channel, date_from, date_to)
    if status:
        conditions.append(_status_condition(status))
    total = int(db.scalar(select(func.count()).select_from(Outbox).where(*conditions)) or 0)
    events = db.scalars(select(Outbox).where(*conditions).order_by(Outbox.created_at.desc(), Outbox.id.desc())
                        .limit(limit).offset(offset)).all()
    rfq_ids = {(e.payload.get('context') or {}).get('rfq_id') for e in events} - {None}
    org_ids = {(e.payload.get('context') or {}).get('org_id') for e in events} - {None}
    rfqs = {r.id: r for r in db.scalars(select(RFQ).where(RFQ.id.in_(rfq_ids)))} if rfq_ids else {}
    orgs = dict(db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))).all()) if org_ids else {}
    pairs = {(e.payload.get('channel'), e.payload.get('destination')) for e in events if e.payload.get('destination')}
    restrictions = {}
    if pairs:
        for row in db.scalars(select(DeliveryRestriction).where(
                DeliveryRestriction.cleared_at.is_(None),
                DeliveryRestriction.destination.in_([d for _, d in pairs]))):
            restrictions[(row.channel, row.destination)] = row
    ids = [e.id for e in events]
    history = dict(db.execute(select(EmailDeliveryEvent.outbox_id, func.count()).where(
        EmailDeliveryEvent.outbox_id.in_(ids)).group_by(EmailDeliveryEvent.outbox_id)).all()) if ids else {}
    return {'total': total, 'limit': limit, 'offset': offset,
            'rows': [_row(e, rfqs, orgs, restrictions, history) for e in events]}


@router.get('/admin/deliveries/summary')
def summary(rfq_id: str | None = None, org_id: str | None = None, channel: str | None = None,
            date_from: datetime | None = None, date_to: datetime | None = None,
            admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    conditions = _filters(rfq_id, org_id, channel, date_from, date_to)
    grouped = select(Outbox.payload['channel'].as_string().label('channel'), Outbox.status.label('status'),
                     Outbox.delivery_status.label('provider')).where(*conditions).subquery()
    rows = db.execute(select(grouped.c.channel, grouped.c.status, grouped.c.provider, func.count())
                      .group_by(grouped.c.channel, grouped.c.status, grouped.c.provider)).all()
    counts = {label: 0 for label in SUMMARY_LABELS}
    by_channel = {'email': {}, 'sms': {}, 'whatsapp': {}}
    total = pending_sms = 0
    for ch, status, provider, count in rows:
        label = delivery_label(status, provider)
        label = 'processing' if label == 'sending' else label
        counts[label] = counts.get(label, 0) + int(count)
        bucket = by_channel.setdefault(ch or 'email', {})
        bucket[label] = bucket.get(label, 0) + int(count)
        total += int(count)
        if ch in {'sms', 'whatsapp'} and status in {'pending', 'retry', 'processing', 'awaiting_status'} \
                and (provider or '').lower() != 'delivered':
            pending_sms += int(count)
    return {'total': total, **counts, 'pending_sms': pending_sms, 'by_channel': by_channel,
            'note': 'accepted = accepted by the provider; delivery is confirmed only by a delivery report'}


@router.get('/admin/deliveries/{event_id}')
def delivery_detail(event_id: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    event = db.get(Outbox, event_id)
    if not event or event.event != 'notification.delivery':
        raise HTTPException(404, 'Delivery job not found')
    payload = event.payload or {}
    context = payload.get('context') or {}
    feedback = db.scalars(select(EmailDeliveryEvent).where(EmailDeliveryEvent.outbox_id == event.id)
                          .order_by(EmailDeliveryEvent.created_at)).all()
    from app.models import AuditEvent
    actions = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == event.id)
                         .order_by(AuditEvent.created_at)).all()
    related = db.scalars(select(Outbox).where(
        Outbox.event == 'notification.delivery', Outbox.id != event.id,
        Outbox.payload['destination'].as_string() == payload.get('destination'),
        Outbox.payload['context']['rfq_id'].as_string() == context.get('rfq_id')).order_by(Outbox.created_at).limit(20)).all() \
        if payload.get('destination') and context.get('rfq_id') else []
    restriction = db.scalar(select(DeliveryRestriction).where(
        DeliveryRestriction.channel == payload.get('channel'), DeliveryRestriction.destination == payload.get('destination')))
    base = _row(event, {}, {}, {(restriction.channel, restriction.destination): restriction}
                if restriction and restriction.cleared_at is None else {}, {event.id: len(feedback)})
    history = [{'at': event.created_at, 'kind': 'queued', 'detail': payload.get('template')}]
    if payload.get('last_attempt_at'):
        history.append({'at': payload['last_attempt_at'], 'kind': 'submission_attempt',
                        'detail': f'attempt {event.attempts}'})
    if event.submitted_at:
        history.append({'at': event.submitted_at, 'kind': 'provider_accepted' if event.delivery_status not in {None, 'submitting'}
                        else 'submitted', 'detail': event.provider_message_id})
    history += [{'at': f.created_at, 'kind': f.event_type, 'detail': ' '.join(filter(None, (f.status_code, f.diagnostic))),
                 'source': f.source, 'applied': f.applied} for f in feedback]
    history += [{'at': a.created_at, 'kind': a.action, 'detail': None} for a in actions]
    history.sort(key=lambda h: str(h['at']))
    return {**base, 'destination_full': payload.get('destination'), 'history': history,
            'related': [{'id': r.id, 'template': r.payload.get('template'), 'created_at': r.created_at,
                         'status': delivery_label(r.status, r.delivery_status)} for r in related],
            'restriction_history': {'reason': restriction.reason, 'detail': restriction.detail,
                                    'bounce_count': restriction.bounce_count, 'cleared_at': restriction.cleared_at}
            if restriction else None}


# ------------------------------------------------------------------ suppression list
@router.get('/admin/restrictions')
def restrictions(channel: str | None = None, reason: str | None = None, active: bool = True,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    conditions = [DeliveryRestriction.cleared_at.is_(None) if active else DeliveryRestriction.cleared_at.is_not(None)]
    if channel:
        conditions.append(DeliveryRestriction.channel == channel)
    if reason:
        conditions.append(DeliveryRestriction.reason == reason)
    total = int(db.scalar(select(func.count()).select_from(DeliveryRestriction).where(*conditions)) or 0)
    rows = db.scalars(select(DeliveryRestriction).where(*conditions).order_by(
        DeliveryRestriction.created_at.desc()).limit(limit).offset(offset)).all()
    return {'total': total, 'rows': [{'id': r.id, 'channel': r.channel, 'destination': r.destination,
                                      'reason': r.reason, 'detail': r.detail, 'bounce_count': r.bounce_count,
                                      'last_event_at': r.last_event_at, 'created_at': r.created_at,
                                      'cleared_at': r.cleared_at} for r in rows]}


class Revalidate(BaseModel):
    note: str = Field(min_length=5, max_length=500)


@router.post('/admin/restrictions/{restriction_id}/revalidate')
def revalidate(restriction_id: str, payload: Revalidate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """An admin confirmed the address is valid again (e.g. mailbox restored). History kept."""
    row = db.scalar(select(DeliveryRestriction).where(DeliveryRestriction.id == restriction_id).with_for_update())
    if not row:
        raise HTTPException(404, 'Restriction not found')
    if row.reason == 'opted_out':
        raise HTTPException(409, 'The recipient opted out; it cannot be revalidated by an administrator')
    if row.cleared_at is None:
        row.cleared_at = datetime.now(UTC)
        row.cleared_by_user_id = admin.id
        row.detail = (f'{row.detail or ""} | revalidated: {payload.note.strip()}')[-500:]
        audit(db, admin, 'notification.destination_revalidated', entity_id=row.id, reason=row.reason)
    db.commit()
    return {'id': row.id, 'cleared_at': row.cleared_at}


class CorrectContact(BaseModel):
    email: str = Field(min_length=5, max_length=220)
    resend: bool = True


@router.post('/admin/deliveries/{event_id}/correct-contact')
def correct_contact(event_id: str, payload: CorrectContact, admin: User = Depends(admin_user),
                    db: Session = Depends(get_db)):
    """Replace a bounced supplier email in that supplier's records and optionally resend.

    Only records of the delivery's supplier organization holding the bounced address are
    changed (contact channels, legacy contact email, business email). Nothing is deleted.
    """
    from app.modules.notifications.recipients import normalize_destination
    event = db.scalar(select(Outbox).where(Outbox.id == event_id).with_for_update())
    if not event or event.event != 'notification.delivery' or not event.payload.get('procurement_recipient') \
            or event.payload.get('channel') != 'email':
        raise HTTPException(404, 'Supplier email delivery not found')
    try:
        new = normalize_destination('email', payload.email)
    except ValueError as exc:
        raise HTTPException(422, 'Enter a valid email address') from exc
    old = event.payload['destination']
    org_id = (event.payload.get('context') or {}).get('org_id')
    if new == old:
        raise HTTPException(409, 'The new address is the same as the bounced address')
    if db.scalar(select(DeliveryRestriction.id).where(DeliveryRestriction.channel == 'email',
                                                      DeliveryRestriction.destination == new,
                                                      DeliveryRestriction.cleared_at.is_(None))):
        raise HTTPException(409, 'The new address is itself suppressed')
    changed = 0
    for channel in db.scalars(select(ContactChannel).where(ContactChannel.org_id == org_id, ContactChannel.kind == 'email',
                                                           ContactChannel.normalized_value == old)):
        duplicate = db.scalar(select(ContactChannel.id).where(ContactChannel.contact_id == channel.contact_id,
                                                              ContactChannel.kind == 'email',
                                                              ContactChannel.normalized_value == new))
        if duplicate:
            channel.needs_review = False
            continue
        channel.value, channel.normalized_value = new, new
        channel.needs_review, channel.is_verified = False, False
        changed += 1
    for contact in db.scalars(select(Contact).where(Contact.org_id == org_id, func.lower(Contact.email) == old)):
        contact.email = new
        contact.needs_review = False
        changed += 1
    org = db.get(Organization, org_id) if org_id else None
    if org and org.business_email and org.business_email.strip().casefold() == old:
        org.business_email = new
        changed += 1
    if not changed:
        raise HTTPException(409, 'The bounced address was not found in this supplier\'s records; edit the supplier profile')
    resent = None
    if payload.resend:
        resent = _requeue(db, event, new)
    audit(db, admin, 'notification.contact_corrected', org_id, event.id, records=changed, resent=bool(resent))
    db.commit()
    return {'updated_records': changed, 'resent_delivery_id': resent}


def _requeue(db, event, destination):
    context = event.payload.get('context') or {}
    rfq = db.get(RFQ, context.get('rfq_id'))
    invitation = db.scalar(select(Invitation).where(Invitation.rfq_id == context.get('rfq_id'),
                                                    Invitation.supplier_org_id == context.get('org_id')))
    from app.modules.notifications.procurement import rfq_solicitation_state
    if not rfq or not invitation or invitation.status not in {'invited', 'accepted'} or not rfq_solicitation_state(rfq)[0]:
        return None
    key = f'{event.dedupe_key or event.id}:corrected:{sha256(destination.encode()).hexdigest()[:16]}'
    if db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
        return None
    payload = {k: v for k, v in event.payload.items() if k in {
        'channel', 'template', 'procurement_recipient', 'contact_id', 'contact_name', 'recipient_source',
        'user_id', 'item_ids', 'context'}}
    row = Outbox(event='notification.delivery', dedupe_key=key, payload={**payload, 'destination': destination,
                                                                         'corrected_from': event.id})
    db.add(row)
    db.flush()
    return row.id


# ------------------------------------------------------------------ provider webhook
@router.post('/webhooks/email', include_in_schema=True)
async def email_webhook(request: Request, db: Session = Depends(get_db)):
    """Signed provider delivery/bounce events (HMAC-SHA256, see bounces.verify_webhook).

    Headers: ``X-Procura-Timestamp`` (unix seconds) and ``X-Procura-Signature``
    (``sha256=<hex HMAC of "<timestamp>.<raw body>">``). Body: ``{"events": [{"id",
    "type", "message_id", "delivery_id", "recipient", "status", "diagnostic"}]}``.
    """
    from app.modules.notifications.bounces import apply_feedback, normalize_webhook_event, verify_webhook
    from app.rate_limit import enforce_rate_limit
    enforce_rate_limit(request, scope='email-webhook', identity='provider', limit=600, window_seconds=60)
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(413, 'Payload too large')
    if not verify_webhook(body, request.headers.get('x-procura-timestamp'), request.headers.get('x-procura-signature')):
        raise HTTPException(401, 'Invalid webhook signature')
    import json
    try:
        events = json.loads(body).get('events')
    except (ValueError, AttributeError):
        raise HTTPException(422, 'Body must be JSON with an events list') from None
    if not isinstance(events, list) or len(events) > 500:
        raise HTTPException(422, 'events must be a list of at most 500 items')
    results = []
    for raw in events:
        normalized = normalize_webhook_event(raw) if isinstance(raw, dict) else None
        results.append(apply_feedback(db, source='webhook', **normalized) if normalized else 'ignored_invalid')
    db.commit()
    return {'results': results}
