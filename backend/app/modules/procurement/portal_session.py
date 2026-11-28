"""Restricted supplier portal sessions exchanged from an invitation magic link.

Flow (imported suppliers need no account):

1. The email/SMS link is ``/supplier-portal#token=<raw>``. The fragment never reaches
   any server or access log. The page reads it, removes it from the address bar and
   POSTs it once to ``/supplier-portal/session``.
2. The token (SHA-256 hash stored, expiring, revocable, bound to one supplier
   organization, invitation and RFQ) is exchanged for a restricted session: a random
   secret in an HttpOnly cookie ``pp_<invitation>`` scoped to ``/api/v1/supplier-portal``.
   The session authorizes only that invitation's portal endpoints below; it is not a
   user account and grants nothing else (no other RFQ, supplier, price book or admin API).
3. Binding actions (submitting a quotation) require recipient verification: a one-time
   code sent to the same email/phone the link was delivered to.

Raw link tokens, session secrets and codes are never logged or stored.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (Category, Invitation, Organization, Outbox, Quotation, QuoteLine, RFQ, RFQItem,
                        SupplierAccessToken, SupplierPortalSession)
from app.modules.procurement.portal import _hash, apply_quote, matched_item_ids, portal_payload, revoke_links, rfq_open
from app.rate_limit import enforce_rate_limit
from app.schemas import Model, QuoteIn
from app.security import as_utc, audit

router = APIRouter(prefix='/supplier-portal', tags=['Supplier Magic Link Portal'])

COOKIE_PATH = '/api/v1/supplier-portal'
_TOKEN_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-')


def _cookie_name(invitation_id: str) -> str:
    return 'pp_' + invitation_id.replace('-', '')[:32]


def _limit(request: Request, scope: str = 'supplier-portal-session', limit: int = 60):
    enforce_rate_limit(request, scope=scope, identity='magic-link', limit=limit, window_seconds=60)


def _deny(status: int, code: str, message: str):
    # Structured detail so the page can show the right state (invalid/expired/revoked/...).
    raise HTTPException(status, {'code': code, 'message': message})


def _no_store(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'


# ------------------------------------------------------------------ exchange
class SessionIn(Model):
    token: str = Field(min_length=20, max_length=200)


def resolve_link(db, token: str, now: datetime):
    """The access token row, invitation and RFQ for a raw link, or a precise denial."""
    if not token or not set(token) <= _TOKEN_CHARS:
        _deny(401, 'invalid_link', 'This link is not valid. Open the full link from your email or SMS.')
    row = db.scalar(select(SupplierAccessToken).where(SupplierAccessToken.token_hash == _hash(token)))
    if not row:
        _deny(401, 'invalid_link', 'This link is not valid. Open the full link from your email or SMS.')
    if row.revoked_at is not None:
        _deny(401, 'link_revoked', 'This link was replaced or revoked. Use the most recent invitation message, '
                                   'or ask the buyer for a new link.')
    if as_utc(row.expires_at) <= now:
        _deny(401, 'link_expired', 'This link has expired. Ask the buyer or Procumaterial for a new invitation link.')
    rfq = db.get(RFQ, row.rfq_id)
    inv = db.scalar(select(Invitation).where(Invitation.id == row.invitation_id, Invitation.rfq_id == row.rfq_id,
                                             Invitation.supplier_org_id == row.supplier_org_id))
    if not rfq or not inv:
        _deny(403, 'invitation_unavailable', 'This invitation is no longer available.')
    if inv.status == 'cancelled':
        _deny(403, 'invitation_withdrawn', 'This invitation was withdrawn by the buyer and is no longer available.')
    if inv.status == 'declined':
        _deny(403, 'invitation_declined', 'You declined this invitation. Contact the buyer if this was a mistake.')
    _require_supplier_active(db, inv.supplier_org_id)
    return row, inv, rfq


def _require_supplier_active(db, org_id: str):
    org = db.get(Organization, org_id)
    if not org or not org.is_active or org.verified_status in {'rejected', 'suspended'}:
        _deny(403, 'supplier_unavailable', 'This supplier account cannot respond to RFQs. Contact Procumaterial support.')


@router.post('/session')
def start_session(payload: SessionIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Exchange a magic-link token for a restricted, HttpOnly portal session."""
    _limit(request, 'supplier-portal-exchange', 30)
    _no_store(response)
    now = datetime.now(UTC)
    row, inv, rfq = resolve_link(db, payload.token, now)
    settings = get_settings()
    secret = secrets.token_urlsafe(32)
    expires = min(as_utc(row.expires_at), now + timedelta(hours=settings.supplier_portal_session_hours))
    session = SupplierPortalSession(token_hash=_hash(secret), access_token_id=row.id, invitation_id=inv.id,
                                    rfq_id=rfq.id, supplier_org_id=inv.supplier_org_id, expires_at=expires,
                                    last_seen_at=now)
    db.add(session)
    row.last_used_at = now
    row.use_count = (row.use_count or 0) + 1
    db.flush()
    audit(db, None, 'supplier_portal.session_started', inv.supplier_org_id, session.id,
          invitation_id=inv.id, token_id=row.id, channel=row.channel)
    db.commit()
    kwargs = dict(max_age=max(60, int((expires - now).total_seconds())), httponly=True,
                  secure=settings.is_production, samesite='lax', path=COOKIE_PATH)
    if settings.resolved_cookie_domain:
        kwargs['domain'] = settings.resolved_cookie_domain
    response.set_cookie(_cookie_name(inv.id), secret, **kwargs)
    return {'invitation_id': inv.id, 'session_expires_at': expires,
            'portal': _view(db, session, inv, rfq, row)}


