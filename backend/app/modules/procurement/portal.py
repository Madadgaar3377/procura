"""Passwordless supplier quotation portal (client guide steps 3-4).

A magic link carries a random 256-bit token. Only its SHA-256 hash is stored in
``supplier_access_tokens`` together with the invitation, RFQ and supplier it is
scoped to. Every request re-checks expiry, revocation and the invitation, and
only ever reads or writes that supplier's own quotation for that RFQ.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (Invitation, Organization, Quotation, QuoteLine, RFQ, RFQItem, RFQMatch,
                        SupplierAccessToken)
from app.rate_limit import enforce_rate_limit
from app.schemas import Model, QuoteIn
from app.security import as_utc, audit
from app.serializers import record

router = APIRouter(prefix='/supplier-portal', tags=['Supplier Magic Link Portal'])

OPEN_RFQ = {'published', 'collecting_quotes'}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def portal_url(token: str) -> str:
    return get_settings().frontend_url.rstrip('/') + '/supplier-portal#token=' + token


def issue_link(db, invitation: Invitation, rfq: RFQ, *, channel: str, destination: str | None = None,
               contact_id: str | None = None, now: datetime | None = None) -> str:
    """Create a new magic link token for one invitation. Returns the raw token (never stored)."""
    now = now or datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    expires = min(as_utc(rfq.response_deadline) + timedelta(days=2), now + timedelta(days=get_settings().magic_link_days))
    db.add(SupplierAccessToken(
        token_hash=_hash(token), invitation_id=invitation.id, rfq_id=rfq.id,
        supplier_org_id=invitation.supplier_org_id, channel=channel[:12],
        destination_hash=_hash(destination.strip().casefold()) if destination else None,
        contact_id=contact_id, expires_at=expires,
    ))
    db.flush()
    return token


def revoke_links(db, invitation_id: str, now: datetime | None = None) -> int:
    """Revoke every link of an invitation and end the portal sessions exchanged from them."""
    from app.models import SupplierPortalSession
    now = now or datetime.now(UTC)
    result = db.execute(update(SupplierAccessToken).where(
        SupplierAccessToken.invitation_id == invitation_id,
        SupplierAccessToken.revoked_at.is_(None),
    ).values(revoked_at=now))
    db.execute(update(SupplierPortalSession).where(
        SupplierPortalSession.invitation_id == invitation_id,
        SupplierPortalSession.revoked_at.is_(None),
    ).values(revoked_at=now))
    return result.rowcount or 0


def matched_item_ids(db, invitation: Invitation) -> list[str]:
    """Matched lines for this supplier; falls back to the match's recorded item reasons."""
    if invitation.matched_item_ids:
        return list(invitation.matched_item_ids)
    match = db.scalar(select(RFQMatch).where(RFQMatch.rfq_id == invitation.rfq_id,
                                             RFQMatch.supplier_org_id == invitation.supplier_org_id))
    ids = [reason[5:] for reason in (match.reasons or []) if isinstance(reason, str) and reason.startswith('item:')] if match else []
    return ids


def authorize(db, token: str, *, lock: bool = False):
    if not token or len(token) < 20 or len(token) > 200 or not re.fullmatch(r'[A-Za-z0-9_\-]+', token):
        raise HTTPException(401, 'Invalid or expired link')
    now = datetime.now(UTC)
    row = db.scalar(select(SupplierAccessToken).where(SupplierAccessToken.token_hash == _hash(token)))
    if not row or row.revoked_at is not None or as_utc(row.expires_at) <= now:
        raise HTTPException(401, 'This link has expired or was revoked. Ask the buyer or Procumaterial for a new link.')
    rfq_query = select(RFQ).where(RFQ.id == row.rfq_id)
    rfq = db.scalar(rfq_query.with_for_update() if lock else rfq_query)
    inv_query = select(Invitation).where(Invitation.id == row.invitation_id, Invitation.rfq_id == row.rfq_id,
                                         Invitation.supplier_org_id == row.supplier_org_id)
    inv = db.scalar(inv_query.with_for_update() if lock else inv_query)
    if not rfq or not inv or inv.status in {'declined', 'cancelled'}:
        raise HTTPException(403, 'This invitation is no longer available')
    org = db.get(Organization, inv.supplier_org_id)
    if not org or not org.is_active or org.verified_status in {'rejected', 'suspended'}:
        raise HTTPException(403, 'This invitation is no longer available')
    row.last_used_at = now
    row.use_count = (row.use_count or 0) + 1
    return row, inv, rfq


