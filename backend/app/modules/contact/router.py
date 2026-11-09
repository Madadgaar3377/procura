"""Durable public contact intake and administrator review."""
from datetime import date, datetime, time, timezone
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditEvent, ContactInquiry, Member, User, Outbox
from app.rate_limit import enforce_rate_limit
from app.response_contracts import ContactInquiryOut, ResponseModel
from app.schemas import ContactInquiryCreate, ContactInquiryStatus, ContactInquiryNote
from app.security import admin_user, audit, current_user, bearer
from fastapi.security import HTTPAuthorizationCredentials

router = APIRouter(prefix='/contact', tags=['Public Contact & Support'])


class ContactReceipt(ResponseModel):
    reference: str
    message: str


@router.post('', status_code=201, response_model=ContactReceipt)
def submit(payload: ContactInquiryCreate, request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)):
    if payload.submission_key:
        existing=db.scalar(select(ContactInquiry).where(ContactInquiry.submission_key==str(payload.submission_key)))
        if existing:
            return {'reference': existing.ticket_reference, 'message': 'Your enquiry has been received. Our team will review it.'}
    # FastAPI is reached through the same-origin BFF, so request.client may be
    # the shared Next.js egress address. Key this public limit by normalized,
    # hashed email instead of throttling every visitor behind that proxy.
    enforce_rate_limit(request, scope='public-contact', identity=sha256(payload.email.encode()).hexdigest(), limit=5, window_seconds=3600)
    if payload.website:
        return {'reference': 'received', 'message': 'Your enquiry has been received.'}
    user = None
    try:
        user = current_user(request, creds, db)
    except HTTPException:
        pass
    memberships = db.scalars(select(Member.organization_id).where(Member.user_id == user.id)).all() if user else []
    ticket = f"PM-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8].upper()}"
    inquiry = ContactInquiry(
        ticket_reference=ticket, submission_key=str(payload.submission_key) if payload.submission_key else None, user_id=user.id if user else None,
        organization_id=memberships[0] if len(memberships) == 1 else None,
        name=payload.name.strip(), email=payload.email, company=payload.company.strip() if payload.company else None,
        phone=payload.phone.strip() if payload.phone else None, topic=payload.topic, user_type=payload.user_type,
        category=payload.category, subject=payload.subject.strip(), message=payload.message.strip(),
    )
    db.add(inquiry)
    db.flush()
    # The worker loads personal data from the protected inquiry row; outbox payloads contain only its ID.
    db.add(Outbox(event='contact.inquiry.support', payload={'inquiry_id': inquiry.id}, dedupe_key=f'contact-support:{inquiry.id}'))
    db.add(Outbox(event='contact.inquiry.ack', payload={'inquiry_id': inquiry.id}, dedupe_key=f'contact-ack:{inquiry.id}'))
    db.commit()
    return {'reference': ticket, 'message': 'Your enquiry has been received. Our team will review it.'}


@router.get('/admin/inquiries', response_model=list[ContactInquiryOut])
def list_inquiries(
    status: Literal['new','in_progress','waiting_for_user','reviewed','resolved','closed'] | None = Query(None),
    category: str | None = Query(None, max_length=40), search: str | None = Query(None, max_length=200),
    created_after: date | None = Query(None), created_before: date | None = Query(None),
    sort: Literal['newest','oldest'] = Query('newest'),
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    query = select(ContactInquiry).order_by(ContactInquiry.created_at.desc() if sort=='newest' else ContactInquiry.created_at.asc()).offset(offset).limit(limit)
    if status:
        query = query.where(ContactInquiry.status == status)
    if category:
        query = query.where(ContactInquiry.category == category)
    if created_after:
        query=query.where(ContactInquiry.created_at>=datetime.combine(created_after,time.min,tzinfo=timezone.utc))
    if created_before:
        query=query.where(ContactInquiry.created_at<datetime.combine(created_before.fromordinal(created_before.toordinal()+1),time.min,tzinfo=timezone.utc))
    if search and search.strip():
        term=f'%{search.strip()}%'
        query=query.where(or_(ContactInquiry.ticket_reference.ilike(term), ContactInquiry.name.ilike(term), ContactInquiry.email.ilike(term), ContactInquiry.subject.ilike(term)))
    return db.scalars(query).all()


@router.patch('/admin/inquiries/{inquiry_id}', response_model=ContactInquiryOut)
def update_inquiry(
    inquiry_id: str, payload: ContactInquiryStatus,
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    inquiry = db.get(ContactInquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, 'Contact enquiry not found')
    requested_status = 'in_progress' if payload.status == 'reviewed' else payload.status
    transitions = {
        'new': {'in_progress', 'closed'},
        'in_progress': {'waiting_for_user', 'resolved', 'closed'},
        'waiting_for_user': {'in_progress', 'resolved', 'closed'},
        'resolved': {'in_progress', 'closed'},
        'closed': set(),
    }
    if requested_status != inquiry.status and requested_status not in transitions.get(inquiry.status, set()):
        raise HTTPException(409, 'This status transition is not available')
    inquiry.status = requested_status
    if requested_status == 'resolved' and inquiry.resolved_at is None:
        inquiry.resolved_at = datetime.now(timezone.utc)
    elif requested_status == 'in_progress':
        inquiry.resolved_at = None
    inquiry.handled_by = admin.id
    inquiry.handled_at = datetime.now(timezone.utc)
    audit(db, admin, 'contact.inquiry_updated', entity_id=inquiry.id, status=requested_status)
    db.commit()
    db.refresh(inquiry)
    return inquiry


@router.get('/admin/inquiries/{inquiry_id}')
def inquiry_detail(inquiry_id: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    inquiry=db.get(ContactInquiry,inquiry_id)
    if inquiry is None:raise HTTPException(404,'Contact enquiry not found')
    history=db.scalars(select(AuditEvent).where(AuditEvent.entity_id==inquiry.id).order_by(AuditEvent.created_at.desc()).limit(50)).all()
    deliveries=db.scalars(select(Outbox).where(Outbox.payload['inquiry_id'].as_string()==inquiry.id)).all()
    return {'inquiry':inquiry,'status_history':[{'action':e.action,'at':e.created_at,'admin_id':e.actor_user_id,'details':e.metadata_json} for e in history],
            'email_notifications':[{'event':e.event,'status':e.status,'delivery_status':e.delivery_status,'attempts':e.attempts} for e in deliveries]}


@router.post('/admin/inquiries/{inquiry_id}/notes', response_model=ContactInquiryOut)
def add_note(inquiry_id: str, payload: ContactInquiryNote, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    inquiry=db.get(ContactInquiry,inquiry_id)
    if inquiry is None:raise HTTPException(404,'Contact enquiry not found')
    stamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    entry=f'[{stamp}] {admin.id}: {payload.note}'
    inquiry.internal_notes=f'{inquiry.internal_notes}\n{entry}' if inquiry.internal_notes else entry
    audit(db,admin,'contact.inquiry_note_added',entity_id=inquiry.id)
    db.commit();db.refresh(inquiry)
    return inquiry