# ------------------------------------------------------------------ session auth
class PortalContext:
    def __init__(self, session, inv, rfq, link):
        self.session, self.inv, self.rfq, self.link = session, inv, rfq, link


def portal_context(request: Request, invitation: str | None = Query(None, max_length=36),
                   x_portal_invitation: str | None = Header(None, max_length=36),
                   db: Session = Depends(get_db)) -> PortalContext:
    invitation_id = x_portal_invitation or invitation
    if not invitation_id:
        _deny(401, 'session_required', 'Open the secure link from your invitation to continue.')
    secret = request.cookies.get(_cookie_name(invitation_id))
    if not secret or len(secret) > 200:
        _deny(401, 'session_required', 'Your secure session ended. Open the invitation link again to continue.')
    now = datetime.now(UTC)
    session = db.scalar(select(SupplierPortalSession).where(SupplierPortalSession.token_hash == _hash(secret)))
    if not session or session.invitation_id != invitation_id:
        _deny(401, 'session_expired', 'Your secure session ended. Open the invitation link again to continue.')
    link = db.get(SupplierAccessToken, session.access_token_id)
    # Report the root cause: a revoked link also ends the sessions exchanged from it.
    if not link or link.revoked_at is not None or link.invitation_id != session.invitation_id:
        _deny(401, 'link_revoked', 'This link was revoked. Ask the buyer for a new invitation link.')
    if session.revoked_at is not None or as_utc(session.expires_at) <= now:
        _deny(401, 'session_expired', 'Your secure session ended. Open the invitation link again to continue.')
    rfq = db.get(RFQ, session.rfq_id)
    inv = db.scalar(select(Invitation).where(Invitation.id == session.invitation_id, Invitation.rfq_id == session.rfq_id,
                                             Invitation.supplier_org_id == session.supplier_org_id))
    if not rfq or not inv or inv.status == 'cancelled':
        _deny(403, 'invitation_withdrawn', 'This invitation is no longer available.')
    _require_supplier_active(db, inv.supplier_org_id)
    session.last_seen_at = now
    return PortalContext(session, inv, rfq, link)


def _locked(db, ctx: PortalContext) -> PortalContext:
    """Re-read RFQ then invitation FOR UPDATE (the lock order quotation submission uses)."""
    rfq = db.scalar(select(RFQ).where(RFQ.id == ctx.rfq.id).with_for_update())
    inv = db.scalar(select(Invitation).where(Invitation.id == ctx.inv.id).with_for_update())
    return PortalContext(ctx.session, inv, rfq, ctx.link)


# ------------------------------------------------------------------ view
def _taxonomy(db, items) -> dict[str, str]:
    ids = {value for item in items for value in (item.main_category_id, item.subcategory_id) if value}
    return dict(db.execute(select(Category.id, Category.name_en).where(Category.id.in_(ids))).all()) if ids else {}


def _attachments(db, rfq) -> list[dict]:
    from app.models import StoredFile
    rows = [{'id': a['id'], 'filename': a.get('filename', ''), 'content_type': a.get('content_type', ''),
             'size': a.get('size')} for a in (rfq.attachment_records or []) if a.get('id')]
    rows += [{'id': f.id, 'filename': f.safe_filename, 'content_type': f.content_type, 'size': f.size_bytes}
             for f in db.scalars(select(StoredFile).where(
                 StoredFile.entity_type == 'rfq', StoredFile.entity_id == rfq.id,
                 StoredFile.status == 'available', StoredFile.deleted_at.is_(None)))]
    return rows


