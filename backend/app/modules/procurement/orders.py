"""Purchase orders, delivery notes / goods receipts, tax invoices and three-way matching.

Every write is a short transaction on a handful of rows. External notifications
are only queued as outbox events and processed by the background worker.
"""
from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import (DeliveryNote, DeliveryNoteLine, Organization, Outbox, PurchaseOrder, PurchaseOrderLine,
                        Quotation, QuoteLine, RFQ, RFQItem, TaxInvoice, TaxInvoiceLine)
from app.modules.procurement.money import ZERO, Line, d, money, totals
from app.security import audit

OPEN_DELIVERY = {'dispatched', 'received', 'received_with_issues'}
RECEIVED = {'received', 'received_with_issues'}
LIVE_INVOICE = {'submitted', 'accepted'}


def new_reference(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC):%Y%m}-{secrets.token_hex(3).upper()}"


def queue_event(db, event: str, payload: dict, key: str) -> None:
    if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
        db.add(Outbox(event=event, payload=payload, dedupe_key=key))


# --------------------------------------------------------------------------- PO
def prepare_purchase_order(db, quote: Quotation, actor) -> PurchaseOrder:
    """Idempotently prepare a draft PO from an approved (awarded) quotation."""
    existing = db.scalar(select(PurchaseOrder).where(PurchaseOrder.quotation_id == quote.id))
    if existing:
        return existing
    if quote.status != 'awarded':
        raise HTTPException(409, 'Only an approved quotation can become a purchase order')
    rfq = db.get(RFQ, quote.rfq_id)
    lines = db.scalars(select(QuoteLine).where(QuoteLine.quotation_id == quote.id).order_by(QuoteLine.created_at, QuoteLine.id)).all()
    if not lines:
        raise HTTPException(422, 'The approved quotation has no priced lines')
    items = {i.id: i for i in db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id))}
    terms = quote.commercial_terms or {}
    po = PurchaseOrder(
        reference=new_reference('PO'), rfq_id=rfq.id, quotation_id=quote.id, buyer_org_id=rfq.buyer_org_id,
        supplier_org_id=quote.supplier_org_id, status='draft', currency=quote.currency,
        delivery_address=rfq.delivery_address or rfq.city or '', payment_terms=str(terms.get('payment_terms') or ''),
        delivery_terms=str(terms.get('delivery_terms') or ''),
        quotation_reference=str(terms.get('quotation_reference') or f'QT-{quote.id[:8].upper()}'),
        required_delivery_date=rfq.required_delivery_date, prepared_by_user_id=actor.id if actor else None,
        special_instructions=rfq.additional_instructions or '',
    )
    db.add(po)
    db.flush()
    calc = []
    for number, line in enumerate(lines, start=1):
        item = items.get(line.rfq_item_id)
        vat_rate = d(line.vat_rate if line.vat_rate is not None else quote.vat_rate)
        entry = Line(d(line.quantity), d(line.unit_price), d(line.discount_percent), vat_rate)
        calc.append(entry)
        db.add(PurchaseOrderLine(
            purchase_order_id=po.id, line_number=number, rfq_item_id=line.rfq_item_id, quote_line_id=line.id,
            material_id=item.material_id if item else None,
            description=(item.description if item else line.offered_description) + (f' — {line.offered_description}' if item and line.offered_description and line.offered_description != item.description else ''),
            brand=' '.join(filter(None, [line.brand, line.model])), unit=item.unit if item else '',
            quantity=line.quantity, unit_price=line.unit_price, discount_percent=line.discount_percent or ZERO,
            vat_rate=vat_rate, line_total=entry.net,
        ))
    _apply_totals(po, calc, terms)
    audit(db, actor, 'purchase_order.prepared', po.buyer_org_id, po.id, quotation_id=quote.id, reference=po.reference)
    db.flush()
    return po


def _apply_totals(doc, calc: list[Line], terms: dict | None = None) -> None:
    terms = terms or {}
    result = totals(calc, header_discount=terms.get('discount_amount') or '0',
                    delivery_charge=terms.get('delivery_charge') or '0', delivery_vat_rate=terms.get('delivery_vat_rate') or '0')
    doc.subtotal = result['subtotal']
    doc.discount_total = result['discount']
    doc.vat_total = result['vat']
    doc.total = result['total']


