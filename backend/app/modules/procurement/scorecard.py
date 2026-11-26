"""Supplier performance scorecard computed only from recorded transactions.

A metric with no underlying records is reported as null ("no data"), never estimated.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from app.models import (DeliveryNote, DeliveryNoteLine, Invitation, Organization, PurchaseOrder, Quotation, TaxInvoice)
from app.security import as_utc


def _pct(part: int, whole: int) -> str | None:
    if not whole:
        return None
    return str((Decimal(part) * 100 / Decimal(whole)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def scorecards(db, supplier_ids: list[str], *, buyer_org_id: str | None = None) -> list[dict]:
    """Metrics per supplier; optionally restricted to one buyer's transactions."""
    ids = list(dict.fromkeys(supplier_ids))[:200]
    if not ids:
        return []
    inv_q = select(Invitation).where(Invitation.supplier_org_id.in_(ids))
    quote_q = select(Quotation).where(Quotation.supplier_org_id.in_(ids))
    po_q = select(PurchaseOrder).where(PurchaseOrder.supplier_org_id.in_(ids), PurchaseOrder.status != 'draft')
    if buyer_org_id:
        from app.models import RFQ
        buyer_rfqs = select(RFQ.id).where(RFQ.buyer_org_id == buyer_org_id)
        inv_q = inv_q.where(Invitation.rfq_id.in_(buyer_rfqs))
        quote_q = quote_q.where(Quotation.rfq_id.in_(buyer_rfqs))
        po_q = po_q.where(PurchaseOrder.buyer_org_id == buyer_org_id)
    invitations = db.scalars(inv_q).all()
    quotes = {q.invitation_id: q for q in db.scalars(quote_q).all()}
    pos = db.scalars(po_q).all()
    po_ids = [po.id for po in pos]
    notes = db.scalars(select(DeliveryNote).where(DeliveryNote.purchase_order_id.in_(po_ids))).all() if po_ids else []
    note_lines = defaultdict(list)
    if notes:
        for line in db.scalars(select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id.in_([n.id for n in notes]))):
            note_lines[line.delivery_note_id].append(line)
    invoices = db.scalars(select(TaxInvoice).where(TaxInvoice.purchase_order_id.in_(po_ids), TaxInvoice.status != 'draft')).all() if po_ids else []
    names = dict(db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(ids))).all())

    by_supplier = {sid: {'inv': [], 'quotes': [], 'pos': [], 'notes': [], 'invoices': []} for sid in ids}
    for inv in invitations:
        by_supplier[inv.supplier_org_id]['inv'].append(inv)
        quote = quotes.get(inv.id)
        if quote and quote.status != 'draft':
            by_supplier[inv.supplier_org_id]['quotes'].append((inv, quote))
    for po in pos:
        by_supplier[po.supplier_org_id]['pos'].append(po)
    for note in notes:
        by_supplier[note.supplier_org_id]['notes'].append(note)
    for invoice in invoices:
        by_supplier[invoice.supplier_org_id]['invoices'].append(invoice)

    last_receipt = {}
    for note in notes:
        if note.received_at:
            prev = last_receipt.get(note.purchase_order_id)
            last_receipt[note.purchase_order_id] = max(prev, as_utc(note.received_at)) if prev else as_utc(note.received_at)

    output = []
    for sid in ids:
        data = by_supplier[sid]
        responded = data['quotes']
        hours = [(as_utc(q.submitted_at) - as_utc(inv.invited_at)).total_seconds() / 3600 for inv, q in responded if q.submitted_at]
        decided = [q for _, q in responded if q.status in {'awarded', 'rejected'}]
        awarded = [q for _, q in responded if q.status == 'awarded']
        delivered = [po for po in data['pos'] if po.status == 'delivered']
        with_due = [po for po in delivered if po.required_delivery_date]
        on_time = [po for po in with_due if po.id in last_receipt and
                   last_receipt[po.id] <= datetime.combine(po.required_delivery_date, time.max, tzinfo=UTC)]
        received_notes = [n for n in data['notes'] if n.status in {'received', 'received_with_issues'}]
        discrepancies = [n for n in received_notes if n.status == 'received_with_issues']
        damaged = sum((Decimal(l.damaged_quantity or 0) for n in received_notes for l in note_lines[n.id]), Decimal(0))
        short = sum((Decimal(l.delivered_quantity) - Decimal(l.received_quantity) for n in received_notes for l in note_lines[n.id]
                     if l.received_quantity is not None and l.received_quantity < l.delivered_quantity), Decimal(0))
        matched = [i for i in data['invoices'] if i.match_status == 'matched']
        output.append({
            'supplier_org_id': sid,
            'supplier_name': names.get(sid, ''),
            'rfqs_invited': len(data['inv']),
            'quotations_submitted': len(responded),
            'rfq_response_rate': _pct(len(responded), len(data['inv'])),
            'average_response_hours': str(Decimal(sum(hours) / len(hours)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)) if hours else None,
            'quotations_decided': len(decided),
            'quotation_acceptance_rate': _pct(len(awarded), len(decided)),
            'purchase_orders': len(data['pos']),
            'orders_completed': len(delivered),
            'order_completion_rate': _pct(len(delivered), len(data['pos'])),
            'on_time_delivery_rate': _pct(len(on_time), len(with_due)),
            'deliveries_received': len(received_notes),
            'deliveries_with_discrepancies': len(discrepancies),
            'delivery_discrepancy_rate': _pct(len(discrepancies), len(received_notes)),
            'damaged_quantity': str(damaged),
            'short_quantity': str(short),
            'invoices_submitted': len(data['invoices']),
            'invoice_match_rate': _pct(len(matched), len(data['invoices'])),
            'basis': 'Computed from recorded invitations, quotations, purchase orders, goods receipts and invoices. Empty values mean no records yet.',
        })
    return output


def suppliers_for_buyer(db, buyer_org_id: str) -> list[str]:
    from app.models import RFQ
    rows = db.scalars(select(Invitation.supplier_org_id).join(RFQ, RFQ.id == Invitation.rfq_id)
                      .where(RFQ.buyer_org_id == buyer_org_id).group_by(Invitation.supplier_org_id)
                      .order_by(func.count().desc()).limit(200)).all()
    return list(rows)
