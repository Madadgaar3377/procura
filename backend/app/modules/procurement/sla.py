"""SLA reminders and escalation (client guide feature 5).

A periodic sweep queues at most one ``sla.reminder`` outbox event per rule,
entity and reminder number (dedupe key), so reminders are never duplicated.
Each rule's query only selects work that is still outstanding, and the worker
re-checks the condition before sending, so reminders stop as soon as the
action is completed. Intervals are configurable by administrators.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, select

from app.models import (DeliveryNote, Invitation, Outbox, PlatformSettings, PurchaseOrder, Quotation, RFQ,
                        TaxInvoice)
from app.security import as_utc

DEFAULT_SLA = {
    'enabled': True,
    'supplier_response_reminder_hours': 48,   # invited supplier has not submitted a quotation
    'supplier_escalation_hours': 96,          # escalate a silent supplier to the buyer
    'quotation_approval_reminder_hours': 24,  # prepared draft awaiting supplier approval
    'buyer_decision_reminder_hours': 48,      # quotations received after deadline, nothing approved
    'po_issue_reminder_hours': 24,            # draft purchase order not issued
    'delivery_overdue_grace_hours': 24,       # issued PO past required delivery date
    'invoice_review_reminder_hours': 48,      # submitted invoice not reviewed
    'repeat_every_hours': 24,
    'max_reminders': 3,
}
BATCH = 200


def get_config(db) -> dict:
    row = db.get(PlatformSettings, 'default')
    return {**DEFAULT_SLA, **((row.sla_config or {}) if row else {})}


def save_config(db, values: dict) -> dict:
    clean = {}
    for key, default in DEFAULT_SLA.items():
        if key not in values:
            continue
        value = values[key]
        if isinstance(default, bool):
            clean[key] = bool(value)
        else:
            number = int(value)
            if number < 1 or number > 24 * 90:
                raise ValueError(f'{key} must be between 1 and {24 * 90} hours')
            clean[key] = number
    row = db.get(PlatformSettings, 'default')
    if not row:
        row = PlatformSettings(id='default')
        db.add(row)
    row.sla_config = {**(row.sla_config or {}), **clean}
    db.flush()
    return get_config(db)


def _number(started: datetime, threshold_h: int, cfg: dict, now: datetime) -> int | None:
    elapsed = (now - as_utc(started)).total_seconds() / 3600 - threshold_h
    if elapsed < 0:
        return None
    n = int(elapsed // cfg['repeat_every_hours']) + 1
    return n if n <= cfg['max_reminders'] else None


def _queue(db, rule: str, entity_id: str, n: int, payload: dict) -> bool:
    key = f'sla:{rule}:{entity_id}:{n}'
    if db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
        return False
    db.add(Outbox(event='sla.reminder', payload={'rule': rule, 'entity_id': entity_id, 'n': n, **payload}, dedupe_key=key))
    return True


def sweep(db, now: datetime | None = None) -> int:
    cfg = get_config(db)
    if not cfg['enabled']:
        return 0
    now = as_utc(now or datetime.now(UTC))
    queued = 0
    open_rfq = and_(RFQ.status.in_({'published', 'collecting_quotes'}), RFQ.response_deadline > now)
    responded = exists().where(Quotation.invitation_id == Invitation.id, Quotation.status != 'draft')

    # 1. Suppliers who have not responded (reminder, then escalation to the buyer).
    oldest = now - timedelta(hours=min(cfg['supplier_response_reminder_hours'], cfg['supplier_escalation_hours']))
    for inv, rfq in db.execute(select(Invitation, RFQ).join(RFQ, RFQ.id == Invitation.rfq_id).where(
            open_rfq, Invitation.status.in_({'invited', 'accepted'}), Invitation.invited_at <= oldest, ~responded)
            .order_by(Invitation.invited_at).limit(BATCH)).all():
        n = _number(inv.invited_at, cfg['supplier_response_reminder_hours'], cfg, now)
        if n:
            queued += _queue(db, 'supplier_response', inv.id, n, {'rfq_id': rfq.id, 'org_id': inv.supplier_org_id})
        if _number(inv.invited_at, cfg['supplier_escalation_hours'], {**cfg, 'max_reminders': 1}, now):
            queued += _queue(db, 'supplier_escalation', inv.id, 1, {'rfq_id': rfq.id, 'org_id': rfq.buyer_org_id, 'supplier_org_id': inv.supplier_org_id})

    # 2. Prepared drafts awaiting supplier approval.
    for quote in db.scalars(select(Quotation).join(RFQ, RFQ.id == Quotation.rfq_id).where(
            open_rfq, Quotation.status == 'draft',
            Quotation.created_at <= now - timedelta(hours=cfg['quotation_approval_reminder_hours'])).limit(BATCH)).all():
        n = _number(quote.created_at, cfg['quotation_approval_reminder_hours'], cfg, now)
        if n:
            queued += _queue(db, 'quotation_approval', quote.id, n, {'rfq_id': quote.rfq_id, 'org_id': quote.supplier_org_id})

    # 3. Pending buyer decision: deadline passed, quotations received, nothing approved.
    awarded = exists().where(Quotation.rfq_id == RFQ.id, Quotation.status == 'awarded')
    received = exists().where(Quotation.rfq_id == RFQ.id, Quotation.status.in_({'submitted', 'shortlisted'}))
    for rfq in db.scalars(select(RFQ).where(RFQ.status.in_({'published', 'collecting_quotes', 'closed'}), RFQ.response_deadline <= now,
                                            RFQ.response_deadline > now - timedelta(days=60), received, ~awarded).limit(BATCH)).all():
        n = _number(rfq.response_deadline, cfg['buyer_decision_reminder_hours'], cfg, now)
        if n:
            queued += _queue(db, 'buyer_decision', rfq.id, n, {'rfq_id': rfq.id, 'org_id': rfq.buyer_org_id})

    # 4. Draft purchase orders not issued.
    for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.status == 'draft',
                                                     PurchaseOrder.created_at <= now - timedelta(hours=cfg['po_issue_reminder_hours'])).limit(BATCH)).all():
        n = _number(po.created_at, cfg['po_issue_reminder_hours'], cfg, now)
        if n:
            queued += _queue(db, 'po_issue', po.id, n, {'org_id': po.buyer_org_id})

    # 5. Overdue deliveries.
    for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.status.in_({'issued', 'acknowledged', 'partially_delivered'}),
                                                     PurchaseOrder.required_delivery_date.is_not(None),
                                                     PurchaseOrder.required_delivery_date < now.date()).limit(BATCH)).all():
        due = datetime.combine(po.required_delivery_date, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
        n = _number(due, cfg['delivery_overdue_grace_hours'], cfg, now)
        if n:
            queued += _queue(db, 'delivery_overdue', po.id, n, {'org_id': po.supplier_org_id, 'buyer_org_id': po.buyer_org_id})

    # 6. Pending buyer actions: submitted invoices and dispatched deliveries awaiting receipt.
    for invoice in db.scalars(select(TaxInvoice).where(TaxInvoice.status == 'submitted',
                                                       TaxInvoice.submitted_at <= now - timedelta(hours=cfg['invoice_review_reminder_hours'])).limit(BATCH)).all():
        n = _number(invoice.submitted_at, cfg['invoice_review_reminder_hours'], cfg, now)
        if n:
            queued += _queue(db, 'invoice_review', invoice.id, n, {'org_id': invoice.buyer_org_id})
    for note in db.scalars(select(DeliveryNote).where(DeliveryNote.status == 'dispatched',
                                                      DeliveryNote.dispatched_at <= now - timedelta(hours=cfg['invoice_review_reminder_hours'])).limit(BATCH)).all():
        n = _number(note.dispatched_at, cfg['invoice_review_reminder_hours'], cfg, now)
        if n:
            queued += _queue(db, 'goods_receipt', note.id, n, {'org_id': note.buyer_org_id})
    if queued:
        db.commit()
    return queued


def still_outstanding(db, rule: str, entity_id: str, now: datetime | None = None) -> bool:
    """Re-check at send time so a completed action never triggers a stale reminder."""
    now = as_utc(now or datetime.now(UTC))
    if rule in {'supplier_response', 'supplier_escalation'}:
        inv = db.get(Invitation, entity_id)
        rfq = db.get(RFQ, inv.rfq_id) if inv else None
        return bool(inv and rfq and inv.status in {'invited', 'accepted'} and rfq.status in {'published', 'collecting_quotes'}
                    and as_utc(rfq.response_deadline) > now and not db.scalar(select(Quotation.id).where(
                        Quotation.invitation_id == inv.id, Quotation.status != 'draft')))
    if rule == 'quotation_approval':
        quote = db.get(Quotation, entity_id)
        rfq = db.get(RFQ, quote.rfq_id) if quote else None
        return bool(quote and quote.status == 'draft' and rfq and as_utc(rfq.response_deadline) > now)
    if rule == 'buyer_decision':
        return not db.scalar(select(Quotation.id).where(Quotation.rfq_id == entity_id, Quotation.status == 'awarded'))
    if rule == 'po_issue':
        po = db.get(PurchaseOrder, entity_id)
        return bool(po and po.status == 'draft')
    if rule == 'delivery_overdue':
        po = db.get(PurchaseOrder, entity_id)
        return bool(po and po.status in {'issued', 'acknowledged', 'partially_delivered'})
    if rule == 'invoice_review':
        invoice = db.get(TaxInvoice, entity_id)
        return bool(invoice and invoice.status == 'submitted')
    if rule == 'goods_receipt':
        note = db.get(DeliveryNote, entity_id)
        return bool(note and note.status == 'dispatched')
    return False


MESSAGES = {
    'supplier_response': 'Reminder: RFQ {ref} is waiting for your quotation. Your prepared quotation only needs review and approval.',
    'supplier_escalation': 'Escalation: {supplier} has not responded to RFQ {ref}. Consider contacting them or relying on other offers.',
    'quotation_approval': 'Reminder: your prepared quotation for RFQ {ref} is waiting for your approval.',
    'buyer_decision': 'Action needed: quotations for RFQ {ref} are ready for comparison and approval.',
    'po_issue': 'Action needed: purchase order {ref} is prepared and waiting to be issued.',
    'delivery_overdue': 'Delivery overdue: purchase order {ref} has passed its required delivery date.',
    'invoice_review': 'Action needed: invoice {ref} is waiting for your review.',
    'goods_receipt': 'Action needed: confirm receipt of delivery {ref}.',
}
