"""Authenticated, password-proven replacement of a phone after new-number OTP proof."""
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Outbox, PhoneOTP, User
from app.modules.auth.router import norm_phone, otp_digest, queue_delivery, recipient_limit
from app.modules.notifications.auth_dispatch import schedule_auth_deliveries
from app.rate_limit import enforce_rate_limit
from app.security import as_utc, audit, check_password, credential_stamp, current_user

router = APIRouter(prefix='/auth/phone/change', tags=['Authentication'])


class PhoneChangeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: str = Field(min_length=1, max_length=80)
    phone_country: str | None = Field(default=None, min_length=2, max_length=2)
    current_password: str = Field(min_length=1, max_length=128)


class PhoneChangeConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(min_length=36, max_length=36)
    code: str = Field(pattern=r'^[0-9]{6}$')


class PhoneChangeRequested(BaseModel):
    challenge_id: str
    message: str
    expires_in: int


class PhoneChangeStatus(BaseModel):
    state: Literal['closed', 'expired', 'failed', 'delivered', 'pending']
    message: str


class PhoneChangeVerified(BaseModel):
    verified: bool
    phone: str


@router.get('/{challenge_id}/status',response_model=PhoneChangeStatus)
def change_status(challenge_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    challenge = db.scalar(select(PhoneOTP).where(PhoneOTP.id == challenge_id, PhoneOTP.user_id == user.id,
                                                PhoneOTP.purpose == 'phone_change'))
    if not challenge:
        raise HTTPException(404, 'Verification request not found')
    if challenge.used:
        return {'state': 'closed', 'message': 'This code was used or replaced. Request a new code if needed.'}
    if as_utc(challenge.expires_at) <= datetime.now(UTC):
        return {'state': 'expired', 'message': 'This verification code expired. Request a new code.'}
    job = db.scalar(select(Outbox).where(Outbox.dedupe_key == f'phone-change:{challenge.id}'))
    if job and job.status in {'failed', 'uncertain', 'skipped'}:
        return {'state': 'failed', 'message': 'SMS delivery could not be confirmed. Check international SMS availability or contact support. Your registered phone is unchanged.'}
    if job and job.delivery_status == 'Delivered':
        return {'state': 'delivered', 'message': 'The gateway reported delivery. Enter your code to verify ownership.'}
    return {'state': 'pending', 'message': 'Waiting for SMS delivery. Gateway acceptance does not confirm delivery.'}


@router.post('/request', status_code=202,response_model=PhoneChangeRequested)
def request_change(payload: PhoneChangeRequest, request: Request, background_tasks: BackgroundTasks,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    enforce_rate_limit(request, scope='phone-change-user', identity=user.id, limit=5, window_seconds=900)
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if not check_password(user.password_hash, payload.current_password):
        raise HTTPException(400, 'Current password is incorrect')
    phone = norm_phone(payload.phone, payload.phone_country, sms=True)
    if phone == user.phone_e164:
        raise HTTPException(409, 'Choose a different phone number')
    if db.scalar(select(User.id).where(User.phone_e164 == phone)):
        raise HTTPException(409, 'This phone number is already registered')
    from app.config import get_settings
    recipient_limit(request, scope='phone-change-cooldown', identity=user.id, limit=1,
                    window_seconds=get_settings().otp_resend_seconds)
    recipient_limit(request, scope='phone-change-recipient', identity=phone, limit=3, window_seconds=600)
    db.execute(update(PhoneOTP).where(PhoneOTP.user_id == user.id, PhoneOTP.purpose == 'phone_change',
                                     PhoneOTP.used.is_(False)).values(used=True))
    code = f'{secrets.randbelow(1000000):06d}'
    otp = PhoneOTP(user_id=user.id, phone=phone, previous_phone=user.phone_e164,
                   credential_hash=credential_stamp(user), purpose='phone_change',
                   code_hash=otp_digest('phone_change:'+user.id, phone, code),
                   expires_at=datetime.now(UTC)+timedelta(minutes=5))
    db.add(otp)
    db.flush()
    job = queue_delivery(db, user_id=user.id, channel='sms', template='phone_change_otp', secret=code,
                         context={'otp_id': otp.id, 'recipient': phone, 'phone_change': True},
                         dedupe_key=f'phone-change:{otp.id}')
    audit(db, user, 'auth.phone_change_requested', entity_id=otp.id)
    db.commit()
    schedule_auth_deliveries(background_tasks, [job.id])
    return {'challenge_id': otp.id, 'message': 'Code queued for the new phone. Your registered phone is unchanged.', 'expires_in': 300}


@router.post('/confirm',response_model=PhoneChangeVerified)
def confirm_change(payload: PhoneChangeConfirm, request: Request, background_tasks: BackgroundTasks,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    enforce_rate_limit(request, scope='phone-change-confirm', identity=user.id, limit=10, window_seconds=900)
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    otp = db.scalar(select(PhoneOTP).where(PhoneOTP.id == payload.challenge_id, PhoneOTP.user_id == user.id,
                                          PhoneOTP.purpose == 'phone_change').with_for_update())
    if not otp or otp.used or otp.attempts >= 5 or as_utc(otp.expires_at) <= datetime.now(UTC):
        raise HTTPException(400, 'Invalid or expired verification code')
    if otp.previous_phone != user.phone_e164 or not hmac.compare_digest(otp.credential_hash or '', credential_stamp(user)):
        raise HTTPException(400, 'Account changed. Request a new verification code.')
    otp.attempts += 1
    if not hmac.compare_digest(otp.code_hash, otp_digest('phone_change:'+user.id, otp.phone, payload.code)):
        db.commit()
        raise HTTPException(400, 'Invalid or expired verification code')
    if db.scalar(select(User.id).where(User.phone_e164 == otp.phone, User.id != user.id)):
        raise HTTPException(409, 'This phone number is already registered')
    previous = user.phone_e164
    previous_verified = user.phone_verified
    # Clear the old region before parsing an explicitly international replacement.
    user.phone_country = None
    user.phone = otp.phone
    user.phone_e164 = otp.phone
    user.phone_verified = True
    otp.used = True
    try:
        # Catch uniqueness races before delivery helpers can trigger autoflush.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'This phone number is already registered') from exc
    jobs = []
    # Notify trusted old destinations independently of marketing preferences.
    for channel, recipient in [('sms', previous if previous_verified else None),
                                ('email', user.email if user.email_verified else None)]:
        if recipient:
            job = queue_delivery(db, user_id=user.id, channel=channel, template='phone_changed',
                                 context={'recipient': recipient, 'change_id': otp.id},
                                 dedupe_key=f'phone-changed:{otp.id}:{channel}')
            jobs.append(job.id)
    audit(db, user, 'auth.phone_changed', entity_id=otp.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'This phone number is already registered') from exc
    schedule_auth_deliveries(background_tasks, jobs)
    return {'verified': True, 'phone': user.phone_e164}
