"""Authenticated procurement lifecycle API: tracking, weighted comparison, purchase orders,
delivery notes / goods receipts, tax invoices, three-way matching, scorecards,
Document AI, client-format PDFs, magic-link administration and workflow monitoring.

Authorization: buyer actions require membership of the buyer organization
(owner/manager/buyer; issuing a PO requires owner/manager); supplier actions
require membership of the supplier organization (owner/manager/supplier).
Records of other organizations are reported as not found.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (DeliveryNote, DeliveryNoteLine, DocumentExtraction, Invitation, Member, Organization, Outbox,
                        PurchaseOrder, PurchaseOrderLine, Quotation, RFQ, StoredFile, TaxInvoice, TaxInvoiceLine, User)
from app.modules.procurement import documents, orders, scoring, scorecard, sla, tracking
from app.schemas import Model
from app.security import audit, current_user, org_member
from app.serializers import record

router = APIRouter(prefix='/procurement', tags=['Procurement Lifecycle'])

BUYER = {'owner', 'manager', 'buyer'}
BUYER_APPROVER = {'owner', 'manager'}
SUPPLIER = {'owner', 'manager', 'supplier'}


# ------------------------------------------------------------------ helpers
def _member_role(db, user, org_id) -> str | None:
    if user.is_admin:
        return 'admin'
    return db.scalar(select(Member.role).join(Organization, Organization.id == Member.organization_id).where(
        Member.user_id == user.id, Member.organization_id == org_id, Organization.is_active.is_(True)))


def _side(db, user, doc, *, buyer_roles=BUYER, supplier_roles=SUPPLIER) -> str:
    """Return 'buyer', 'supplier' or 'admin' for a document with buyer_org_id/supplier_org_id."""
    if user.is_admin:
        return 'admin'
    if _member_role(db, user, doc.buyer_org_id) in buyer_roles:
        return 'buyer'
    if _member_role(db, user, doc.supplier_org_id) in supplier_roles:
        return 'supplier'
    raise HTTPException(404, 'Not found')


def _require(db, user, org_id, roles):
    org_member(db, user, org_id, roles)


def _pdf(content: bytes, name: str, disposition: str) -> StreamingResponse:
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', name)[:120]
    return StreamingResponse(BytesIO(content), media_type='application/pdf', headers={
        'Content-Disposition': f'{disposition}; filename="{safe}.pdf"', 'Cache-Control': 'private, no-store'})


def _lock(db, model, entity_id):
    row = db.scalar(select(model).where(model.id == entity_id).with_for_update())
    if not row:
        raise HTTPException(404, 'Not found')
    return row


# ------------------------------------------------------ RFQ entry & tracking
class PlainListIn(Model):
    text: str = Field(min_length=2, max_length=50_000)
    ai_enabled: bool = True


LINE_RE = re.compile(r'^\s*(?:\d+[.)\-]\s+)?(?P<body>.+?)\s*$')
QTY_RE = re.compile(r'(?:^|[\s,;x×-])(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>[A-Za-z؀-ۿ.²³/]{1,12})?\s*$')
QTY_LEAD_RE = re.compile(r'^\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>[A-Za-z.]{1,10})?\s*(?:x|×|of)?\s+(?P<body>.+)$', re.I)
BRAND_RE = re.compile(r'\b(?:brand|make|manufacturer)\s*[:=]\s*([^;,\n]+)', re.I)


def parse_plain_line(raw: str) -> dict | None:
    body = LINE_RE.match(raw).group('body') if raw.strip() else ''
    body = body.strip(' \t-•*')
    if len(body) < 2:
        return None
    qty, unit = None, None
    lead = QTY_LEAD_RE.match(body)
    tail = QTY_RE.search(body)
    if tail and tail.start() > 0:
        qty, unit = tail.group('qty'), tail.group('unit')
        body = body[:tail.start()].rstrip(' ,;-x×')
    elif lead:
        qty, unit, body = lead.group('qty'), lead.group('unit'), lead.group('body')
    brand = BRAND_RE.search(body)
    return {'description': body.strip()[:250], 'quantity': (qty or '1').replace(',', '.'), 'unit': (unit or '').strip('.')[:30],
            'brand': brand.group(1).strip() if brand else ''}


@router.post('/rfqs/classify-list')
def classify_plain_list(payload: PlainListIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Split a plain list into lines and classify each: Discipline, Main Category, Subcategory, Material, Brand."""
    from app.models import Category, Material
    from app.modules.rfqs.material_matching import classify_material_description
    from app.rate_limit import enforce_rate_limit
    enforce_rate_limit(request, scope='rfq-classify-list', identity=user.id, limit=20, window_seconds=60)
    lines = [parse_plain_line(raw) for raw in payload.text.splitlines()]
    lines = [line for line in lines if line][:500]
    if not lines:
        raise HTTPException(422, 'Enter at least one material line')
    categories = {c.id: c for c in db.scalars(select(Category).where(Category.active.is_(True)))}
    results = []
    for line in lines:
        result = classify_material_description(db, line['description'], unit=line['unit'] or None,
                                               ai_enabled=payload.ai_enabled and len(lines) <= 100, categories=categories)
        material = db.get(Material, result['material_id']) if result.get('material_id') else None
        main = categories.get(result.get('main_category_id') or '')
        results.append({**line, **result, 'unit': line['unit'] or result.get('suggested_unit') or (material.unit if material else ''),
                        'discipline': (material.discipline if material else None) or (main.code.split('-')[0] if main and main.code else None)})
    return {'lines': results, 'count': len(results),
            'needs_confirmation': sum(1 for r in results if r.get('needs_confirmation'))}