def _mask(destination: str | None, channel: str) -> str:
    if not destination:
        return ''
    if channel == 'email' and '@' in destination:
        name, _, domain = destination.partition('@')
        return (name[:2] + '•••@' + domain) if len(name) > 2 else ('•••@' + domain)
    return destination[:4] + '•••' + destination[-3:]


def _view(db, session, inv, rfq, link) -> dict:
    from app.modules.matching.service import requested_brand
    base = portal_payload(db, inv, rfq, link)
    items = db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id).order_by(RFQItem.created_at, RFQItem.id)).all()
    names = _taxonomy(db, items)
    detail = {}
    for item in items:
        main = names.get(item.main_category_id)
        brand = requested_brand(item)
        detail[item.id] = {'discipline': main.split(' - ', 1)[0] if main else None, 'main_category': main,
                           'subcategory': names.get(item.subcategory_id),
                           'required_brand': brand[0] if brand else None,
                           'brand_mandatory': bool(brand and brand[2])}
    base['items'] = [{**entry, **detail.get(entry['id'], {})} for entry in base['items']]
    quote = base.get('quotation')
    if quote:
        quote['submission_source'] = db.scalar(select(Quotation.submission_source).where(Quotation.id == quote['id']))
    supplier = db.get(Organization, inv.supplier_org_id)
    base.update({
        'invitation_status': inv.status,
        'rfq_status': rfq.status,
        'rfq_open': rfq_open(rfq),
        'supplier': {'name': supplier.name if supplier else '', 'city': getattr(supplier, 'city', None) if supplier else None},
        'buyer': {'name': base['buyer_name'],
                  'visible': bool(rfq.public_display_authorized)},
        'attachments': _attachments(db, rfq),
        'verification': {'verified': session.verified_at is not None, 'channel': link.channel,
                         'code_sent_at': session.verification_sent_at,
                         'code_expires_at': session.verification_expires_at},
        'session_expires_at': session.expires_at,
        'can_accept': inv.status == 'invited' and rfq_open(rfq),
        'can_decline': inv.status in {'invited', 'accepted'} and not (quote and quote['status'] != 'draft'),
        'can_download_quotation': bool(quote and quote['status'] != 'draft'),
    })
    return base


@router.get('/me')
def view(response: Response, ctx: PortalContext = Depends(portal_context), db: Session = Depends(get_db)):
    _no_store(response)
    result = _view(db, ctx.session, ctx.inv, ctx.rfq, ctx.link)
    db.commit()
    return result


# ------------------------------------------------------------------ actions
def _require_open(rfq):
    if not rfq_open(rfq):
        _deny(409, 'rfq_closed', 'This RFQ is closed or its response deadline has passed.')


@router.post('/me/accept')
def accept(request: Request, response: Response, ctx: PortalContext = Depends(portal_context),
           db: Session = Depends(get_db)):
    _limit(request)
    _no_store(response)
    ctx = _locked(db, ctx)
    _require_open(ctx.rfq)
    if ctx.inv.status == 'declined':
        _deny(409, 'invitation_declined', 'This invitation was declined.')
    if ctx.inv.status == 'invited':
        ctx.inv.status = 'accepted'
        key = f'rfq-invitation-responded:{ctx.inv.id}:accept'
        if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
            db.add(Outbox(event='rfq.invitation.responded', dedupe_key=key, payload={
                'invitation_id': ctx.inv.id, 'rfq_id': ctx.rfq.id, 'supplier_org_id': ctx.inv.supplier_org_id,
                'action': 'accept'}))
        audit(db, None, 'rfq.invitation_accept_magic_link', ctx.inv.supplier_org_id, ctx.inv.id, session_id=ctx.session.id)
    db.commit()
    return _view(db, ctx.session, ctx.inv, ctx.rfq, ctx.link)


class DeclineIn(Model):
    reason: str = Field(default='', max_length=500)