def rfq_open(rfq: RFQ) -> bool:
    return rfq.status in OPEN_RFQ and as_utc(rfq.response_deadline) > datetime.now(UTC)


def portal_payload(db, inv: Invitation, rfq: RFQ, row: SupplierAccessToken) -> dict:
    from app.modules.quotations.router import invoice
    supplier = db.get(Organization, inv.supplier_org_id)
    buyer = db.get(Organization, rfq.buyer_org_id)
    items = db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id).order_by(RFQItem.created_at, RFQItem.id)).all()
    matched = set(matched_item_ids(db, inv))
    quote = db.scalar(select(Quotation).where(Quotation.invitation_id == inv.id))
    lines = db.scalars(select(QuoteLine).where(QuoteLine.quotation_id == quote.id)).all() if quote else []
    return {
        'invitation_id': inv.id,
        'supplier_name': supplier.name if supplier else '',
        'buyer_name': buyer.name if buyer and rfq.public_display_authorized else 'Procumaterial buyer',
        'rfq': {
            'reference': rfq.reference, 'title': rfq.project_name or rfq.title, 'city': rfq.city,
            'delivery_address': rfq.delivery_address, 'response_deadline': rfq.response_deadline,
            'required_delivery_date': rfq.required_delivery_date, 'instructions': rfq.additional_instructions,
        },
        'items': [{**record(item), 'matched': item.id in matched} for item in items],
        'matched_item_ids': [item.id for item in items if item.id in matched],
        'quotation': {
            'id': quote.id, 'status': quote.status, 'currency': quote.currency, 'vat_rate': str(quote.vat_rate),
            'price_basis': quote.price_basis, 'commercial_terms': quote.commercial_terms or {},
            'review_reasons': quote.review_reasons or [], 'submitted_at': quote.submitted_at,
            'totals': invoice(db, quote, lines),
        } if quote else None,
        'lines': [{
            'rfq_item_id': line.rfq_item_id, 'unit_price': str(line.unit_price), 'quantity': str(line.quantity),
            'discount_percent': str(line.discount_percent or 0), 'brand': line.brand, 'model': line.model,
            'offered_description': line.offered_description, 'offered_specs': line.offered_specs,
            'lead_days': line.lead_days, 'warranty_months': line.warranty_months, 'availability': line.availability,
            'compliance': line.compliance, 'note': line.note, 'prefilled_from_price_book': bool(line.source_rate_id),
        } for line in lines],
        'editable': bool(rfq_open(rfq) and (not quote or quote.status == 'draft')),
        'link_expires_at': row.expires_at,
    }


def apply_quote(db, quote: Quotation | None, inv: Invitation, rfq: RFQ, payload: QuoteIn, *, source: str) -> Quotation:
    """Replace a draft's lines with the supplier's edits, keeping price-book provenance."""
    if quote and quote.status != 'draft':
        raise HTTPException(409, 'This quotation was already submitted')
    valid = set(db.scalars(select(RFQItem.id).where(RFQItem.rfq_id == rfq.id)))
    used = set()
    for line in payload.lines:
        if line.rfq_item_id not in valid or line.rfq_item_id in used:
            raise HTTPException(422, 'Invalid or duplicate quoted item')
        used.add(line.rfq_item_id)
    if payload.price_basis not in {'supply', 'supply_install'}:
        raise HTTPException(422, 'Invalid price basis')
    if not quote:
        quote = Quotation(invitation_id=inv.id, rfq_id=rfq.id, supplier_org_id=inv.supplier_org_id)
        db.add(quote)
        db.flush()
    originals = {line.rfq_item_id: line for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id == quote.id))}
    for line in originals.values():
        db.delete(line)
    db.flush()
    for line in payload.lines:
        original = originals.get(line.rfq_item_id)
        provenance = {key: getattr(original, key) for key in ('source_rate_id', 'source_rate_version', 'source_rate_snapshot', 'vat_rate')} if original else {}
        db.add(QuoteLine(quotation_id=quote.id, **line.model_dump(), **provenance))
    quote.price_basis = payload.price_basis
    quote.vat_rate = payload.vat_rate
    quote.currency = payload.currency
    quote.commercial_terms = payload.commercial_terms.model_dump(mode='json')
    quote.review_required = True
    if source == 'magic_link' and quote.submission_source != 'automatic_draft':
        quote.submission_source = 'magic_link'
    if inv.status == 'invited':
        inv.status = 'accepted'
    db.flush()
    return quote