def issue_purchase_order(db, po: PurchaseOrder, actor) -> PurchaseOrder:
    if po.status == 'issued':
        return po
    if po.status != 'draft':
        raise HTTPException(409, 'Only a draft purchase order can be issued')
    if not po.delivery_address.strip():
        raise HTTPException(422, 'Enter the delivery address before issuing the purchase order')
    po.status = 'issued'
    po.issued_at = datetime.now(UTC)
    po.issued_by_user_id = actor.id if actor else None
    audit(db, actor, 'purchase_order.issued', po.buyer_org_id, po.id, reference=po.reference)
    queue_event(db, 'purchase_order.issued', {'purchase_order_id': po.id}, f'po-issued:{po.id}')
    return po


def po_line_progress(db, po: PurchaseOrder) -> dict[str, dict[str, Decimal]]:
    """Ordered, dispatched, accepted-received, damaged and invoiced quantity per PO line (one query each)."""
    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id)).all()
    progress = {line.id: {'ordered': d(line.quantity), 'dispatched': ZERO, 'received': ZERO, 'damaged': ZERO, 'invoiced': ZERO} for line in lines}
    rows = db.execute(select(DeliveryNoteLine, DeliveryNote.status).join(DeliveryNote, DeliveryNote.id == DeliveryNoteLine.delivery_note_id)
                      .where(DeliveryNote.purchase_order_id == po.id, DeliveryNote.status.in_(OPEN_DELIVERY))).all()
    for line, status in rows:
        bucket = progress.get(line.po_line_id)
        if not bucket:
            continue
        bucket['dispatched'] += d(line.delivered_quantity)
        if status in RECEIVED:
            bucket['received'] += d(line.received_quantity) - d(line.damaged_quantity)
            bucket['damaged'] += d(line.damaged_quantity)
    for po_line_id, qty in db.execute(select(TaxInvoiceLine.po_line_id, func.sum(TaxInvoiceLine.quantity))
                                      .join(TaxInvoice, TaxInvoice.id == TaxInvoiceLine.invoice_id)
                                      .where(TaxInvoice.purchase_order_id == po.id, TaxInvoice.status.in_(LIVE_INVOICE))
                                      .group_by(TaxInvoiceLine.po_line_id)).all():
        if po_line_id in progress:
            progress[po_line_id]['invoiced'] = d(qty)
    return progress


def refresh_po_delivery_status(db, po: PurchaseOrder) -> None:
    if po.status not in {'issued', 'acknowledged', 'partially_delivered', 'delivered'}:
        return
    progress = po_line_progress(db, po)
    if progress and all(v['received'] >= v['ordered'] for v in progress.values()):
        po.status = 'delivered'
    elif any(v['received'] > 0 or v['dispatched'] > 0 for v in progress.values()):
        po.status = 'partially_delivered'


# ------------------------------------------------------------------- delivery
def create_delivery_note(db, po: PurchaseOrder, payload, actor) -> DeliveryNote:
    if po.status not in {'issued', 'acknowledged', 'partially_delivered'}:
        raise HTTPException(409, 'Deliveries can be recorded only against an issued purchase order')
    po_lines = {line.id: line for line in db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id))}
    progress = po_line_progress(db, po)
    seen = set()
    entries = [line for line in payload.lines if d(line.delivered_quantity) > 0]
    if not entries:
        raise HTTPException(422, 'Enter a delivered quantity for at least one line')
    for line in entries:
        if line.po_line_id not in po_lines or line.po_line_id in seen:
            raise HTTPException(422, 'Invalid or duplicate purchase order line')
        seen.add(line.po_line_id)
        outstanding = progress[line.po_line_id]['ordered'] - progress[line.po_line_id]['dispatched']
        if d(line.delivered_quantity) > outstanding:
            raise HTTPException(422, f'Delivered quantity exceeds the outstanding quantity ({outstanding}) for line {po_lines[line.po_line_id].line_number}')
    note = DeliveryNote(
        reference=new_reference('DN'), purchase_order_id=po.id, supplier_org_id=po.supplier_org_id,
        buyer_org_id=po.buyer_org_id, status='draft', delivery_date=payload.delivery_date or date.today(),
        vehicle_no=payload.vehicle_no, driver_name=payload.driver_name, carrier=payload.carrier,
        delivery_address=po.delivery_address, supplier_remarks=payload.remarks,
    )
    db.add(note)
    db.flush()
    for line in entries:
        db.add(DeliveryNoteLine(delivery_note_id=note.id, po_line_id=line.po_line_id,
                                delivered_quantity=d(line.delivered_quantity), remarks=line.remarks))
    audit(db, actor, 'delivery_note.created', po.supplier_org_id, note.id, purchase_order_id=po.id, reference=note.reference)
    if payload.dispatch:
        dispatch_delivery_note(db, note, po, actor)
    db.flush()
    return note