@router.post('/me/decline')
def decline(payload: DeclineIn, request: Request, response: Response, ctx: PortalContext = Depends(portal_context),
            db: Session = Depends(get_db)):
    _limit(request)
    _no_store(response)
    ctx = _locked(db, ctx)
    if db.scalar(select(Quotation.id).where(Quotation.invitation_id == ctx.inv.id, Quotation.status != 'draft')):
        _deny(409, 'quotation_submitted', 'A submitted quotation cannot be declined.')
    if ctx.inv.status != 'declined':
        ctx.inv.status = 'declined'
        key = f'rfq-invitation-responded:{ctx.inv.id}:decline'
        if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
            db.add(Outbox(event='rfq.invitation.responded', dedupe_key=key, payload={
                'invitation_id': ctx.inv.id, 'rfq_id': ctx.rfq.id, 'supplier_org_id': ctx.inv.supplier_org_id,
                'action': 'decline'}))
        revoke_links(db, ctx.inv.id)
        db.execute(update(SupplierPortalSession).where(SupplierPortalSession.invitation_id == ctx.inv.id,
                                                       SupplierPortalSession.revoked_at.is_(None))
                   .values(revoked_at=datetime.now(UTC)))
        audit(db, None, 'rfq.invitation_declined_magic_link', ctx.inv.supplier_org_id, ctx.inv.id,
              session_id=ctx.session.id, reason=payload.reason.strip()[:200] or None)
    db.commit()
    response.delete_cookie(_cookie_name(ctx.inv.id), path=COOKIE_PATH,
                           domain=get_settings().resolved_cookie_domain or None)
    return {'status': 'declined'}


class DraftIn(Model):
    quotation: QuoteIn


@router.post('/me/draft')
def save_draft(payload: DraftIn, request: Request, response: Response, ctx: PortalContext = Depends(portal_context),
               db: Session = Depends(get_db)):
    _limit(request)
    _no_store(response)
    ctx = _locked(db, ctx)
    _require_open(ctx.rfq)
    if ctx.inv.status == 'declined':
        _deny(409, 'invitation_declined', 'This invitation was declined.')
    _validate_scope(db, ctx.inv, payload.quotation)
    quote = db.scalar(select(Quotation).where(Quotation.invitation_id == ctx.inv.id).with_for_update())
    quote = apply_quote(db, quote, ctx.inv, ctx.rfq, payload.quotation, source='magic_link')
    audit(db, None, 'quotation.magic_link_draft_saved', ctx.inv.supplier_org_id, quote.id,
          invitation_id=ctx.inv.id, session_id=ctx.session.id)
    db.commit()
    return _view(db, ctx.session, ctx.inv, ctx.rfq, ctx.link)


def _validate_scope(db, inv, quotation: QuoteIn):
    """Quoted lines must be this supplier's matched RFQ lines (partial quotations allowed)."""
    scope = set(matched_item_ids(db, inv))
    if scope and any(line.rfq_item_id not in scope for line in quotation.lines):
        _deny(422, 'item_not_in_scope', 'Quote only the RFQ items matched to your company.')


# ------------------------------------------------------------------ verification
@router.post('/me/verification/request')
def request_code(request: Request, response: Response, ctx: PortalContext = Depends(portal_context),
                 db: Session = Depends(get_db)):
    """Send a one-time code to the email/phone this link was delivered to."""
    _limit(request, 'supplier-portal-verify', 10)
    _no_store(response)
    _require_open(ctx.rfq)
    now = datetime.now(UTC)
    session = db.scalar(select(SupplierPortalSession).where(SupplierPortalSession.id == ctx.session.id).with_for_update())
    if session.verified_at:
        return {'verified': True}
    if session.verification_sent_at and now - as_utc(session.verification_sent_at) < timedelta(seconds=60):
        _deny(429, 'code_recently_sent', 'A code was sent less than a minute ago. Check your messages before requesting another.')
    delivery = _link_delivery(db, ctx)
    if not delivery:
        _deny(409, 'verification_unavailable', 'This link cannot be verified automatically. Contact the buyer '
                                               'or Procumaterial support to submit your quotation.')
    from app.modules.notifications.crypto import encrypt_delivery_secret
    settings = get_settings()
    code = f'{secrets.randbelow(1_000_000):06d}'
    session.verification_code_hash = _code_hash(session.id, code)
    session.verification_expires_at = now + timedelta(minutes=settings.supplier_portal_code_minutes)
    session.verification_sent_at = now
    session.verification_attempts = 0
    channel = 'sms' if delivery.payload['channel'] == 'whatsapp' else delivery.payload['channel']
    from uuid import uuid4
    db.add(Outbox(event='notification.delivery', dedupe_key=f'portal-verify:{session.id}:{uuid4().hex}',
                  secret_ciphertext=encrypt_delivery_secret(code), payload={
                      'channel': channel, 'template': 'portal_verification_code', 'procurement_recipient': True,
                      'destination': delivery.payload['destination'], 'contact_id': delivery.payload.get('contact_id'),
                      'recipient_source': 'portal_verification',
                      'context': {'rfq_id': ctx.rfq.id, 'org_id': ctx.inv.supplier_org_id, 'reference': ctx.rfq.reference,
                                  'buyer_org_id': ctx.rfq.buyer_org_id, 'session_id': session.id,
                                  'minutes': settings.supplier_portal_code_minutes}}))
    audit(db, None, 'supplier_portal.verification_requested', ctx.inv.supplier_org_id, session.id, channel=channel)
    db.commit()
    return {'verified': False, 'channel': channel, 'sent_to': _mask(delivery.payload['destination'], channel),
            'expires_at': session.verification_expires_at}