class PortalAccess(Model):
    token: str = Field(min_length=20, max_length=200)


class PortalQuote(PortalAccess):
    quotation: QuoteIn


class PortalApprove(PortalQuote):
    confirm_submission: bool = False


def _limit(request: Request):
    enforce_rate_limit(request, scope='supplier-portal', identity='magic-link', limit=90, window_seconds=60)


@router.post('/open')
def open_portal(payload: PortalAccess, request: Request, response: Response, db: Session = Depends(get_db)):
    _limit(request)
    response.headers['Cache-Control'] = 'no-store'
    row, inv, rfq = authorize(db, payload.token)
    result = portal_payload(db, inv, rfq, row)
    db.commit()
    return result


@router.post('/save')
def save_draft(payload: PortalQuote, request: Request, response: Response, db: Session = Depends(get_db)):
    _limit(request)
    response.headers['Cache-Control'] = 'no-store'
    row, inv, rfq = authorize(db, payload.token, lock=True)
    if not rfq_open(rfq):
        raise HTTPException(409, 'The RFQ response deadline has passed')
    quote = db.scalar(select(Quotation).where(Quotation.invitation_id == inv.id).with_for_update())
    quote = apply_quote(db, quote, inv, rfq, payload.quotation, source='magic_link')
    audit(db, None, 'quotation.magic_link_draft_saved', inv.supplier_org_id, quote.id, invitation_id=inv.id, token_id=row.id)
    db.commit()
    return portal_payload(db, inv, rfq, row)


@router.post('/approve')
def approve_and_submit(payload: PortalApprove, request: Request, response: Response, db: Session = Depends(get_db)):
    """The supplier's explicit approval is what submits the quotation to the buyer."""
    _limit(request)
    response.headers['Cache-Control'] = 'no-store'
    if not payload.confirm_submission:
        raise HTTPException(422, 'Confirm that you approve and submit this quotation')
    row, inv, rfq = authorize(db, payload.token, lock=True)
    if not rfq_open(rfq):
        raise HTTPException(409, 'The RFQ response deadline has passed')
    lines = payload.quotation.lines
    if not lines:
        raise HTTPException(422, 'Price at least one item before submitting')
    if any(line.unit_price <= 0 for line in lines):
        raise HTTPException(422, 'Every quoted item needs a unit price above zero. Remove items you do not want to quote.')
    quote = db.scalar(select(Quotation).where(Quotation.invitation_id == inv.id).with_for_update())
    quote = apply_quote(db, quote, inv, rfq, payload.quotation, source='magic_link')
    from app.modules.quotations.automation import submit_record
    submit_record(db, quote, None)
    quote.submission_source = 'magic_link'
    audit(db, None, 'quotation.magic_link_approved', inv.supplier_org_id, quote.id, invitation_id=inv.id, token_id=row.id)
    db.commit()
    return portal_payload(db, inv, rfq, row)


@router.post('/decline')
def decline(payload: PortalAccess, request: Request, db: Session = Depends(get_db)):
    _limit(request)
    row, inv, rfq = authorize(db, payload.token, lock=True)
    quote = db.scalar(select(Quotation.id).where(Quotation.invitation_id == inv.id, Quotation.status != 'draft'))
    if quote:
        raise HTTPException(409, 'A submitted quotation cannot be declined')
    inv.status = 'declined'
    revoke_links(db, inv.id)
    audit(db, None, 'rfq.invitation_declined_magic_link', inv.supplier_org_id, inv.id, token_id=row.id)
    db.commit()
    return {'status': 'declined'}
