"""Durable public contact intake and administrator review."""
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ContactInquiry, User
from app.rate_limit import enforce_rate_limit
from app.response_contracts import ContactInquiryOut, ResponseModel
from app.schemas import ContactInquiryCreate, ContactInquiryStatus
from app.security import admin_user, audit
from fastapi import HTTPException

router = APIRouter(prefix='/contact', tags=['Public Contact & Support'])


class ContactReceipt(ResponseModel):
    reference: str
    message: str


@router.post('', status_code=201, response_model=ContactReceipt)
def submit(payload: ContactInquiryCreate, request: Request, db: Session = Depends(get_db)):
    address = request.client.host if request.client else 'unknown'
    # FastAPI is reached through the same-origin BFF, so request.client may be
    # the shared Next.js egress address. Key this public limit by normalized,
    # hashed email instead of throttling every visitor behind that proxy.
    enforce_rate_limit(request, scope='public-contact', identity=sha256(payload.email.encode()).hexdigest(), limit=5, window_seconds=3600)
    inquiry = ContactInquiry(
        name=payload.name.strip(), email=payload.email, company=payload.company.strip() if payload.company else None,
        phone=payload.phone.strip() if payload.phone else None, topic=payload.topic, message=payload.message.strip(),
    )
    db.add(inquiry)
    db.commit()
    return {'reference': inquiry.id, 'message': 'Your enquiry has been received. Our team will review it.'}


@router.get('/admin/inquiries', response_model=list[ContactInquiryOut])
def list_inquiries(
    status: Literal['new','reviewed','resolved'] | None = Query(None),
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    query = select(ContactInquiry).order_by(ContactInquiry.created_at.desc()).limit(100)
    if status:
        query = query.where(ContactInquiry.status == status)
    return db.scalars(query).all()


@router.patch('/admin/inquiries/{inquiry_id}', response_model=ContactInquiryOut)
def update_inquiry(
    inquiry_id: str, payload: ContactInquiryStatus,
    admin: User = Depends(admin_user), db: Session = Depends(get_db),
):
    inquiry = db.get(ContactInquiry, inquiry_id)
    if inquiry is None:
        raise HTTPException(404, 'Contact enquiry not found')
    inquiry.status = payload.status
    inquiry.handled_by = admin.id
    inquiry.handled_at = datetime.now(timezone.utc)
    audit(db, admin, 'contact.inquiry_updated', entity_id=inquiry.id, status=payload.status)
    db.commit()
    db.refresh(inquiry)
    return inquiry
