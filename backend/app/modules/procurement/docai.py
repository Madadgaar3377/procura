"""Document AI for supplier quotation PDFs (client guide feature 6).

Upload -> background extraction -> editable review -> explicit confirmation.
Extraction reuses the existing Gemini integration settings; Gemini reads both
text PDFs and scanned PDFs (OCR) natively. Nothing financial is saved to a
quotation until an authorized user confirms the reviewed data.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.models import DocumentExtraction, Invitation, Quotation, QuoteLine, RFQItem, StoredFile

log = logging.getLogger(__name__)

SCHEMA_HINT = {
    'supplier': {'name': '', 'vat_number': '', 'cr_number': '', 'address': '', 'email': '', 'phone': ''},
    'quotation_reference': '', 'quotation_date': 'YYYY-MM-DD', 'valid_until': 'YYYY-MM-DD', 'currency': 'SAR',
    'lines': [{'description': '', 'quantity': '0', 'unit': '', 'unit_price': '0.00', 'discount_percent': '0', 'line_total': '0.00'}],
    'subtotal': '0.00', 'discount': '0.00', 'vat_rate_percent': '15', 'vat_amount': '0.00', 'total': '0.00',
    'payment_terms': '', 'delivery_terms': '', 'delivery_lead_time': '', 'warranty': '', 'notes': '',
}


def read_file_bytes(stored: StoredFile) -> bytes:
    settings = get_settings()
    if settings.r2_enabled and stored.bucket_type == 'private' and not stored.object_key.startswith('local:'):
        from app.storage import stream_object
        return stream_object('private', stored.object_key)[0]
    from pathlib import Path
    root = (Path(settings.upload_dir).resolve() / 'document-ai').resolve()
    target = (root / stored.object_key.removeprefix('local:')).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise FileNotFoundError('Stored document is missing')
    return target.read_bytes()


def gemini_extract(content: bytes, content_type: str) -> dict:
    """Ask Gemini for structured quotation data. Raises RuntimeError when unavailable."""
    settings = get_settings()
    key = settings.gemini_api_key.get_secret_value()
    if not settings.gemini_enabled or not key:
        raise RuntimeError('Document AI requires GEMINI_ENABLED=true and GEMINI_API_KEY')
    prompt = ('Extract the supplier quotation in this document as JSON matching this shape exactly. '
              'Copy numbers exactly as printed (no currency symbols, use dot decimals). Use empty strings for anything not present. '
              'Never invent values. Shape: ' + json.dumps(SCHEMA_HINT))
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent'
    body = {
        'contents': [{'role': 'user', 'parts': [
            {'inline_data': {'mime_type': content_type, 'data': base64.b64encode(content).decode()}},
            {'text': prompt},
        ]}],
        'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json', 'maxOutputTokens': 8192},
    }
    try:
        with httpx.Client(timeout=max(30.0, settings.gemini_timeout_seconds), follow_redirects=False) as client:
            response = client.post(url, headers={'x-goog-api-key': key}, json=body)
            response.raise_for_status()
            text = response.json()['candidates'][0]['content']['parts'][0]['text']
        data = json.loads(text)
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise RuntimeError(f'Document AI extraction failed ({type(exc).__name__})') from exc
    if not isinstance(data, dict):
        raise RuntimeError('Document AI returned an unexpected structure')
    return data


def _dec(value) -> str:
    try:
        return str(Decimal(str(value).replace(',', '').strip() or '0'))
    except (InvalidOperation, ValueError):
        return ''


def normalize(data: dict) -> dict:
    lines = []
    for line in data.get('lines') or []:
        if not isinstance(line, dict) or not str(line.get('description') or '').strip():
            continue
        lines.append({key: (_dec(line.get(key)) if key in {'quantity', 'unit_price', 'discount_percent', 'line_total'} else str(line.get(key) or ''))
                      for key in ('description', 'quantity', 'unit', 'unit_price', 'discount_percent', 'line_total')})
    out = {key: data.get(key) for key in SCHEMA_HINT if key != 'lines'}
    out['supplier'] = {k: str((data.get('supplier') or {}).get(k) or '') for k in SCHEMA_HINT['supplier']}
    for key in ('subtotal', 'discount', 'vat_rate_percent', 'vat_amount', 'total'):
        out[key] = _dec(data.get(key))
    for key in ('quotation_reference', 'quotation_date', 'valid_until', 'currency', 'payment_terms', 'delivery_terms', 'delivery_lead_time', 'warranty', 'notes'):
        out[key] = str(data.get(key) or '')
    out['lines'] = lines
    # Arithmetic cross-check so the reviewer sees inconsistencies instead of trusting OCR blindly.
    warnings = []
    computed = Decimal(0)
    for n, line in enumerate(lines, 1):
        try:
            gross = Decimal(line['quantity'] or 0) * Decimal(line['unit_price'] or 0)
            net = gross * (1 - Decimal(line['discount_percent'] or 0) / 100)
            computed += net
            if line['line_total'] and abs(net - Decimal(line['line_total'])) > Decimal('0.05'):
                warnings.append(f'Line {n}: quantity x price ({net:.2f}) differs from printed line total ({line["line_total"]})')
        except InvalidOperation:
            warnings.append(f'Line {n}: unreadable number')
    if out['subtotal'] and lines and abs(computed - Decimal(out['subtotal'])) > Decimal('0.05') and not out['discount']:
        warnings.append(f'Sum of lines ({computed:.2f}) differs from printed subtotal ({out["subtotal"]})')
    out['warnings'] = warnings
    return out


def process_extraction(db, event) -> None:
    """Background job; always ends in review or failed."""
    from app.worker import PermanentOutboxError
    job = db.get(DocumentExtraction, (event.payload or {}).get('extraction_id'))
    if not job:
        raise PermanentOutboxError('Document extraction no longer exists')
    if job.status in {'review', 'confirmed', 'failed'}:
        return
    stored = db.get(StoredFile, job.file_id)
    job.status = 'processing'
    db.commit()  # release locks before network I/O
    try:
        content = read_file_bytes(stored)
        data = normalize(gemini_extract(content, stored.content_type))
        job.extracted = data
        job.method = 'gemini_document'
        job.status = 'review'
        job.error = None
    except Exception as exc:  # noqa: BLE001 - job must reach a terminal state
        log.warning('Document AI extraction failed id=%s (%s)', job.id, type(exc).__name__)
        job.status = 'review'
        job.method = 'manual'
        job.extracted = {**normalize({}), 'warnings': ['Automatic extraction was not available. Enter the quotation details manually.']}
        job.error = str(exc)[:500]
    job.updated_at = datetime.now(UTC)


def confirm(db, job: DocumentExtraction, payload, actor) -> DocumentExtraction:
    """Save reviewed data; optionally record it as the supplier's quotation for an RFQ."""
    from app.modules.quotations.automation import submit_record
    from app.security import audit
    if job.status == 'confirmed':
        raise HTTPException(409, 'This extraction was already confirmed')
    if job.status not in {'review'}:
        raise HTTPException(409, 'Extraction is still processing')
    data = payload.model_dump(mode='json')
    job.confirmed_data = data
    job.confirmed_by_user_id = actor.id
    job.confirmed_at = datetime.now(UTC)
    job.status = 'confirmed'
    quote_id = None
    if payload.rfq_id and payload.supplier_org_id and any(line.rfq_item_id for line in payload.lines):
        valid = set(db.scalars(select(RFQItem.id).where(RFQItem.rfq_id == payload.rfq_id)))
        inv = db.scalar(select(Invitation).where(Invitation.rfq_id == payload.rfq_id, Invitation.supplier_org_id == payload.supplier_org_id).with_for_update())
        mapped = [line for line in payload.lines if line.rfq_item_id]
        if any(line.rfq_item_id not in valid for line in mapped) or len({l.rfq_item_id for l in mapped}) != len(mapped):
            raise HTTPException(422, 'Each mapped line must reference a different item of this RFQ')
        if not inv:
            inv = Invitation(rfq_id=payload.rfq_id, supplier_org_id=payload.supplier_org_id, status='accepted', matched_item_ids=[l.rfq_item_id for l in mapped])
            db.add(inv)
            db.flush()
        quote = db.scalar(select(Quotation).where(Quotation.invitation_id == inv.id).with_for_update())
        if quote and quote.status != 'draft':
            raise HTTPException(409, 'This supplier already has a submitted quotation for the RFQ')
        if not quote:
            quote = Quotation(invitation_id=inv.id, rfq_id=payload.rfq_id, supplier_org_id=payload.supplier_org_id)
            db.add(quote)
            db.flush()
        for old in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id == quote.id)).all():
            db.delete(old)
        db.flush()
        for line in mapped:
            item = db.get(RFQItem, line.rfq_item_id)
            db.add(QuoteLine(quotation_id=quote.id, rfq_item_id=item.id, offered_description=line.description, quantity=line.quantity,
                             unit_price=line.unit_price, discount_percent=line.discount_percent, lead_days=payload.lead_days or 0,
                             warranty_months=payload.warranty_months, compliance='unconfirmed', note='Recorded from supplier PDF via Document AI'))
        quote.currency = payload.currency
        quote.vat_rate = payload.vat_rate
        quote.commercial_terms = {'quotation_reference': payload.quotation_reference, 'payment_terms': payload.payment_terms,
                                  'delivery_terms': payload.delivery_terms, 'warranty_terms': payload.warranty,
                                  'validity_until': payload.valid_until.isoformat() if payload.valid_until else None,
                                  'discount_amount': str(payload.header_discount)}
        db.flush()
        submit_record(db, quote, actor)
        quote.submission_source = 'document_ai'
        quote_id = quote.id
    audit(db, actor, 'document_ai.confirmed', job.org_id, job.id, quotation_id=quote_id)
    db.flush()
    job.extracted = {**(job.extracted or {}), 'quotation_id': quote_id}
    return job