def dispatch_delivery_note(db, note: DeliveryNote, po: PurchaseOrder, actor) -> None:
    if note.status != 'draft':
        raise HTTPException(409, 'This delivery note was already dispatched')
    note.status = 'dispatched'
    note.dispatched_at = datetime.now(UTC)
    db.flush()
    refresh_po_delivery_status(db, po)
    audit(db, actor, 'delivery_note.dispatched', po.supplier_org_id, note.id, reference=note.reference)
    queue_event(db, 'delivery_note.dispatched', {'delivery_note_id': note.id}, f'dn-dispatched:{note.id}')


def receive_delivery(db, note: DeliveryNote, po: PurchaseOrder, payload, actor) -> DeliveryNote:
    """Buyer goods receipt: received quantities, damage and shortages, line by line."""
    if note.status != 'dispatched':
        raise HTTPException(409, 'Only a dispatched delivery can be received')
    lines = {line.po_line_id: line for line in db.scalars(select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id == note.id))}
    provided = {entry.po_line_id: entry for entry in payload.lines}
    if set(provided) - set(lines):
        raise HTTPException(422, 'Receipt lines must belong to this delivery note')
    issues = False
    for po_line_id, line in lines.items():
        entry = provided.get(po_line_id)
        received = d(entry.received_quantity) if entry else d(line.delivered_quantity)
        damaged = d(entry.damaged_quantity) if entry else ZERO
        if received < 0 or damaged < 0 or damaged > received or received > d(line.delivered_quantity):
            raise HTTPException(422, 'Received quantity cannot exceed the delivered quantity and damage cannot exceed received quantity')
        line.received_quantity = received
        line.damaged_quantity = damaged
        line.receipt_remarks = entry.remarks if entry else ''
        if received < d(line.delivered_quantity) or damaged > 0:
            issues = True
    note.status = 'received_with_issues' if issues else 'received'
    note.received_at = datetime.now(UTC)
    note.received_by_user_id = actor.id if actor else None
    note.received_by_name = payload.received_by_name or (actor.username if actor else '')
    note.receipt_notes = payload.notes
    db.flush()
    refresh_po_delivery_status(db, po)
    audit(db, actor, 'goods_receipt.recorded', po.buyer_org_id, note.id, reference=note.reference, issues=issues)
    for invoice in db.scalars(select(TaxInvoice).where(TaxInvoice.purchase_order_id == po.id, TaxInvoice.status == 'submitted')).all():
        three_way_match(db, invoice)
    queue_event(db, 'goods_receipt.recorded', {'delivery_note_id': note.id}, f'grn:{note.id}')
    return note