def _buyer_rfq(db, user, rfq_id, roles=BUYER) -> RFQ:
    rfq = db.get(RFQ, rfq_id)
    if not rfq:
        raise HTTPException(404, 'RFQ not found')
    _require(db, user, rfq.buyer_org_id, roles)
    return rfq


@router.get('/rfqs/{rfq_id}/tracking')
def rfq_tracking(rfq_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return tracking.buyer_tracking(db, _buyer_rfq(db, user, rfq_id))


@router.get('/rfqs/{rfq_id}/comparison')
def weighted_comparison(rfq_id: str, price: int | None = Query(None, ge=0, le=100), delivery: int | None = Query(None, ge=0, le=100),
                        warranty: int | None = Query(None, ge=0, le=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rfq = _buyer_rfq(db, user, rfq_id)
    weights = None if price is None and delivery is None and warranty is None else {'price': price or 0, 'delivery': delivery or 0, 'warranty': warranty or 0}
    return scoring.compare(db, rfq, weights)


class WeightsIn(Model):
    price: int = Field(ge=0, le=100)
    delivery: int = Field(ge=0, le=100)
    warranty: int = Field(ge=0, le=100)


@router.put('/rfqs/{rfq_id}/scoring-weights')
def save_weights(rfq_id: str, payload: WeightsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rfq = _buyer_rfq(db, user, rfq_id)
    rfq.scoring_weights = scoring.normalize_weights(payload.model_dump())
    audit(db, user, 'rfq.scoring_weights_updated', rfq.buyer_org_id, rfq.id, weights=rfq.scoring_weights)
    db.commit()
    return scoring.compare(db, rfq)


@router.get('/rfqs/{rfq_id}/document.pdf')
def rfq_pdf(rfq_id: str, lang: Literal['en', 'ar'] = 'en', download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rfq = db.get(RFQ, rfq_id)
    if not rfq:
        raise HTTPException(404, 'RFQ not found')
    supplier = None
    if not user.is_admin and _member_role(db, user, rfq.buyer_org_id) not in BUYER:
        invited = db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq.id)).all()
        own = next((org for org in invited if _member_role(db, user, org) in SUPPLIER), None)
        if not own:
            raise HTTPException(404, 'RFQ not found')
        supplier = db.get(Organization, own)
    return _pdf(documents.render(documents.rfq_spec(db, rfq, supplier), lang=lang), f'RFQ-{rfq.reference}-{lang}', 'attachment' if download else 'inline')


@router.get('/quotations/{quotation_id}/document.pdf')
def quotation_pdf(quotation_id: str, lang: Literal['en', 'ar'] = 'en', download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.modules.quotations.router import get_quote
    get_quote(quotation_id, user, db)  # existing buyer/supplier/admin authorization
    quote = db.get(Quotation, quotation_id)
    spec = documents.quotation_spec(db, quote)
    return _pdf(documents.render(spec, lang=lang), f"Quotation-{spec['reference']}-{lang}", 'attachment' if download else 'inline')


# ---------------------------------------------------------- supplier inbox
@router.get('/supplier/inbox')
def supplier_inbox(org_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, org_id, SUPPLIER)
    return tracking.supplier_inbox(db, org_id, limit=limit, offset=offset)


@router.get('/invitations/{invitation_id}/magic-links')
def magic_links(invitation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = db.get(Invitation, invitation_id)
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    rfq = db.get(RFQ, inv.rfq_id)
    if not user.is_admin and _member_role(db, user, inv.supplier_org_id) not in {'owner', 'manager'} and _member_role(db, user, rfq.buyer_org_id) not in BUYER:
        raise HTTPException(404, 'Invitation not found')
    return tracking.link_status(db, inv.id)


@router.post('/invitations/{invitation_id}/magic-links/revoke')
def revoke_magic_links(invitation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.modules.procurement.portal import revoke_links
    inv = db.get(Invitation, invitation_id)
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    if not user.is_admin and _member_role(db, user, inv.supplier_org_id) not in {'owner', 'manager'}:
        raise HTTPException(404, 'Invitation not found')
    count = revoke_links(db, inv.id)
    audit(db, user, 'magic_links.revoked', inv.supplier_org_id, inv.id, count=count)
    db.commit()
    return {'revoked': count}


@router.post('/invitations/{invitation_id}/resend')
def resend_invitation(invitation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Queue one fresh consolidated notification (new magic link) for an invited supplier."""
    from app.modules.notifications.procurement import context_for
    from app.modules.notifications.recipients import enqueue
    from app.modules.procurement.portal import matched_item_ids
    inv = db.get(Invitation, invitation_id)
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    rfq = _buyer_rfq(db, user, inv.rfq_id)
    if inv.status == 'declined' or rfq.status not in {'published', 'collecting_quotes'}:
        raise HTTPException(409, 'This invitation can no longer be resent')
    bucket = datetime.now(UTC).strftime('%Y%m%d%H')  # at most one resend per supplier per hour
    enqueue(db, org_id=inv.supplier_org_id, template='rfq_invitation',
            context={**context_for(db, rfq), 'org_id': inv.supplier_org_id, 'invitation_id': inv.id,
                     'matched_count': len(matched_item_ids(db, inv))},
            source_key=f'rfq-invitation-resend:{inv.id}:{bucket}')
    audit(db, user, 'rfq.invitation_resent', rfq.buyer_org_id, inv.id)
    db.commit()
    return {'queued': True}


# ----------------------------------------------------------- purchase orders
def _po_detail(db, po: PurchaseOrder, side: str) -> dict:
    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id).order_by(PurchaseOrderLine.line_number)).all()
    progress = orders.po_line_progress(db, po)
    notes = db.scalars(select(DeliveryNote).where(DeliveryNote.purchase_order_id == po.id).order_by(DeliveryNote.created_at)).all()
    invoices = db.scalars(select(TaxInvoice).where(TaxInvoice.purchase_order_id == po.id).order_by(TaxInvoice.created_at)).all()
    names = orders.org_names(db, [po.buyer_org_id, po.supplier_org_id])
    rfq = db.get(RFQ, po.rfq_id)
    return {
        **record(po), 'side': side, 'buyer_name': names.get(po.buyer_org_id), 'supplier_name': names.get(po.supplier_org_id),
        'rfq_reference': rfq.reference if rfq else '',
        'lines': [{**record(line), **{key: str(value) for key, value in progress.get(line.id, {}).items()}} for line in lines],
        'delivery_notes': [record(n) for n in notes if side != 'buyer' or n.status != 'draft'],
        'invoices': [record(i) for i in invoices if side != 'buyer' or i.status != 'draft'],
    }


@router.get('/purchase-orders')
def list_purchase_orders(org_id: str, side: Literal['buyer', 'supplier'] = 'buyer', status: str | None = None,
                         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                         user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, org_id, BUYER if side == 'buyer' else SUPPLIER)
    query = select(PurchaseOrder).where((PurchaseOrder.buyer_org_id if side == 'buyer' else PurchaseOrder.supplier_org_id) == org_id)
    if side == 'supplier':
        query = query.where(PurchaseOrder.status != 'draft')  # suppliers see only issued orders
    if status:
        query = query.where(PurchaseOrder.status == status)
    rows = db.scalars(query.order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id).limit(limit).offset(offset)).all()
    names = orders.org_names(db, [p.buyer_org_id for p in rows] + [p.supplier_org_id for p in rows])
    return [{**record(p), 'buyer_name': names.get(p.buyer_org_id), 'supplier_name': names.get(p.supplier_org_id)} for p in rows]


@router.get('/purchase-orders/{po_id}')
def get_purchase_order(po_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(404, 'Not found')
    side = _side(db, user, po)
    if side == 'supplier' and po.status == 'draft':
        raise HTTPException(404, 'Not found')
    return _po_detail(db, po, side)


class POUpdate(Model):
    delivery_address: str = Field(default='', max_length=300)
    site_contact: str = Field(default='', max_length=200)
    payment_terms: str = Field(default='', max_length=4000)
    delivery_terms: str = Field(default='', max_length=4000)
    special_instructions: str = Field(default='', max_length=4000)
    notes: str = Field(default='', max_length=4000)
    required_delivery_date: date | None = None


@router.put('/purchase-orders/{po_id}')
def update_purchase_order(po_id: str, payload: POUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = _lock(db, PurchaseOrder, po_id)
    _require(db, user, po.buyer_org_id, BUYER)
    if po.status != 'draft':
        raise HTTPException(409, 'Only a draft purchase order can be edited')
    for key, value in payload.model_dump().items():
        setattr(po, key, value)
    audit(db, user, 'purchase_order.updated', po.buyer_org_id, po.id)
    db.commit()
    return _po_detail(db, po, 'buyer')


@router.post('/purchase-orders/{po_id}/issue')
def issue_purchase_order(po_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = _lock(db, PurchaseOrder, po_id)
    _require(db, user, po.buyer_org_id, BUYER_APPROVER)
    orders.issue_purchase_order(db, po, user)
    db.commit()
    return _po_detail(db, po, 'buyer')


@router.post('/purchase-orders/{po_id}/acknowledge')
def acknowledge_purchase_order(po_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = _lock(db, PurchaseOrder, po_id)
    _require(db, user, po.supplier_org_id, SUPPLIER)
    if po.status == 'issued':
        po.status = 'acknowledged'
        po.acknowledged_at = datetime.now(UTC)
        audit(db, user, 'purchase_order.acknowledged', po.supplier_org_id, po.id)
        db.commit()
    elif po.status == 'draft':
        raise HTTPException(404, 'Not found')
    return _po_detail(db, po, 'supplier')


@router.post('/purchase-orders/{po_id}/cancel')
def cancel_purchase_order(po_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = _lock(db, PurchaseOrder, po_id)
    _require(db, user, po.buyer_org_id, BUYER_APPROVER)
    if po.status not in {'draft', 'issued', 'acknowledged'} or db.scalar(select(DeliveryNote.id).where(DeliveryNote.purchase_order_id == po.id, DeliveryNote.status != 'draft')):
        raise HTTPException(409, 'A purchase order with recorded deliveries cannot be cancelled')
    po.status = 'cancelled'
    audit(db, user, 'purchase_order.cancelled', po.buyer_org_id, po.id)
    db.commit()
    return _po_detail(db, po, 'buyer')


@router.get('/purchase-orders/{po_id}/document.pdf')
def purchase_order_pdf(po_id: str, lang: Literal['en', 'ar'] = 'en', download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(404, 'Not found')
    if _side(db, user, po) == 'supplier' and po.status == 'draft':
        raise HTTPException(404, 'Not found')
    return _pdf(documents.render(documents.po_spec(db, po), lang=lang), f'PO-{po.reference}-{lang}', 'attachment' if download else 'inline')


# ---------------------------------------------------------- delivery notes
class DeliveryLineIn(Model):
    po_line_id: str
    delivered_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    remarks: str = Field(default='', max_length=500)


class DeliveryIn(Model):
    delivery_date: date | None = None
    vehicle_no: str = Field(default='', max_length=60)
    driver_name: str = Field(default='', max_length=150)
    carrier: str = Field(default='', max_length=150)
    remarks: str = Field(default='', max_length=4000)
    dispatch: bool = False
    lines: list[DeliveryLineIn] = Field(min_length=1, max_length=500)


class ReceiptLineIn(Model):
    po_line_id: str
    received_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    damaged_quantity: Decimal = Field(default=Decimal('0'), ge=0, max_digits=14, decimal_places=3)
    remarks: str = Field(default='', max_length=500)


class ReceiptIn(Model):
    received_by_name: str = Field(default='', max_length=150)
    notes: str = Field(default='', max_length=4000)
    lines: list[ReceiptLineIn] = Field(default_factory=list, max_length=500)


def _note_detail(db, note: DeliveryNote, side: str) -> dict:
    rows = db.execute(select(DeliveryNoteLine, PurchaseOrderLine).join(PurchaseOrderLine, PurchaseOrderLine.id == DeliveryNoteLine.po_line_id)
                      .where(DeliveryNoteLine.delivery_note_id == note.id).order_by(PurchaseOrderLine.line_number)).all()
    po = db.get(PurchaseOrder, note.purchase_order_id)
    names = orders.org_names(db, [note.buyer_org_id, note.supplier_org_id])
    return {**record(note), 'side': side, 'po_reference': po.reference if po else '', 'buyer_name': names.get(note.buyer_org_id),
            'supplier_name': names.get(note.supplier_org_id),
            'lines': [{**record(line), 'line_number': po_line.line_number, 'description': po_line.description, 'unit': po_line.unit,
                       'ordered_quantity': str(po_line.quantity)} for line, po_line in rows]}


@router.post('/purchase-orders/{po_id}/delivery-notes', status_code=201)
def create_delivery_note(po_id: str, payload: DeliveryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = _lock(db, PurchaseOrder, po_id)
    _require(db, user, po.supplier_org_id, SUPPLIER)
    note = orders.create_delivery_note(db, po, payload, user)
    db.commit()
    return _note_detail(db, note, 'supplier')


@router.post('/delivery-notes/{note_id}/dispatch')
def dispatch_delivery_note(note_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = _lock(db, DeliveryNote, note_id)
    _require(db, user, note.supplier_org_id, SUPPLIER)
    po = _lock(db, PurchaseOrder, note.purchase_order_id)
    orders.dispatch_delivery_note(db, note, po, user)
    db.commit()
    return _note_detail(db, note, 'supplier')


@router.post('/delivery-notes/{note_id}/receive')
def receive_delivery(note_id: str, payload: ReceiptIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po_id = db.scalar(select(DeliveryNote.purchase_order_id).where(DeliveryNote.id == note_id))
    if not po_id:
        raise HTTPException(404, 'Not found')
    po = _lock(db, PurchaseOrder, po_id)  # PO before note: same lock order as invoicing
    note = _lock(db, DeliveryNote, note_id)
    _require(db, user, note.buyer_org_id, BUYER)
    orders.receive_delivery(db, note, po, payload, user)
    db.commit()
    return _note_detail(db, note, 'buyer')


@router.get('/delivery-notes')
def list_delivery_notes(org_id: str, side: Literal['buyer', 'supplier'] = 'buyer', status: str | None = None,
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, org_id, BUYER if side == 'buyer' else SUPPLIER)
    query = select(DeliveryNote, PurchaseOrder.reference).join(PurchaseOrder, PurchaseOrder.id == DeliveryNote.purchase_order_id).where(
        (DeliveryNote.buyer_org_id if side == 'buyer' else DeliveryNote.supplier_org_id) == org_id)
    if side == 'buyer':
        query = query.where(DeliveryNote.status != 'draft')
    if status:
        query = query.where(DeliveryNote.status == status)
    rows = db.execute(query.order_by(DeliveryNote.created_at.desc(), DeliveryNote.id).limit(limit).offset(offset)).all()
    names = orders.org_names(db, [n.buyer_org_id for n, _ in rows] + [n.supplier_org_id for n, _ in rows])
    return [{**record(n), 'po_reference': ref, 'buyer_name': names.get(n.buyer_org_id), 'supplier_name': names.get(n.supplier_org_id)} for n, ref in rows]


@router.get('/delivery-notes/{note_id}')
def get_delivery_note(note_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = db.get(DeliveryNote, note_id)
    if not note:
        raise HTTPException(404, 'Not found')
    side = _side(db, user, note)
    if side == 'buyer' and note.status == 'draft':
        raise HTTPException(404, 'Not found')
    return _note_detail(db, note, side)


@router.get('/delivery-notes/{note_id}/document.pdf')
def delivery_note_pdf(note_id: str, lang: Literal['en', 'ar'] = 'en', download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = db.get(DeliveryNote, note_id)
    if not note:
        raise HTTPException(404, 'Not found')
    _side(db, user, note)
    return _pdf(documents.render(documents.delivery_spec(db, note), lang=lang), f'DN-{note.reference}-{lang}', 'attachment' if download else 'inline')


# ---------------------------------------------------------------- invoices
class InvoiceLineIn(Model):
    po_line_id: str
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    discount_percent: Decimal | None = Field(default=None, ge=0, le=100)
    vat_rate: Decimal | None = Field(default=None, ge=0, le=1)


class InvoiceIn(Model):
    invoice_number: str = Field(min_length=1, max_length=60)
    invoice_date: date | None = None
    due_date: date | None = None
    delivery_note_id: str | None = None
    bank_name: str = Field(default='', max_length=150)
    account_name: str = Field(default='', max_length=150)
    iban: str = Field(default='', max_length=40)
    swift: str = Field(default='', max_length=20)
    payment_terms: str = Field(default='', max_length=4000)
    submit: bool = False
    lines: list[InvoiceLineIn] = Field(default_factory=list, max_length=500)


class InvoiceReview(Model):
    decision: Literal['accept', 'dispute']
    notes: str = Field(default='', max_length=4000)


def _invoice_detail(db, invoice: TaxInvoice, side: str) -> dict:
    po = db.get(PurchaseOrder, invoice.purchase_order_id)
    names = orders.org_names(db, [invoice.buyer_org_id, invoice.supplier_org_id])
    lines = db.scalars(select(TaxInvoiceLine).where(TaxInvoiceLine.invoice_id == invoice.id).order_by(TaxInvoiceLine.created_at, TaxInvoiceLine.id)).all()
    return {**record(invoice), 'side': side, 'po_reference': po.reference if po else '', 'buyer_name': names.get(invoice.buyer_org_id),
            'supplier_name': names.get(invoice.supplier_org_id), 'lines': [record(line) for line in lines]}


@router.post('/purchase-orders/{po_id}/invoices', status_code=201)
def create_invoice(po_id: str, payload: InvoiceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po = _lock(db, PurchaseOrder, po_id)
    _require(db, user, po.supplier_org_id, SUPPLIER)
    invoice = orders.create_invoice(db, po, payload, user)
    db.commit()
    return _invoice_detail(db, invoice, 'supplier')


@router.post('/invoices/{invoice_id}/submit')
def submit_invoice(invoice_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po_id = db.scalar(select(TaxInvoice.purchase_order_id).where(TaxInvoice.id == invoice_id))
    if not po_id:
        raise HTTPException(404, 'Not found')
    _lock(db, PurchaseOrder, po_id)
    invoice = _lock(db, TaxInvoice, invoice_id)
    _require(db, user, invoice.supplier_org_id, SUPPLIER)
    orders.submit_invoice(db, invoice, user)
    db.commit()
    return _invoice_detail(db, invoice, 'supplier')


@router.post('/invoices/{invoice_id}/review')
def review_invoice(invoice_id: str, payload: InvoiceReview, user: User = Depends(current_user), db: Session = Depends(get_db)):
    po_id = db.scalar(select(TaxInvoice.purchase_order_id).where(TaxInvoice.id == invoice_id))
    if not po_id:
        raise HTTPException(404, 'Not found')
    _lock(db, PurchaseOrder, po_id)
    invoice = _lock(db, TaxInvoice, invoice_id)
    _require(db, user, invoice.buyer_org_id, BUYER_APPROVER if payload.decision == 'accept' else BUYER)
    orders.review_invoice(db, invoice, payload.decision, payload.notes, user)
    db.commit()
    return _invoice_detail(db, invoice, 'buyer')


@router.post('/invoices/{invoice_id}/rematch')
def rematch_invoice(invoice_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    invoice = db.get(TaxInvoice, invoice_id)
    if not invoice:
        raise HTTPException(404, 'Not found')
    side = _side(db, user, invoice)
    if invoice.status == 'draft' and side == 'buyer':
        raise HTTPException(404, 'Not found')
    orders.three_way_match(db, invoice)
    db.commit()
    return _invoice_detail(db, invoice, side)


@router.get('/invoices')
def list_invoices(org_id: str, side: Literal['buyer', 'supplier'] = 'buyer', match_status: str | None = None, status: str | None = None,
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, org_id, BUYER if side == 'buyer' else SUPPLIER)
    query = select(TaxInvoice, PurchaseOrder.reference).join(PurchaseOrder, PurchaseOrder.id == TaxInvoice.purchase_order_id).where(
        (TaxInvoice.buyer_org_id if side == 'buyer' else TaxInvoice.supplier_org_id) == org_id)
    if side == 'buyer':
        query = query.where(TaxInvoice.status != 'draft')
    if match_status:
        query = query.where(TaxInvoice.match_status == match_status)
    if status:
        query = query.where(TaxInvoice.status == status)
    rows = db.execute(query.order_by(TaxInvoice.created_at.desc(), TaxInvoice.id).limit(limit).offset(offset)).all()
    names = orders.org_names(db, [i.buyer_org_id for i, _ in rows] + [i.supplier_org_id for i, _ in rows])
    return [{**record(i), 'po_reference': ref, 'buyer_name': names.get(i.buyer_org_id), 'supplier_name': names.get(i.supplier_org_id)} for i, ref in rows]


@router.get('/invoices/{invoice_id}')
def get_invoice(invoice_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    invoice = db.get(TaxInvoice, invoice_id)
    if not invoice:
        raise HTTPException(404, 'Not found')
    side = _side(db, user, invoice)
    if side == 'buyer' and invoice.status == 'draft':
        raise HTTPException(404, 'Not found')
    return _invoice_detail(db, invoice, side)


@router.get('/invoices/{invoice_id}/document.pdf')
def invoice_pdf(invoice_id: str, lang: Literal['en', 'ar'] = 'en', download: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    invoice = db.get(TaxInvoice, invoice_id)
    if not invoice:
        raise HTTPException(404, 'Not found')
    if _side(db, user, invoice) == 'buyer' and invoice.status == 'draft':
        raise HTTPException(404, 'Not found')
    return _pdf(documents.render(documents.invoice_spec(db, invoice), lang=lang), f'Invoice-{invoice.invoice_number}-{lang}', 'attachment' if download else 'inline')


@router.get('/three-way-matching')
def three_way_overview(org_id: str, side: Literal['buyer', 'supplier'] = 'buyer', user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, org_id, BUYER if side == 'buyer' else SUPPLIER)
    column = TaxInvoice.buyer_org_id if side == 'buyer' else TaxInvoice.supplier_org_id
    counts = dict(db.execute(select(TaxInvoice.match_status, func.count()).where(column == org_id, TaxInvoice.status != 'draft')
                             .group_by(TaxInvoice.match_status)).all())
    return {'counts': counts, 'invoices': list_invoices(org_id, side, None, None, 100, 0, user, db)}


# -------------------------------------------------------- document sharing
class ShareIn(Model):
    kind: Literal['rfq', 'quotation', 'purchase_order', 'delivery_note', 'invoice']
    id: str
    lang: Literal['en', 'ar'] = 'en'
    recipient: Literal['counterparty', 'me'] = 'counterparty'


@router.post('/documents/share')
def share_document(payload: ShareIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Email a client-format PDF to the counterparty's business email or to the requesting user."""
    from app.rate_limit import enforce_rate_limit
    enforce_rate_limit(request, scope='document-share', identity=user.id, limit=20, window_seconds=3600)
    model = {'purchase_order': PurchaseOrder, 'delivery_note': DeliveryNote, 'invoice': TaxInvoice, 'quotation': Quotation, 'rfq': RFQ}[payload.kind]
    doc = db.get(model, payload.id)
    if not doc:
        raise HTTPException(404, 'Not found')
    if payload.kind == 'rfq':
        _require(db, user, doc.buyer_org_id, BUYER)
        counterparty = None
    elif payload.kind == 'quotation':
        rfq = db.get(RFQ, doc.rfq_id)
        side = 'buyer' if _member_role(db, user, rfq.buyer_org_id) in BUYER else 'supplier' if _member_role(db, user, doc.supplier_org_id) in SUPPLIER else None
        if not side or (side == 'buyer' and doc.status == 'draft'):
            raise HTTPException(404, 'Not found')
        counterparty = doc.supplier_org_id if side == 'buyer' else rfq.buyer_org_id
    else:
        side = _side(db, user, doc)
        counterparty = doc.supplier_org_id if side == 'buyer' else doc.buyer_org_id
    if payload.recipient == 'me':
        if not user.email or not user.email_verified:
            raise HTTPException(422, 'Verify your email address first')
        address = user.email
    else:
        org = db.get(Organization, counterparty) if counterparty else None
        if not org or not org.business_email:
            raise HTTPException(422, 'The other company has no business email on file')
        address = org.business_email
    key = f'document-share:{payload.kind}:{payload.id}:{payload.lang}:{address.casefold()}:{datetime.now(UTC):%Y%m%d%H}'
    orders.queue_event(db, 'document.share', {'kind': payload.kind, 'id': payload.id, 'lang': payload.lang, 'address': address, 'user_id': user.id}, key)
    audit(db, user, 'document.shared', None, payload.id, kind=payload.kind, recipient=payload.recipient)
    db.commit()
    return {'queued': True, 'recipient': payload.recipient}


# --------------------------------------------------------------- scorecards
@router.get('/scorecards')
def list_scorecards(org_id: str, side: Literal['buyer', 'supplier'] = 'buyer', user: User = Depends(current_user), db: Session = Depends(get_db)):
    if side == 'supplier':
        _require(db, user, org_id, SUPPLIER)
        return scorecard.scorecards(db, [org_id])
    _require(db, user, org_id, BUYER)
    return scorecard.scorecards(db, scorecard.suppliers_for_buyer(db, org_id), buyer_org_id=org_id)


# --------------------------------------------------------------- Document AI
class ExtractedLineIn(Model):
    description: str = Field(min_length=1, max_length=4000)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit: str = Field(default='', max_length=30)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    discount_percent: Decimal = Field(default=Decimal('0'), ge=0, le=100)
    rfq_item_id: str | None = None


class ExtractionConfirm(Model):
    supplier_name: str = Field(default='', max_length=220)
    supplier_org_id: str | None = None
    rfq_id: str | None = None
    quotation_reference: str = Field(default='', max_length=100)
    currency: Literal['SAR', 'USD', 'EUR', 'AED', 'GBP'] = 'SAR'
    vat_rate: Decimal = Field(default=Decimal('0.15'), ge=0, le=1)
    header_discount: Decimal = Field(default=Decimal('0'), ge=0, max_digits=14, decimal_places=2)
    payment_terms: str = Field(default='', max_length=4000)
    delivery_terms: str = Field(default='', max_length=4000)
    warranty: str = Field(default='', max_length=2000)
    warranty_months: int | None = Field(default=None, ge=0, le=600)
    lead_days: int | None = Field(default=None, ge=0, le=3650)
    valid_until: datetime | None = None
    lines: list[ExtractedLineIn] = Field(min_length=1, max_length=500)


def _extraction(db, user, extraction_id) -> DocumentExtraction:
    job = db.get(DocumentExtraction, extraction_id)
    if not job:
        raise HTTPException(404, 'Not found')
    _require(db, user, job.org_id, BUYER | SUPPLIER)
    return job


@router.post('/document-ai/extractions', status_code=201)
def upload_for_extraction(request: Request, org_id: str = Form(...), rfq_id: str | None = Form(None), file: UploadFile = File(...),
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    import io
    from hashlib import sha256
    from pathlib import Path
    from uuid import uuid4

    from app.rate_limit import enforce_rate_limit
    from app.storage import (StorageUnavailable, StorageValidationError, build_object_key, safe_filename, upload_file,
                             validate_file_content)
    _require(db, user, org_id, BUYER | SUPPLIER)
    if rfq_id:
        rfq = db.get(RFQ, rfq_id)
        if not rfq or rfq.buyer_org_id != org_id:
            raise HTTPException(404, 'RFQ not found')
    enforce_rate_limit(request, scope='document-ai', identity=user.id, limit=20, window_seconds=3600)
    settings = get_settings()
    max_bytes = min(settings.max_upload_bytes, 15 * 1024 * 1024)
    content = file.file.read(max_bytes + 1)
    allowed = {'.pdf': ('application/pdf',), '.png': ('image/png',), '.jpg': ('image/jpeg',), '.jpeg': ('image/jpeg',)}
    try:
        suffix, content_type = validate_file_content(file.filename, file.content_type, content, allowed, max_bytes)
    except StorageValidationError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    name = safe_filename(file.filename, suffix)
    db.rollback()  # no transaction is held during storage I/O
    stored = StoredFile(bucket_type='private', object_key='', original_filename=name, safe_filename=name, content_type=content_type,
                        file_extension=suffix, size_bytes=len(content), visibility='private', purpose='document_ai', status='available',
                        owner_user_id=user.id, uploaded_by_user_id=user.id, organization_id=org_id, entity_type='document_ai',
                        entity_id=str(uuid4()), checksum_sha256=sha256(content).hexdigest())
    if settings.r2_enabled:
        key = build_object_key('private/document-ai', org_id, suffix)
        try:
            upload_file('private', key, io.BytesIO(content), content_type, max_bytes=max_bytes)
        except StorageUnavailable as exc:
            raise HTTPException(503, 'File storage is temporarily unavailable') from exc
        stored.object_key = key
    else:
        if settings.is_production:
            raise HTTPException(503, 'Private document storage is not configured')
        root = (Path(settings.upload_dir).resolve() / 'document-ai').resolve()
        relative = f'{org_id}/{uuid4().hex}{suffix}'
        target = (root / relative).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        stored.object_key = 'local:' + relative
    db.add(stored)
    db.flush()
    job = DocumentExtraction(org_id=org_id, rfq_id=rfq_id, uploaded_by_user_id=user.id, file_id=stored.id, status='queued', extracted={})
    db.add(job)
    db.flush()
    stored.entity_id = job.id
    db.add(Outbox(event='document_ai.extract', payload={'extraction_id': job.id}, dedupe_key=f'document-ai:{job.id}'))
    audit(db, user, 'document_ai.uploaded', org_id, job.id, filename=name)
    db.commit()
    return {**record(job), 'filename': name}


@router.get('/document-ai/extractions')
def list_extractions(org_id: str, limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, org_id, BUYER | SUPPLIER)
    rows = db.execute(select(DocumentExtraction, StoredFile.safe_filename).join(StoredFile, StoredFile.id == DocumentExtraction.file_id)
                      .where(DocumentExtraction.org_id == org_id).order_by(DocumentExtraction.created_at.desc()).limit(limit)).all()
    return [{**record(job), 'filename': name} for job, name in rows]


@router.get('/document-ai/extractions/{extraction_id}')
def get_extraction(extraction_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _extraction(db, user, extraction_id)
    return {**record(job), 'filename': db.get(StoredFile, job.file_id).safe_filename}


@router.post('/document-ai/extractions/{extraction_id}/confirm')
def confirm_extraction(extraction_id: str, payload: ExtractionConfirm, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.modules.procurement.docai import confirm
    job = db.scalar(select(DocumentExtraction).where(DocumentExtraction.id == extraction_id).with_for_update())
    if not job:
        raise HTTPException(404, 'Not found')
    _require(db, user, job.org_id, BUYER)
    if payload.rfq_id:
        rfq = db.get(RFQ, payload.rfq_id)
        if not rfq or rfq.buyer_org_id != job.org_id:
            raise HTTPException(404, 'RFQ not found')
        if payload.supplier_org_id:
            supplier = db.get(Organization, payload.supplier_org_id)
            if not supplier or not supplier.is_supplier:
                raise HTTPException(422, 'Select a supplier company')
    confirm(db, job, payload, user)
    db.commit()
    return record(job)


# ------------------------------------------------------------------ admin
def _admin(user):
    if not user.is_admin:
        raise HTTPException(403, 'Administrator access required')


@router.get('/admin/workflow')
def workflow_monitor(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.modules.procurement.whatsapp import configuration_status
    _admin(user)
    now = datetime.now(UTC)
    outbox = db.execute(select(Outbox.event, Outbox.status, func.count()).where(Outbox.created_at > now - timedelta(days=30))
                        .group_by(Outbox.event, Outbox.status)).all()
    failed = db.scalars(select(Outbox).where(Outbox.status.in_({'failed', 'uncertain'})).order_by(Outbox.created_at.desc()).limit(50)).all()
    stuck = db.scalars(select(Outbox).where(Outbox.status == 'processing', Outbox.lock_until < now).limit(50)).all()
    has_invitation = select(Invitation.id).where(Invitation.rfq_id == RFQ.id).exists()
    stuck_rfqs = db.scalars(select(RFQ).where(RFQ.status == 'published', RFQ.published_at < now - timedelta(minutes=10),
                                              RFQ.response_deadline > now, ~has_invitation).limit(50)).all()
    delivery_states = db.execute(select(Outbox.payload['channel'].as_string(), Outbox.status, func.count()).where(
        Outbox.event == 'notification.delivery', Outbox.created_at > now - timedelta(days=30)).group_by(
        Outbox.payload['channel'].as_string(), Outbox.status)).all()
    docs = dict(db.execute(select(DocumentExtraction.status, func.count()).group_by(DocumentExtraction.status)).all())
    pipeline = {
        'rfqs_open': db.scalar(select(func.count()).select_from(RFQ).where(RFQ.status.in_({'published', 'collecting_quotes'}))) or 0,
        'quotations_draft': db.scalar(select(func.count()).select_from(Quotation).where(Quotation.status == 'draft')) or 0,
        'quotations_submitted': db.scalar(select(func.count()).select_from(Quotation).where(Quotation.status.in_({'submitted', 'shortlisted'}))) or 0,
        'purchase_orders': dict(db.execute(select(PurchaseOrder.status, func.count()).group_by(PurchaseOrder.status)).all()),
        'invoices_by_match': dict(db.execute(select(TaxInvoice.match_status, func.count()).where(TaxInvoice.status != 'draft').group_by(TaxInvoice.match_status)).all()),
    }
    return {
        'outbox': [{'event': e, 'status': s, 'count': c} for e, s, c in outbox],
        'deliveries': [{'channel': ch or 'internal', 'status': s, 'count': c} for ch, s, c in delivery_states],
        'failed_jobs': [{'id': o.id, 'event': o.event, 'status': o.status, 'attempts': o.attempts, 'last_error': o.last_error,
                         'delivery_status': o.delivery_status, 'channel': (o.payload or {}).get('channel'), 'created_at': o.created_at} for o in failed],
        'stalled_jobs': [{'id': o.id, 'event': o.event, 'lock_until': o.lock_until} for o in stuck],
        'stuck_rfqs': [{'id': r.id, 'reference': r.reference, 'published_at': r.published_at} for r in stuck_rfqs],
        'document_ai': docs,
        'pipeline': pipeline,
        'whatsapp': configuration_status(),
        'sla': sla.get_config(db),
        'mock_notifications': get_settings().mock_notifications,
    }


@router.post('/admin/outbox/{event_id}/retry')
def retry_job(event_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    event = _lock(db, Outbox, event_id)
    if event.status not in {'failed', 'processing'}:
        raise HTTPException(409, 'Only failed or stalled jobs can be retried. Uncertain deliveries must be reconciled first.')
    if event.event == 'notification.delivery' and event.provider_message_id and event.delivery_status not in {None, 'submitting'}:
        raise HTTPException(409, 'The provider accepted this message; retrying could send a duplicate')
    event.status, event.attempts, event.next_attempt_at, event.lock_until, event.last_error = 'retry', 0, None, None, None
    audit(db, user, 'outbox.retried', None, event.id, event_name=event.event)
    db.commit()
    return {'status': event.status}


@router.post('/admin/rfqs/{rfq_id}/recover')
def recover_stuck_rfq(rfq_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Safely requeue matching for a published RFQ (existing scripts.requeue_rfq_matching logic; no deletes)."""
    import sys
    from pathlib import Path
    _admin(user)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scripts.requeue_rfq_matching import recover_rfq
    ok = recover_rfq(db, rfq_id)
    audit(db, user, 'rfq.matching_recovered', None, rfq_id, queued=ok)
    db.commit()
    return {'queued': ok}


@router.get('/admin/sla')
def get_sla(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    return {'config': sla.get_config(db), 'defaults': sla.DEFAULT_SLA}


@router.put('/admin/sla')
def put_sla(payload: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    try:
        config = sla.save_config(db, payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, 'sla.updated', None, 'default', config=config)
    db.commit()
    return {'config': config, 'defaults': sla.DEFAULT_SLA}
