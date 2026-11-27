"""Public website document builders (RFQ, Quotation, PO, Invoice) and the WhatsApp status webhook.

Builder input is rendered straight to a PDF in the client's format and is never
stored, so public builder data stays separate from private procurement records.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Outbox
from app.modules.procurement import documents
from app.modules.procurement.money import Line, amount_in_words, totals
from app.rate_limit import enforce_rate_limit
from app.schemas import Model

router = APIRouter(tags=['Public Document Builders'])


class Party(Model):
    name: str = Field(default='', max_length=220)
    address: str = Field(default='', max_length=400)
    vat_number: str = Field(default='', max_length=80)
    cr_number: str = Field(default='', max_length=80)
    contact: str = Field(default='', max_length=300)


class BuilderLine(Model):
    description: str = Field(min_length=1, max_length=1000)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit: str = Field(default='', max_length=30)
    unit_price: Decimal = Field(default=Decimal('0'), ge=0, max_digits=14, decimal_places=2)
    discount_percent: Decimal = Field(default=Decimal('0'), ge=0, le=100)
    vat_percent: Decimal = Field(default=Decimal('15'), ge=0, le=100)
    remarks: str = Field(default='', max_length=300)


class BuilderIn(Model):
    language: Literal['en', 'ar'] = 'en'
    sender: Party = Field(default_factory=Party)
    recipient: Party = Field(default_factory=Party)
    ship_to: str = Field(default='', max_length=400)
    number: str = Field(default='', max_length=60)
    document_date: date | None = None
    due_date: date | None = None
    reference: str = Field(default='', max_length=120)
    project: str = Field(default='', max_length=250)
    delivery_location: str = Field(default='', max_length=300)
    currency: Literal['SAR', 'USD', 'EUR', 'AED', 'GBP'] = 'SAR'
    payment_terms: str = Field(default='', max_length=1000)
    delivery_terms: str = Field(default='', max_length=1000)
    lead_time: str = Field(default='', max_length=300)
    warranty: str = Field(default='', max_length=300)
    stock_availability: str = Field(default='', max_length=300)
    notes: str = Field(default='', max_length=2000)
    bank_name: str = Field(default='', max_length=150)
    account_name: str = Field(default='', max_length=150)
    iban: str = Field(default='', max_length=40)
    swift: str = Field(default='', max_length=20)
    lines: list[BuilderLine] = Field(min_length=1, max_length=300)


def _lines(lines: list[BuilderLine]):
    calc = [Line(l.quantity, l.unit_price, l.discount_percent, l.vat_percent / 100) for l in lines]
    return calc, totals(calc)


def _party(p: Party) -> list[str]:
    return [p.name, p.address, f'VAT: {p.vat_number}' if p.vat_number else '', f'CR: {p.cr_number}' if p.cr_number else '', p.contact]


def build_spec(kind: str, data: BuilderIn) -> dict:
    n = documents._num
    q = documents._qty
    today = (data.document_date or date.today()).isoformat()
    calc, result = _lines(data.lines)
    if kind == 'rfq':
        return {'title': 'REQUEST FOR QUOTATION', 'reference': data.number, 'logo_text': data.sender.name,
                'parties': [('FROM (BUYER)', _party(data.sender)), ('TO (SUPPLIER)', _party(data.recipient))],
                'info': [('RFQ NO.', data.number), ('RFQ DATE', today), ('RESPONSE DEADLINE', data.due_date.isoformat() if data.due_date else ''),
                         ('PROJECT / REFERENCE', data.project or data.reference), ('DELIVERY LOCATION', data.delivery_location), ('REQUIRED DELIVERY DATE', '')],
                'columns': [('no', 'No', 6, 'left'), ('description', 'Description / specification', 64, 'left'), ('qty', 'Qty', 14, 'right'), ('uom', 'UoM', 16, 'left')],
                'rows': [{'no': i, 'description': l.description, 'qty': q(l.quantity), 'uom': l.unit} for i, l in enumerate(data.lines, 1)],
                'boxes': [('INSTRUCTIONS TO SUPPLIER', [('Quote validity', data.notes or '30 days'), ('Currency', data.currency), ('Payment terms', data.payment_terms),
                                                        ('Delivery terms (Incoterms)', data.delivery_terms), ('Prices include VAT (Yes / No)', 'No'), ('Attachments', '')])],
                'signatures': ['Prepared by', 'Authorized by'],
                'footer': 'Please quote each line separately and return your quotation before the response deadline.'}
    price_rows = [{'no': i, 'description': l.description, 'qty': q(l.quantity), 'uom': l.unit, 'price': n(l.unit_price),
                   'disc': n(l.discount_percent).rstrip('0').rstrip('.') or '0', 'vat': f'{n(l.vat_percent, 0)}%', 'total': n(c.net)}
                  for i, (l, c) in enumerate(zip(data.lines, calc), 1)]
    if kind == 'quotation':
        return {'title': 'QUOTATION', 'reference': data.number, 'logo_text': data.sender.name,
                'parties': [('FROM (SUPPLIER)', _party(data.sender)), ('TO (CUSTOMER)', _party(data.recipient))],
                'info': [('QUOTATION NO.', data.number), ('QUOTATION DATE', today), ('VALID UNTIL', data.due_date.isoformat() if data.due_date else ''),
                         ('RFQ REFERENCE', data.reference), ('PREPARED BY', data.sender.contact), ('CURRENCY', data.currency)],
                'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 37, 'left'), ('qty', 'Qty', 9, 'right'), ('uom', 'UoM', 8, 'left'),
                            ('price', 'Unit price', 14, 'right'), ('disc', 'Disc. %', 9, 'right'), ('total', 'Line total', 18, 'right')],
                'rows': price_rows,
                'totals': [('Subtotal', n(result['subtotal']), False), ('Discount', n(result['discount']), False), ('VAT', n(result['vat']), False),
                           ('Total', f"{n(result['total'])} {data.currency}", True)],
                'boxes': [('TERMS AND CONDITIONS', [('Payment terms', data.payment_terms), ('Delivery lead time', data.lead_time), ('Warranty', data.warranty),
                                                    ('Delivery terms (Incoterms)', data.delivery_terms), ('Stock availability', data.stock_availability)])],
                'signatures': ['Authorized signature and stamp', 'Customer acceptance (optional)'],
                'footer': 'This quotation is valid until the date shown above. Prices are in the stated currency.'}
    if kind == 'purchase_order':
        return {'title': 'PURCHASE ORDER', 'reference': data.number, 'logo_text': data.sender.name,
                'parties': [('VENDOR', _party(data.recipient)), ('BILL TO', _party(data.sender)), ('SHIP TO', [data.ship_to or data.delivery_location])],
                'info': [('PO NO.', data.number), ('PO DATE', today), ('QUOTATION REFERENCE', data.reference), ('PAYMENT TERMS', data.payment_terms),
                         ('REQUIRED DELIVERY DATE', data.due_date.isoformat() if data.due_date else ''), ('CURRENCY', data.currency)],
                'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 41, 'left'), ('qty', 'Qty', 9, 'right'), ('uom', 'UoM', 8, 'left'),
                            ('price', 'Unit price', 14, 'right'), ('vat', 'VAT', 7, 'right'), ('total', 'Line total', 16, 'right')],
                'rows': price_rows,
                'totals': [('Subtotal', n(result['subtotal']), False)] + ([('Discount', n(result['discount']), False)] if result['discount'] else []) +
                          [('VAT', n(result['vat']), False), ('Total', f"{n(result['total'])} {data.currency}", True)],
                'boxes': [('ORDER TERMS', [('Delivery terms (Incoterms)', data.delivery_terms), ('Special instructions', data.notes), ('Notes', '')])],
                'signatures': ['Prepared by', 'Approved by', 'Vendor acceptance (signature, date)'],
                'footer': 'Please quote the PO number on every delivery note and invoice.'}
    if kind == 'invoice':
        return {'title': 'TAX INVOICE', 'reference': data.number, 'logo_text': data.sender.name,
                'parties': [('SUPPLIER', _party(data.sender)), ('CUSTOMER', _party(data.recipient))],
                'info': [('INVOICE NO.', data.number), ('INVOICE DATE', today), ('DUE DATE', data.due_date.isoformat() if data.due_date else ''),
                         ('PO NO.', data.reference), ('DELIVERY NOTE NO.', data.project), ('CURRENCY', data.currency)],
                'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 41, 'left'), ('qty', 'Qty', 9, 'right'), ('uom', 'UoM', 8, 'left'),
                            ('price', 'Unit price', 14, 'right'), ('vat', 'VAT', 7, 'right'), ('total', 'Line total', 16, 'right')],
                'rows': price_rows,
                'totals': [('Subtotal', n(result['subtotal']), False), ('Discount', n(result['discount']), False), ('VAT', n(result['vat']), False),
                           ('Total due', f"{n(result['total'])} {data.currency}", True)],
                'qr_note': 'QR code, if e-invoicing applies',
                'boxes': [('PAYMENT DETAILS', [('Bank name', data.bank_name), ('Account name', data.account_name), ('IBAN', data.iban), ('SWIFT / BIC', data.swift),
                                               ('Amount in words', amount_in_words(result['total'], data.currency)), ('Payment terms', data.payment_terms)])],
                'signatures': ['Authorized signature and stamp'],
                'footer': 'Please quote the invoice number with your payment.'}
    if kind == 'delivery_note':
        return {'title': 'DELIVERY NOTE', 'reference': data.number, 'logo_text': data.sender.name,
                'parties': [('FROM (SUPPLIER)', _party(data.sender)), ('DELIVER TO', _party(data.recipient) + [data.ship_to])],
                'info': [('DELIVERY NOTE NO.', data.number), ('DELIVERY DATE', today), ('PO NO.', data.reference), ('VEHICLE NO.', ''), ('DRIVER NAME', ''), ('CARRIER', '')],
                'columns': [('no', 'No', 5, 'left'), ('description', 'Description', 39, 'left'), ('ordered', 'Ordered qty', 13, 'right'),
                            ('delivered', 'Delivered qty', 13, 'right'), ('uom', 'UoM', 8, 'left'), ('remarks', 'Remarks', 22, 'left')],
                'rows': [{'no': i, 'description': l.description, 'ordered': q(l.quantity), 'delivered': q(l.quantity), 'uom': l.unit, 'remarks': l.remarks}
                         for i, l in enumerate(data.lines, 1)],
                'boxes': [('CONDITION ON DELIVERY', [('Delivery status', ''), ('Damage or shortage notes', data.notes)])],
                'signatures': ['Delivered by (name, signature, date)', 'Received by (name, signature, date)', 'Company stamp'],
                'footer': 'Goods are received in good order unless noted above.'}
    raise HTTPException(404, 'Unknown document type')


@router.post('/public/document-builder/{kind}/totals')
def builder_totals(kind: Literal['rfq', 'quotation', 'purchase_order', 'invoice', 'delivery_note'], payload: BuilderIn, request: Request):
    enforce_rate_limit(request, scope='public-builder-totals', identity='public', limit=120, window_seconds=60)
    calc, result = _lines(payload.lines)
    return {**{k: str(v) for k, v in result.items()}, 'lines': [str(c.net) for c in calc], 'currency': payload.currency,
            'amount_in_words': amount_in_words(result['total'], payload.currency)}


@router.post('/public/document-builder/{kind}')
def build_document(kind: Literal['rfq', 'quotation', 'purchase_order', 'invoice', 'delivery_note'], payload: BuilderIn, request: Request):
    """Generate a PDF in the client's format. Requires no account; nothing is stored."""
    enforce_rate_limit(request, scope='public-builder', identity='public', limit=30, window_seconds=60)
    pdf = documents.render(build_spec(kind, payload), lang=payload.language)
    name = f"{kind.replace('_', '-')}-{payload.number or 'draft'}".replace('/', '-')[:80]
    return StreamingResponse(BytesIO(pdf), media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="{name}.pdf"', 'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex'})


# --------------------------------------------------------------- WhatsApp
@router.get('/webhooks/whatsapp', response_class=PlainTextResponse)
def whatsapp_verify(mode: str = Query('', alias='hub.mode'), token: str = Query('', alias='hub.verify_token'),
                    challenge: str = Query('', alias='hub.challenge')):
    expected = get_settings().whatsapp_verify_token.get_secret_value()
    if mode != 'subscribe' or not expected or token != expected:
        raise HTTPException(403, 'Verification failed')
    return challenge


@router.post('/webhooks/whatsapp')
async def whatsapp_status(request: Request, db: Session = Depends(get_db)):
    from app.modules.notifications.service import update_delivery_notification
    from app.modules.procurement.whatsapp import STATUS_MAP, verify_signature
    raw = await request.body()
    if not verify_signature(raw, request.headers.get('X-Hub-Signature-256')):
        raise HTTPException(403, 'Invalid signature')
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(400, 'Invalid JSON') from exc
    updated = 0
    for entry in body.get('entry', []):
        for change in entry.get('changes', []):
            for status in (change.get('value') or {}).get('statuses', []):
                event = db.scalar(select(Outbox).where(Outbox.provider_message_id == str(status.get('id')), Outbox.payload['channel'].as_string() == 'whatsapp'))
                mapped = STATUS_MAP.get(str(status.get('status')))
                if not event or not mapped:
                    continue
                event.delivery_status = mapped
                if mapped == 'failed':
                    event.status = 'failed'
                    errors = status.get('errors') or [{}]
                    event.last_error = f"WhatsApp error {errors[0].get('code', '')}: {errors[0].get('title', '')}"[:500]
                update_delivery_notification(db, event.id, mapped)
                updated += 1
    db.commit()
    return Response(status_code=200, content=json.dumps({'updated': updated}), media_type='application/json')


# ------------------------------------------------------- SMS Gateway for Android
SMS_GATE_EVENTS = {'sms:sent': 'Sent', 'sms:delivered': 'Delivered', 'sms:failed': 'Failed'}
SMS_GATE_STATE_RANK = {'Pending': 0, 'Processed': 1, 'Sent': 2, 'Delivered': 3, 'Failed': 3}


def verify_sms_gate_signature(raw: bytes, signature: str | None, timestamp: str | None, now: float | None = None) -> bool:
    """HMAC-SHA256(signing key, raw body + X-Timestamp), hex, within five minutes.

    The signing key is the one configured in the SMS Gateway app/cloud settings and in
    SMS_GATE_WEBHOOK_SIGNING_KEY. Without a configured key the webhook is disabled.
    """
    import hashlib
    import hmac
    import time as _time
    key = get_settings().sms_gate_webhook_signing_key.get_secret_value()
    if not key or not signature or not timestamp:
        return False
    try:
        if abs((now if now is not None else _time.time()) - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    expected = hmac.new(key.encode(), raw + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


@router.post('/webhooks/sms-gate')
async def sms_gate_status(request: Request, db: Session = Depends(get_db)):
    """Delivery reports pushed by SMS Gateway for Android (sms:sent / sms:delivered / sms:failed).

    Authenticated by the provider's HMAC signature, not by a user session. It only updates
    the delivery state of an SMS this platform already submitted (matched by provider
    message id); it can never create or resubmit a message. Status polling remains the
    fallback when webhooks are not configured or not delivered.
    """
    from datetime import UTC, datetime

    from app.notification_worker import apply_sms
    if not get_settings().sms_gate_webhook_signing_key.get_secret_value():
        raise HTTPException(404, 'Not found')
    raw = await request.body()
    if not verify_sms_gate_signature(raw, request.headers.get('X-Signature'), request.headers.get('X-Timestamp')):
        raise HTTPException(403, 'Invalid signature')
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(400, 'Invalid JSON') from exc
    state = SMS_GATE_EVENTS.get(str(body.get('event') or ''))
    payload = body.get('payload') if isinstance(body.get('payload'), dict) else {}
    message_id = str(payload.get('messageId') or payload.get('id') or '')
    if not state or not message_id:
        return {'updated': 0, 'ignored': True}
    event = db.scalar(select(Outbox).where(Outbox.provider_message_id == message_id,
                                           Outbox.event == 'notification.delivery').with_for_update())
    if not event or event.payload.get('channel') != 'sms':
        return {'updated': 0}
    current = event.delivery_status or 'Pending'
    if SMS_GATE_STATE_RANK.get(current, 0) >= SMS_GATE_STATE_RANK[state] and current != 'Sent':
        return {'updated': 0}  # Never downgrade a final state; duplicate reports are idempotent.
    apply_sms(db, event, {'id': message_id, 'state': state, 'reason': payload.get('reason'), 'source': 'webhook'},
              datetime.now(UTC))
    db.commit()
    return {'updated': 1}