def _code_hash(session_id: str, code: str) -> str:
    return hashlib.sha256(f'{session_id}:{code}'.encode()).hexdigest()


def _link_delivery(db, ctx: PortalContext):
    """The delivery job that carried this link (matched by destination hash)."""
    if not ctx.link.destination_hash:
        return None
    rows = db.scalars(select(Outbox).where(
        Outbox.event == 'notification.delivery',
        Outbox.payload['context']['rfq_id'].as_string() == ctx.rfq.id,
        Outbox.payload['context']['org_id'].as_string() == ctx.inv.supplier_org_id,
        Outbox.payload['template'].as_string().in_(('rfq_invitation', 'deadline_reminder')),
    ).order_by(Outbox.created_at.desc()).limit(50)).all()
    for row in rows:
        destination = row.payload.get('destination')
        if destination and _hash(destination.strip().casefold()) == ctx.link.destination_hash:
            return row
    return None


class CodeIn(Model):
    code: str = Field(min_length=6, max_length=6, pattern=r'^[0-9]{6}$')


@router.post('/me/verification/confirm')
def confirm_code(payload: CodeIn, request: Request, response: Response, ctx: PortalContext = Depends(portal_context),
                 db: Session = Depends(get_db)):
    _limit(request, 'supplier-portal-verify', 20)
    _no_store(response)
    now = datetime.now(UTC)
    session = db.scalar(select(SupplierPortalSession).where(SupplierPortalSession.id == ctx.session.id).with_for_update())
    if session.verified_at:
        return {'verified': True}
    if (not session.verification_code_hash or not session.verification_expires_at
            or as_utc(session.verification_expires_at) <= now):
        _deny(409, 'code_expired', 'The verification code expired. Request a new code.')
    if session.verification_attempts >= 5:
        _deny(429, 'too_many_attempts', 'Too many incorrect codes. Request a new code.')
    session.verification_attempts += 1
    if not hmac.compare_digest(session.verification_code_hash, _code_hash(session.id, payload.code)):
        db.commit()
        _deny(422, 'code_incorrect', 'The code is incorrect. Check the latest message and try again.')
    session.verified_at = now
    session.verification_code_hash = None
    audit(db, None, 'supplier_portal.recipient_verified', ctx.inv.supplier_org_id, session.id)
    db.commit()
    return {'verified': True}


# ------------------------------------------------------------------ submission
class SubmitIn(Model):
    quotation: QuoteIn
    confirm_submission: bool = False


@router.post('/me/submit')
def submit(payload: SubmitIn, request: Request, response: Response, ctx: PortalContext = Depends(portal_context),
           db: Session = Depends(get_db)):
    """Binding submission: explicit confirmation + verified recipient + real prices only."""
    _limit(request)
    _no_store(response)
    if not payload.confirm_submission:
        _deny(422, 'confirmation_required', 'Confirm that you are authorized to submit this quotation.')
    if ctx.session.verified_at is None:
        _deny(403, 'verification_required', 'Verify the code sent to you before submitting a binding quotation.')
    ctx = _locked(db, ctx)
    _require_open(ctx.rfq)
    if ctx.inv.status == 'declined':
        _deny(409, 'invitation_declined', 'This invitation was declined.')
    lines = payload.quotation.lines
    # Never submit fake or zero prices: unpriced items are simply left out (partial quote).
    if any(line.unit_price <= 0 for line in lines):
        _deny(422, 'price_required', 'Every quoted item needs a unit price above zero. Remove items you do not quote.')
    _validate_scope(db, ctx.inv, payload.quotation)
    quote = db.scalar(select(Quotation).where(Quotation.invitation_id == ctx.inv.id).with_for_update())
    quote = apply_quote(db, quote, ctx.inv, ctx.rfq, payload.quotation, source='magic_link')
    from app.modules.quotations.automation import submit_record
    submit_record(db, quote, None)  # queues the buyer notification (quotation.submitted)
    quote.submission_source = 'magic_link'
    audit(db, None, 'quotation.magic_link_submitted', ctx.inv.supplier_org_id, quote.id,
          invitation_id=ctx.inv.id, session_id=ctx.session.id, verified_channel=ctx.link.channel)
    db.commit()
    return _view(db, ctx.session, ctx.inv, ctx.rfq, ctx.link)