# --------------------------------------------------------------------- invoice
def create_invoice(db, po: PurchaseOrder, payload, actor) -> TaxInvoice:
    if po.status not in {'issued', 'acknowledged', 'partially_delivered', 'delivered'}:
        raise HTTPException(409, 'Invoices can be raised only against an issued purchase order')
    if db.scalar(select(TaxInvoice.id).where(TaxInvoice.supplier_org_id == po.supplier_org_id, TaxInvoice.invoice_number == payload.invoice_number)):
        raise HTTPException(409, 'This invoice number already exists for your company')
    if payload.delivery_note_id:
        note = db.get(DeliveryNote, payload.delivery_note_id)
        if not note or note.purchase_order_id != po.id:
            raise HTTPException(422, 'The delivery note does not belong to this purchase order')
    po_lines = {line.id: line for line in db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id))}
    entries = payload.lines
    if not entries:
        # Default: invoice what was dispatched but not yet invoiced, at PO prices.
        progress = po_line_progress(db, po)
        entries = [type('L', (), {'po_line_id': line_id, 'quantity': v['dispatched'] - v['invoiced'], 'unit_price': None,
                                   'discount_percent': None, 'vat_rate': None})()
                   for line_id, v in progress.items() if v['dispatched'] - v['invoiced'] > 0]
    if not entries:
        raise HTTPException(422, 'Nothing to invoice yet: record a delivery or enter invoice lines')
    invoice = TaxInvoice(
        reference=new_reference('INV'), invoice_number=payload.invoice_number, purchase_order_id=po.id,
        delivery_note_id=payload.delivery_note_id, supplier_org_id=po.supplier_org_id, buyer_org_id=po.buyer_org_id,
        status='draft', invoice_date=payload.invoice_date or date.today(), due_date=payload.due_date, currency=po.currency,
        bank_name=payload.bank_name, account_name=payload.account_name, iban=payload.iban, swift=payload.swift,
        payment_terms=payload.payment_terms or po.payment_terms, match_details={},
    )
    db.add(invoice)
    db.flush()
    calc = []
    seen = set()
    for entry in entries:
        po_line = po_lines.get(entry.po_line_id)
        if not po_line or entry.po_line_id in seen:
            raise HTTPException(422, 'Invoice lines must reference this purchase order once each')
        seen.add(entry.po_line_id)
        qty = d(entry.quantity)
        if qty <= 0:
            raise HTTPException(422, 'Invoice quantities must be greater than zero')
        line = Line(qty, d(entry.unit_price if entry.unit_price is not None else po_line.unit_price),
                    d(entry.discount_percent if entry.discount_percent is not None else po_line.discount_percent),
                    d(entry.vat_rate if entry.vat_rate is not None else po_line.vat_rate))
        calc.append(line)
        db.add(TaxInvoiceLine(invoice_id=invoice.id, po_line_id=po_line.id, description=po_line.description, unit=po_line.unit,
                              quantity=qty, unit_price=line.unit_price, discount_percent=line.discount_percent,
                              vat_rate=line.vat_rate, line_total=line.net))
    try:
        _apply_totals(invoice, calc)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, actor, 'tax_invoice.created', po.supplier_org_id, invoice.id, reference=invoice.reference, invoice_number=invoice.invoice_number)
    db.flush()
    if payload.submit:
        submit_invoice(db, invoice, actor)
    return invoice


def submit_invoice(db, invoice: TaxInvoice, actor) -> None:
    if invoice.status != 'draft':
        raise HTTPException(409, 'Only a draft invoice can be submitted')
    invoice.status = 'submitted'
    invoice.submitted_at = datetime.now(UTC)
    db.flush()
    three_way_match(db, invoice)
    audit(db, actor, 'tax_invoice.submitted', invoice.supplier_org_id, invoice.id, match_status=invoice.match_status)
    queue_event(db, 'tax_invoice.submitted', {'invoice_id': invoice.id}, f'invoice-submitted:{invoice.id}')


