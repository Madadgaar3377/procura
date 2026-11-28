"""Line-by-line RFQ tracking for buyers and grouped RFQ inbox for suppliers.

Client status words: Open, Quoted, Approved, Pending (buyer); Draft, Submitted (supplier).
All data is loaded with a fixed number of set-based queries (no per-line queries).
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select

from app.models import (Invitation, Organization, Outbox, PurchaseOrder, Quotation, QuoteLine, RFQ, RFQItem, RFQMatch,
                        SupplierAccessToken)
from app.modules.procurement.portal import matched_item_ids

SUBMITTED = {'submitted', 'shortlisted', 'awarded'}


def _invitation_items(db, rfq_id: str, invitations: list[Invitation]) -> dict[str, set[str]]:
    result = {}
    missing = [inv for inv in invitations if not inv.matched_item_ids]
    reasons = {}
    if missing:
        for match in db.scalars(select(RFQMatch).where(RFQMatch.rfq_id == rfq_id, RFQMatch.supplier_org_id.in_([i.supplier_org_id for i in missing]))):
            reasons[match.supplier_org_id] = {r[5:] for r in (match.reasons or []) if isinstance(r, str) and r.startswith('item:')}
    for inv in invitations:
        result[inv.id] = set(inv.matched_item_ids or reasons.get(inv.supplier_org_id, set()))
    return result


def buyer_tracking(db, rfq: RFQ) -> dict:
    items = db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id,RFQItem.is_active.is_(True)).order_by(RFQItem.created_at, RFQItem.id)).all()
    invitations = db.scalars(select(Invitation).where(Invitation.rfq_id == rfq.id)).all()
    covered = _invitation_items(db, rfq.id, invitations)
    quotes = db.scalars(select(Quotation).where(Quotation.rfq_id == rfq.id)).all()
    quote_by_id = {q.id: q for q in quotes}
    lines = db.scalars(select(QuoteLine).where(QuoteLine.quotation_id.in_(list(quote_by_id)))).all() if quotes else []
    names = dict(db.execute(select(Organization.id, Organization.name).where(
        Organization.id.in_({i.supplier_org_id for i in invitations} | {q.supplier_org_id for q in quotes}))).all()) if invitations or quotes else {}
    sent = defaultdict(set)
    for event_key, status in db.execute(select(Outbox.dedupe_key, Outbox.status).where(
            Outbox.event == 'notification.delivery', Outbox.payload[('context', 'rfq_id')].as_string() == rfq.id)).all():
        sent[status].add(event_key)
    po_by_quote = dict(db.execute(select(PurchaseOrder.quotation_id, PurchaseOrder.reference).where(PurchaseOrder.rfq_id == rfq.id)).all())

    lines_by_item = defaultdict(list)
    for line in lines:
        lines_by_item[line.rfq_item_id].append(line)
    rows = []
    counts = defaultdict(int)
    for item in items:
        inv_for_item = [inv for inv in invitations if item.id in covered.get(inv.id, set())]
        submitted = [l for l in lines_by_item[item.id] if quote_by_id[l.quotation_id].status in SUBMITTED]
        drafts = [l for l in lines_by_item[item.id] if quote_by_id[l.quotation_id].status == 'draft']
        awarded = [l for l in submitted if quote_by_id[l.quotation_id].status == 'awarded']
        best = min(submitted, key=lambda l: Decimal(l.unit_price) * (1 - Decimal(l.discount_percent or 0) / 100), default=None)
        if awarded:
            status = 'Approved'
        elif submitted:
            status = 'Quoted'
        elif inv_for_item:
            status = 'Pending'
        else:
            status = 'Open'
        counts[status] += 1
        selected = awarded[0] if awarded else None
        rows.append({
            'rfq_item_id': item.id,
            'description': item.description,
            'specifications': item.specifications,
            'requested_quantity': str(item.quantity),
            'unit': item.unit,
            'matched_suppliers': len(inv_for_item),
            'invitations_sent': len([inv for inv in inv_for_item if inv.status != 'declined']),
            'declined': len([inv for inv in inv_for_item if inv.status == 'declined']),
            'drafts_in_progress': len(drafts),
            'quotations_received': len(submitted),
            'best_quote': {
                'supplier_name': names.get(quote_by_id[best.quotation_id].supplier_org_id, ''),
                'quantity': str(best.quantity), 'unit_price': str(best.unit_price),
                'currency': quote_by_id[best.quotation_id].currency, 'lead_days': best.lead_days,
            } if best else None,
            'offers': [{
                'quotation_id': l.quotation_id, 'supplier_name': names.get(quote_by_id[l.quotation_id].supplier_org_id, ''),
                'quantity': str(l.quantity), 'unit_price': str(l.unit_price), 'currency': quote_by_id[l.quotation_id].currency,
                'status': quote_by_id[l.quotation_id].status,
            } for l in sorted(submitted, key=lambda l: Decimal(l.unit_price))],
            'selected_supplier': names.get(quote_by_id[selected.quotation_id].supplier_org_id) if selected else None,
            'purchase_order': po_by_quote.get(selected.quotation_id) if selected else None,
            'status': status,
        })
    return {
        'rfq_id': rfq.id,
        'reference': rfq.reference,
        'rfq_status': rfq.status,
        'response_deadline': rfq.response_deadline,
        'summary': {
            'lines': len(items), 'open': counts['Open'], 'pending': counts['Pending'], 'quoted': counts['Quoted'], 'approved': counts['Approved'],
            'suppliers_invited': len(invitations), 'quotations_received': len([q for q in quotes if q.status in SUBMITTED]),
            'notifications': {status: len(keys) for status, keys in sent.items()},
        },
        'lines': rows,
    }


def supplier_inbox(db, org_id: str, *, limit: int = 50, offset: int = 0) -> list[dict]:
    """Incoming RFQs with grouped matched items, draft and submission state, newest first."""
    rows = db.execute(select(Invitation, RFQ).join(RFQ, RFQ.id == Invitation.rfq_id)
                      .where(Invitation.supplier_org_id == org_id).order_by(Invitation.invited_at.desc(), Invitation.id)
                      .limit(limit).offset(offset)).all()
    if not rows:
        return []
    rfq_ids = [rfq.id for _, rfq in rows]
    item_counts = dict(db.execute(select(RFQItem.rfq_id, func.count()).where(RFQItem.rfq_id.in_(rfq_ids),RFQItem.is_active.is_(True)).group_by(RFQItem.rfq_id)).all())
    quotes = {q.invitation_id: q for q in db.scalars(select(Quotation).where(Quotation.invitation_id.in_([inv.id for inv, _ in rows])))}
    line_counts = dict(db.execute(select(QuoteLine.quotation_id, func.count()).where(QuoteLine.quotation_id.in_([q.id for q in quotes.values()]))
                                  .group_by(QuoteLine.quotation_id)).all()) if quotes else {}
    pos = dict(db.execute(select(PurchaseOrder.quotation_id, PurchaseOrder.reference).where(PurchaseOrder.quotation_id.in_([q.id for q in quotes.values()]), PurchaseOrder.status != 'draft')).all()) if quotes else {}
    output = []
    for inv, rfq in rows:
        quote = quotes.get(inv.id)
        matched = matched_item_ids(db, inv)
        output.append({
            'invitation_id': inv.id, 'rfq_id': rfq.id, 'reference': rfq.reference, 'title': rfq.project_name or rfq.title,
            'city': rfq.city, 'response_deadline': rfq.response_deadline, 'invitation_status': inv.status,
            'matched_items': len(matched), 'total_items': item_counts.get(rfq.id, 0),
            'quotation_id': quote.id if quote else None,
            'quotation_status': ('Draft' if quote.status == 'draft' else 'Submitted') if quote else 'Not started',
            'quotation_detail_status': quote.status if quote else None,
            'prefilled_lines': line_counts.get(quote.id, 0) if quote else 0,
            'purchase_order': pos.get(quote.id) if quote else None,
        })
    return output


def link_status(db, invitation_id: str) -> list[dict]:
    return [{'id': t.id, 'channel': t.channel, 'expires_at': t.expires_at, 'revoked_at': t.revoked_at,
             'last_used_at': t.last_used_at, 'use_count': t.use_count, 'created_at': t.created_at}
            for t in db.scalars(select(SupplierAccessToken).where(SupplierAccessToken.invitation_id == invitation_id)
                                .order_by(SupplierAccessToken.created_at.desc())).all()]