# ------------------------------------------------------------------ downloads
def _pdf(content: bytes, name: str):
    from app.modules.procurement.router import _pdf as stream
    return stream(content, name, 'attachment')


@router.get('/me/rfq.pdf')
def rfq_document(lang: Literal['en', 'ar'] = 'en', ctx: PortalContext = Depends(portal_context),
                 db: Session = Depends(get_db)):
    from app.modules.procurement import documents
    supplier = db.get(Organization, ctx.inv.supplier_org_id)
    spec = documents.rfq_spec(db, ctx.rfq, supplier)
    if not ctx.rfq.public_display_authorized:
        # Buyer identity follows its visibility permission, also in the document.
        spec['parties'] = [('FROM (BUYER)', ['Procumaterial buyer']), *spec['parties'][1:]]
        spec['logo_text'] = 'Procumaterial'
    db.commit()
    return _pdf(documents.render(spec, lang=lang), f'RFQ-{ctx.rfq.reference}-{lang}')


@router.get('/me/quotation.pdf')
def quotation_document(lang: Literal['en', 'ar'] = 'en', ctx: PortalContext = Depends(portal_context),
                       db: Session = Depends(get_db)):
    from app.modules.procurement import documents
    quote = db.scalar(select(Quotation).where(Quotation.invitation_id == ctx.inv.id))
    if not quote or quote.status == 'draft':
        _deny(404, 'quotation_not_submitted', 'No submitted quotation is available to download yet.')
    spec = documents.quotation_spec(db, quote)
    db.commit()
    return _pdf(documents.render(spec, lang=lang), f"Quotation-{spec['reference']}-{lang}")


@router.get('/me/attachments/{attachment_id}')
def attachment(attachment_id: str, ctx: PortalContext = Depends(portal_context), db: Session = Depends(get_db)):
    from pathlib import Path

    from fastapi.responses import FileResponse, RedirectResponse

    from app.import_storage import LocalImportStorage
    from app.models import StoredFile
    rfq = ctx.rfq
    stored = db.scalar(select(StoredFile).where(StoredFile.id == attachment_id, StoredFile.entity_type == 'rfq',
                                                StoredFile.entity_id == rfq.id, StoredFile.status == 'available',
                                                StoredFile.deleted_at.is_(None)))
    headers = {'Cache-Control': 'private, no-store', 'X-Robots-Tag': 'noindex, nofollow'}
    if stored:
        from app.storage import StorageUnavailable, generate_download_url
        try:
            url = generate_download_url('private', stored.object_key, stored.safe_filename, stored.content_type)
        except StorageUnavailable as exc:
            raise HTTPException(503, 'File storage is temporarily unavailable') from exc
        audit(db, None, 'file.download_authorized_magic_link', ctx.inv.supplier_org_id, stored.id, session_id=ctx.session.id)
        db.commit()
        return RedirectResponse(url, status_code=307, headers=headers)
    entry = next((e for e in (rfq.attachment_records or []) if e.get('id') == attachment_id), None)
    if not entry:
        _deny(404, 'attachment_not_found', 'Attachment not found.')
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(503, 'Attachment malware scanning is not configured')
    target = LocalImportStorage(str(Path(settings.upload_dir) / 'rfq-attachments'))._target(entry['storage_key'])
    if not target.is_file():
        _deny(404, 'attachment_not_found', 'Attachment not found.')
    db.commit()
    return FileResponse(target, media_type=entry['content_type'], filename=entry['filename'],
                        headers={**headers, 'X-Content-Type-Options': 'nosniff'})


@router.post('/me/logout')
def logout(response: Response, ctx: PortalContext = Depends(portal_context), db: Session = Depends(get_db)):
    session = db.get(SupplierPortalSession, ctx.session.id)
    session.revoked_at = datetime.now(UTC)
    db.commit()
    response.delete_cookie(_cookie_name(ctx.inv.id), path=COOKIE_PATH,
                           domain=get_settings().resolved_cookie_domain or None)
    return {'status': 'signed_out'}