def three_way_match(db, invoice: TaxInvoice) -> TaxInvoice:
    """Compare PO, goods actually received (net of damage) and the supplier invoice, line by line."""
    po = db.get(PurchaseOrder, invoice.purchase_order_id)
    po_lines = {line.id: line for line in db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id))}
    progress = po_line_progress(db, po)
    # Quantities already billed on OTHER live invoices.
    other = defaultdict(lambda: ZERO)
    for po_line_id, qty in db.execute(select(TaxInvoiceLine.po_line_id, func.sum(TaxInvoiceLine.quantity))
                                      .join(TaxInvoice, TaxInvoice.id == TaxInvoiceLine.invoice_id)
                                      .where(TaxInvoice.purchase_order_id == po.id, TaxInvoice.status.in_(LIVE_INVOICE),
                                             TaxInvoice.id != invoice.id).group_by(TaxInvoiceLine.po_line_id)).all():
        other[po_line_id] = d(qty)
    received_any = any(v['received'] > 0 for v in progress.values()) or bool(db.scalar(select(DeliveryNote.id).where(
        DeliveryNote.purchase_order_id == po.id, DeliveryNote.status.in_(RECEIVED))))
    rows = []
    price_issue = qty_issue = pending = False
    expected_calc = []
    for line in db.scalars(select(TaxInvoiceLine).where(TaxInvoiceLine.invoice_id == invoice.id)).all():
        po_line = po_lines.get(line.po_line_id)
        problems = []
        if not po_line:
            rows.append({'po_line_id': line.po_line_id, 'description': line.description, 'issues': ['Material not on the purchase order']})
            qty_issue = price_issue = True
            continue
        received = progress[po_line.id]['received']
        billable = received - other[po_line.id]
        if d(line.unit_price) != d(po_line.unit_price):
            problems.append(f'Unit price {line.unit_price} differs from PO price {po_line.unit_price}')
            price_issue = True
        if d(line.discount_percent) != d(po_line.discount_percent):
            problems.append(f'Discount {line.discount_percent}% differs from PO discount {po_line.discount_percent}%')
            price_issue = True
        if d(line.vat_rate) != d(po_line.vat_rate):
            problems.append(f'VAT rate {d(line.vat_rate) * 100:.2f}% differs from PO VAT {d(po_line.vat_rate) * 100:.2f}%')
            price_issue = True
        if d(line.quantity) + other[po_line.id] > d(po_line.quantity):
            problems.append(f'Invoiced quantity exceeds ordered quantity {po_line.quantity}')
            qty_issue = True
        if not received_any:
            pending = True
        elif d(line.quantity) > billable:
            problems.append(f'Invoiced quantity {line.quantity} exceeds accepted received quantity {max(billable, ZERO)} (damage excluded)')
            qty_issue = True
        expected_calc.append(Line(d(line.quantity), d(po_line.unit_price), d(po_line.discount_percent), d(po_line.vat_rate)))
        rows.append({
            'po_line_id': po_line.id, 'line_number': po_line.line_number, 'description': po_line.description,
            'ordered_quantity': str(po_line.quantity), 'received_quantity': str(received),
            'damaged_quantity': str(progress[po_line.id]['damaged']), 'previously_invoiced': str(other[po_line.id]),
            'invoiced_quantity': str(line.quantity), 'po_unit_price': str(po_line.unit_price),
            'invoice_unit_price': str(line.unit_price), 'po_vat_rate': str(po_line.vat_rate), 'invoice_vat_rate': str(line.vat_rate),
            'issues': problems,
        })
    expected = totals(expected_calc) if expected_calc else {'subtotal': ZERO, 'discount': ZERO, 'vat': ZERO, 'total': ZERO}
    difference = money(d(invoice.total) - expected['total'])
    if difference != 0 and not price_issue:
        price_issue = True
    if price_issue and qty_issue:
        status = 'requires_review'
    elif price_issue:
        status = 'price_mismatch'
    elif qty_issue:
        status = 'quantity_mismatch'
    elif pending:
        status = 'pending_verification'
    else:
        status = 'matched'
    invoice.match_status = status
    invoice.matched_at = datetime.now(UTC)
    invoice.match_details = {
        'lines': rows,
        'invoice_totals': {'subtotal': str(invoice.subtotal), 'discount': str(invoice.discount_total), 'vat': str(invoice.vat_total), 'total': str(invoice.total)},
        'expected_at_po_terms': {key: str(value) for key, value in expected.items()},
        'difference': str(difference),
        'awaiting_goods_receipt': pending and not (price_issue or qty_issue),
        'note': 'Three-way matching never approves an invoice or starts a payment. The buyer reviews every result.',
    }
    db.flush()
    return invoice


def review_invoice(db, invoice: TaxInvoice, decision: str, notes: str, actor) -> TaxInvoice:
    if invoice.status != 'submitted':
        raise HTTPException(409, 'Only a submitted invoice can be reviewed')
    three_way_match(db, invoice)
    if decision == 'accept' and invoice.match_status != 'matched' and not notes.strip():
        raise HTTPException(422, f'This invoice is {invoice.match_status.replace("_", " ")}. Record a reason to accept it anyway, or dispute it.')
    invoice.status = 'accepted' if decision == 'accept' else 'disputed'
    invoice.reviewed_by_user_id = actor.id if actor else None
    invoice.review_notes = notes
    audit(db, actor, f'tax_invoice.{invoice.status}', invoice.buyer_org_id, invoice.id, match_status=invoice.match_status, notes=notes)
    queue_event(db, 'tax_invoice.reviewed', {'invoice_id': invoice.id, 'status': invoice.status}, f'invoice-reviewed:{invoice.id}:{invoice.status}')
    return invoice


def org_names(db, ids) -> dict[str, str]:
    ids = {i for i in ids if i}
    return dict(db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(ids))).all()) if ids else {}
