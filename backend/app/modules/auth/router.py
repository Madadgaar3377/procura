import hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

from app.response_contracts import TokenPair, UserOut

log = logging.getLogger("uvicorn.error")
from hashlib import sha256
from typing import Literal

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.identity import normalize_email, normalize_saudi_phone, validate_password
from app.identity_columns import email_identity
from app.models import AccountPreference, EmailOTP, PhoneOTP, RefreshSession, User
from app.modules.notifications.auth_dispatch import schedule_auth_deliveries
from app.modules.notifications.service import queue_delivery as _queue_delivery
from app.rate_limit import enforce_rate_limit
from app.schemas import Login, UserRegister
from app.security import (
    as_utc,
    audit,
    check_password,
    credential_stamp,
    current_user,
    digest,
    password_hash,
    refresh_value,
    token_for,
)
from app.serializers import record

router=APIRouter(prefix='/auth',tags=['Authentication'])
_DUMMY_PASSWORD_HASH=password_hash(secrets.token_urlsafe(32))

def norm_phone(value, country=None, *, sms=False):
    try:
        return normalize_saudi_phone(value, country, sms=sms)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc

def norm_email(value: str) -> str:
    try:
        return normalize_email(value)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc

def otp_digest(purpose: str, recipient: str, code: str) -> str:
    material=f'{purpose}:{recipient}:{code}'.encode()
    return hmac.new(get_settings().jwt_secret.encode('utf-8'),material,sha256).hexdigest()

def recipient_limit(request, *, scope, identity, limit, window_seconds):
    # Recipient/account quotas must be shared across requester addresses.
    shared=Request({**request.scope,'client':('shared',0)})
    enforce_rate_limit(shared,scope=scope,identity=identity,limit=limit,window_seconds=window_seconds)


def queue_delivery(db, **kwargs):
    correlation_id=kwargs.pop('correlation_id',None)
    user=db.get(User,kwargs['user_id'])
    recipient=(kwargs.get('context') or {}).get('recipient') or (user.phone_e164 if kwargs['channel']=='sms' else user.email)
    if kwargs['channel']=='sms':
        # Ownership challenges are Saudi-only. Preserve security alerts to the
        # previously verified destination during an approved legacy transition.
        if kwargs['template']=='phone_changed':
            from app.identity import normalize_phone as legacy_phone
            recipient=legacy_phone(recipient, sms=True)
        else:
            recipient=norm_phone(recipient)
    else:
        recipient=norm_email(recipient)
    kwargs['context']={**(kwargs.get('context') or {}),'recipient':recipient}
    if correlation_id:kwargs['context']['correlation_id']=correlation_id
    return _queue_delivery(db,**kwargs)



def saudi_sms_eligible(user):
    if not user.phone_verified:
        return False
    try:
        normalize_saudi_phone(user.phone_e164)
        return True
    except ValueError:
        return False


def access_pair(db, user, family_id: str | None = None):
    raw=refresh_value()
    family_id=family_id or secrets.token_hex(18)
    expiry=datetime.now(UTC)+timedelta(days=get_settings().refresh_days)
    db.add(RefreshSession(user_id=user.id,token_hash=digest(raw),family_id=family_id,expires_at=expiry))
    db.commit()
    return {'access_token':token_for(user,family_id),'refresh_token':raw,'token_type':'bearer'}

def registration_conflict(user,username,phone,email):
    if user:
        try:
            if user.phone_e164==phone:return 'This phone number is already registered'
        except ValueError:
            pass
    if user and user.username==username:return 'This username is already registered. Choose another username or sign in.'
    if user and email and (user.email or '').casefold()==email:
        return 'This email address is already registered. Sign in to your existing account or use another email address.'
    return 'A username, phone number or email is already registered. Refresh and check your details.'


@router.post('/register',status_code=201)
def register(payload:UserRegister,request:Request,db:Session=Depends(get_db)):
    enforce_rate_limit(request,scope='registration-ip',identity='all',limit=20,window_seconds=900)
    username=payload.username.casefold().strip()
    phone=norm_phone(payload.phone, payload.phone_country, sms=True)
    email=norm_email(payload.email) if payload.email else None
    conditions=(func.lower(func.trim(User.username))==username)|(User.phone_e164==phone)
    if email:conditions=conditions|(email_identity(User.email)==email)|(func.lower(func.trim(User.username))==email)
    duplicate=db.scalar(select(User).where(conditions).order_by(case((User.phone==phone,0),else_=1)).limit(1))
    if duplicate:
        raise HTTPException(409,registration_conflict(duplicate,username,phone,email))
    user=User(username=username,phone=phone,email=email,password_hash=password_hash(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        duplicate=db.scalar(select(User).where(conditions).order_by(case((User.phone==phone,0),else_=1)).limit(1))
        raise HTTPException(409,registration_conflict(duplicate,username,phone,email)) from exc
    return {'id':user.id,'username':user.username,'phone_verified':False,'message':'Verify phone before login'}

@router.post('/otp/request')
def otp_request(phone:str, request:Request, background_tasks:BackgroundTasks, db:Session=Depends(get_db), phone_country:str|None=None):
    phone=norm_phone(phone, phone_country, sms=True)
    enforce_rate_limit(request,scope='phone-otp-ip',identity='all',limit=20,window_seconds=900)
    recipient_limit(request,scope='phone-otp-recipient',identity=phone,limit=3,window_seconds=600)
    recipient_limit(request,scope='phone-otp-cooldown',identity=phone,limit=1,window_seconds=get_settings().otp_resend_seconds)
    matches=db.scalars(select(User).where(User.phone_e164==phone).limit(2).with_for_update()).all()
    user=matches[0] if len(matches)==1 else None
    log.info('Signup OTP eligibility: account_found=%s phone_already_verified=%s', bool(user), bool(user and user.phone_verified))
    if user and not user.phone_verified:
        now=datetime.now(UTC)
        code=f'{secrets.randbelow(1000000):06d}'
        db.execute(update(PhoneOTP).where(PhoneOTP.phone==phone,PhoneOTP.purpose=='registration',PhoneOTP.used==False).values(used=True))
        otp=PhoneOTP(phone=phone,purpose='registration',code_hash=otp_digest('registration',phone,code),expires_at=now+timedelta(minutes=5))
        db.add(otp);db.flush()
        jobs=[queue_delivery(db,user_id=user.id,channel='sms',template='phone_otp',secret=code,context={'otp_id':otp.id},dedupe_key=f'phone-otp:{otp.id}').id]
        if user.email and not user.email_verified:
            email=norm_email(user.email)
            # Each contact requires its own proof; email must not disclose the SMS challenge.
            email_code=f'{secrets.randbelow(1000000):06d}'
            db.execute(update(EmailOTP).where(EmailOTP.email==email,EmailOTP.purpose=='verification',EmailOTP.used==False).values(used=True))
            email_otp=EmailOTP(email=email,purpose='verification',code_hash=otp_digest('verification',email,email_code),expires_at=otp.expires_at)
            db.add(email_otp);db.flush()
            jobs.append(queue_delivery(db,user_id=user.id,channel='email',template='email_otp',secret=email_code,context={'otp_id':email_otp.id},dedupe_key=f'email-otp:{email_otp.id}').id)
        db.commit()
        schedule_auth_deliveries(background_tasks,jobs)
        return {'message':'If eligible, a verification code is queued for delivery'}
    return {'message':'If eligible, a verification code is queued for delivery'}

class VerifyOTP(BaseModel):
    phone_country: str | None = Field(default=None,min_length=2,max_length=2)
    phone:str=Field(min_length=1,max_length=80)
    code:str=Field(pattern=r'^[0-9]{6}$')

@router.post('/otp/verify')
def otp_verify(payload:VerifyOTP,request:Request,db:Session=Depends(get_db)):
    phone=norm_phone(payload.phone, payload.phone_country)
    enforce_rate_limit(request,scope='phone-otp-verify-ip',identity='all',limit=30,window_seconds=900)
    recipient_limit(request,scope='phone-otp-verify-recipient',identity=phone,limit=10,window_seconds=900)
    matches=db.scalars(select(User).where(User.phone_e164==phone).limit(2)).all()
    user=matches[0] if len(matches)==1 else None
    otp=db.scalar(select(PhoneOTP).where(PhoneOTP.phone==phone,PhoneOTP.purpose=='registration',PhoneOTP.used==False).order_by(PhoneOTP.created_at.desc()).with_for_update())
    now=datetime.now(UTC)
    if not user or not otp or otp.attempts>=5 or as_utc(otp.expires_at)<now:
        raise HTTPException(400,'Invalid or expired OTP')
    otp.attempts+=1
    if not hmac.compare_digest(otp.code_hash,otp_digest('registration',phone,payload.code)):
        db.commit();raise HTTPException(400,'Invalid or expired OTP')
    otp.used=True;user.phone_verified=True;audit(db,user,'auth.phone_verified',entity_id=user.id);db.commit()
    return {'verified':True}

@router.post('/login',response_model=TokenPair)
def login(payload:Login,request:Request,db:Session=Depends(get_db)):
    key=payload.identifier.strip()
    enforce_rate_limit(request,scope='login-ip',identity='all',limit=30,window_seconds=300)
    enforce_rate_limit(request,scope='login-identifier',identity=key.casefold(),limit=10,window_seconds=300)
    try:
        normalized=normalize_email(key) if '@' in key else normalize_saudi_phone(key, payload.phone_country)
    except ValueError:
        normalized='invalid:'+digest(key.casefold())
    recipient_limit(request,scope='login-normalized-identifier',identity=normalized,limit=10,window_seconds=300)
    condition=email_identity(User.email)==normalized if '@' in normalized else User.phone_e164==normalized
    # Serialize credential check/session creation with reset and admin changes.
    matches=db.scalars(select(User).where(condition).limit(2).with_for_update()).all()
    user=matches[0] if len(matches)==1 else None
    valid=check_password(user.password_hash if user else _DUMMY_PASSWORD_HASH,payload.password)
    if not user or not valid or not user.is_active:
        audit(db,None,'auth.login_failed',identifier_digest=digest(normalized));db.commit()
        raise HTTPException(401,'Invalid credentials or account not verified')
    if not user.phone_verified:
        # Reveal the verification destination only after valid credentials.
        raise HTTPException(403,{'code':'phone_verification_required','phone':user.phone,'email':user.email})
    audit(db,user,'auth.login_succeeded',entity_id=user.id)
    return access_pair(db,user)

class RefreshRequest(BaseModel):
    refresh_token:str=Field(min_length=32,max_length=200)

@router.post('/refresh',response_model=TokenPair)
def refresh(payload:RefreshRequest,db:Session=Depends(get_db)):
    # Lock user before session, matching password reset/admin lock order.
    candidate=db.scalar(select(RefreshSession).where(RefreshSession.token_hash==digest(payload.refresh_token)))
    if not candidate:raise HTTPException(401,'Invalid refresh session')
    user=db.scalar(select(User).where(User.id==candidate.user_id).with_for_update().execution_options(populate_existing=True))
    session=db.scalar(select(RefreshSession).where(RefreshSession.id==candidate.id).with_for_update().execution_options(populate_existing=True))
    if not session:
        raise HTTPException(401,'Invalid refresh session')
    if session.revoked:
        if session.family_id:
            db.execute(update(RefreshSession).where(RefreshSession.family_id==session.family_id).values(revoked=True))
            prior_user=db.get(User,session.user_id)
            if prior_user:audit(db,prior_user,'auth.refresh_reuse_detected',entity_id=prior_user.id)
            db.commit()
        raise HTTPException(401,'Invalid refresh session')
    if as_utc(session.expires_at)<datetime.now(UTC):
        raise HTTPException(401,'Invalid refresh session')
    if not user or not user.is_active: raise HTTPException(401,'Invalid session')
    session.revoked=True
    raw=refresh_value()
    family_id=session.family_id or session.id
    db.add(RefreshSession(user_id=user.id,token_hash=digest(raw),family_id=family_id,expires_at=datetime.now(UTC)+timedelta(days=get_settings().refresh_days)))
    db.commit()
    return {'access_token':token_for(user,family_id),'refresh_token':raw,'token_type':'bearer'}

@router.post('/logout')
def logout(payload:RefreshRequest,user:User=Depends(current_user),db:Session=Depends(get_db)):
    session=db.scalar(select(RefreshSession).where(RefreshSession.token_hash==digest(payload.refresh_token),RefreshSession.user_id==user.id))
    if session:
        if session.family_id:
            db.execute(update(RefreshSession).where(RefreshSession.family_id==session.family_id).values(revoked=True))
        else:
            session.revoked=True
        db.commit()
    return {'ok':True}

@router.get('/me',response_model=UserOut)
def me(user:User=Depends(current_user)):
    data=record(user)
    data.pop('password_hash',None)
    return data


class EmailOTPVerify(BaseModel):
    code:str=Field(pattern=r'^[0-9]{6}$')


@router.post('/email/verification/request',status_code=202)
def request_email_verification(request:Request,background_tasks:BackgroundTasks,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if not user.email:
        raise HTTPException(422,'Add an email address before requesting verification')
    email=norm_email(user.email)
    enforce_rate_limit(request,scope='email-otp-ip',identity='all',limit=10,window_seconds=900)
    recipient_limit(request,scope='email-otp-recipient',identity=email,limit=3,window_seconds=600)
    recipient_limit(request,scope='email-otp-cooldown',identity=email,limit=1,window_seconds=get_settings().otp_resend_seconds)
    if user.email_verified:
        return {'message':'If eligible, a verification code will be sent'}
    db.execute(update(EmailOTP).where(EmailOTP.email==email,EmailOTP.purpose=='verification',EmailOTP.used==False).values(used=True))
    code=f'{secrets.randbelow(1000000):06d}'
    otp=EmailOTP(email=email,purpose='verification',code_hash=otp_digest('verification',email,code),expires_at=datetime.now(UTC)+timedelta(minutes=10))
    db.add(otp);db.flush()
    job=queue_delivery(db,user_id=user.id,channel='email',template='email_otp',secret=code,context={'otp_id':otp.id},dedupe_key=f'email-otp:{otp.id}')
    db.commit()
    schedule_auth_deliveries(background_tasks,[job.id])
    return {'message':'If eligible, a verification code will be sent'}


@router.post('/email/verification/confirm')
def confirm_email_verification(payload:EmailOTPVerify,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if not user.email:
        raise HTTPException(400,'No email address is configured')
    email=norm_email(user.email)
    enforce_rate_limit(request,scope='email-otp-verify-ip',identity='all',limit=20,window_seconds=900)
    recipient_limit(request,scope='email-otp-verify-recipient',identity=email,limit=10,window_seconds=900)
    otp=db.scalar(select(EmailOTP).where(EmailOTP.email==email,EmailOTP.purpose=='verification',EmailOTP.used==False).order_by(EmailOTP.created_at.desc()).with_for_update())
    now=datetime.now(UTC)
    if not otp or otp.attempts>=5 or as_utc(otp.expires_at)<now:
        raise HTTPException(400,'Invalid or expired verification code')
    otp.attempts+=1
    if not hmac.compare_digest(otp.code_hash,otp_digest('verification',email,payload.code)):
        db.commit();raise HTTPException(400,'Invalid or expired verification code')
    otp.used=True;user.email_verified=True;audit(db,user,'auth.email_verified',entity_id=user.id);db.commit()
    return {'verified':True}


class PasswordRecoveryRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    identifier: str | None = Field(default=None,min_length=3,max_length=200)
    email: str | None = Field(default=None,max_length=200)
    phone: str | None = Field(default=None,max_length=80)
    phone_country: str | None = Field(default=None,min_length=2,max_length=2)

    def recipient(self):
        supplied=[value for value in (self.identifier,self.email,self.phone) if value]
        if len(supplied)!=1:raise HTTPException(422,'Provide one email address or phone number')
        value=supplied[0].strip()
        return norm_email(value) if '@' in value else norm_phone(value, self.phone_country)

class RecoveryCode(PasswordRecoveryRequest):
    code: str = Field(pattern=r'^[0-9]{6}$')

class RecoveryVerified(BaseModel):
    verified: bool
    reset_token: str


class RecoveryLinkCheck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reset_token: str = Field(pattern=r'^[a-f0-9]{64}$')

class PasswordReset(PasswordRecoveryRequest):
    code: str | None = Field(default=None,pattern=r'^[0-9]{6}$')
    reset_token: str | None = Field(default=None,max_length=2000)
    new_password: str = Field(min_length=6,max_length=128)
    confirm_password: str | None = Field(default=None,max_length=128)
    _password = field_validator('new_password')(validate_password)


def recovery_user(db, recipient):
    condition=email_identity(User.email)==recipient if '@' in recipient else User.phone_e164==recipient
    condition=condition & ((User.email_verified==True) | (User.phone_verified==True))
    matches=db.scalars(select(User).where(condition,User.is_active==True).limit(2).with_for_update().execution_options(populate_existing=True)).all()
    return matches[0] if len(matches)==1 else None


def recovery_key(user):
    return norm_email(user.email) if user.email and user.email_verified else norm_phone(user.phone)


def trusted_recovery_key(user):
    """A verified reset session must still have its original trusted destination."""
    if user.email and user.email_verified:
        return norm_email(user.email)
    if user.phone_verified:
        return norm_phone(user.phone)
    return None


def checked_recovery(db, user, code):
    key=recovery_key(user) if user else ''
    otp=db.scalar(select(EmailOTP).where(EmailOTP.user_id== (user.id if user else ''),EmailOTP.email==key,EmailOTP.purpose=='password_reset',EmailOTP.used==False).order_by(EmailOTP.created_at.desc()).with_for_update().execution_options(populate_existing=True))
    now=datetime.now(UTC)
    if not valid_recovery(otp,user,now) or otp.verified_at is not None:
        raise HTTPException(400,'Invalid or expired recovery code')
    if not hmac.compare_digest(otp.code_hash,otp_digest('password_reset',user.id,code)):
        otp.attempts+=1
        db.commit();raise HTTPException(400,'Invalid or expired recovery code')
    return otp


RESET_PURPOSES=('password_reset','password_verified','password_reset_link')
RESET_ERROR='This password reset link has expired or has already been used. Request a new link.'


def valid_recovery(otp,user,now):
    return bool(user and otp and otp.user_id==user.id and otp.purpose in RESET_PURPOSES
                and not otp.used and not otp.consumed_at and not otp.revoked_at
                and otp.attempts<5 and as_utc(otp.expires_at)>now
                and otp.email==trusted_recovery_key(user)
                and hmac.compare_digest(otp.credential_hash or '',credential_stamp(user)))


def revoke_recovery(db,user):
    db.execute(update(EmailOTP).where(EmailOTP.user_id==user.id,
        EmailOTP.purpose.in_(RESET_PURPOSES),EmailOTP.used==False).values(used=True,revoked_at=datetime.now(UTC)))


def consume_recovery(db,user,otp):
    # Defense in depth: the database, rather than an ORM snapshot, decides
    # whether this request can be consumed. Password and claim commit together.
    now=datetime.now(UTC)
    claimed=db.execute(update(EmailOTP).where(EmailOTP.id==otp.id,
        EmailOTP.user_id==user.id,EmailOTP.purpose==otp.purpose,
        EmailOTP.used==False,EmailOTP.consumed_at.is_(None),EmailOTP.revoked_at.is_(None),
        EmailOTP.expires_at>now,EmailOTP.attempts<5,
        EmailOTP.credential_hash==credential_stamp(user)).values(used=True,consumed_at=now).execution_options(synchronize_session='fetch'))
    if claimed.rowcount!=1:
        raise HTTPException(400,RESET_ERROR)


def checked_recovery_link(db,token):
    candidate=db.scalar(select(EmailOTP).where(EmailOTP.code_hash==digest(token),EmailOTP.purpose=='password_reset_link'))
    user=db.scalar(select(User).where(User.id==candidate.user_id,User.is_active==True).with_for_update().execution_options(populate_existing=True)) if candidate else None
    otp=db.scalar(select(EmailOTP).where(EmailOTP.id==candidate.id).with_for_update().execution_options(populate_existing=True)) if candidate else None
    if not valid_recovery(otp,user,datetime.now(UTC)):
        raise HTTPException(400,RESET_ERROR)
    return user,otp


@router.post('/password/forgot',status_code=202)
def password_forgot(payload:PasswordRecoveryRequest,request:Request,background_tasks:BackgroundTasks,db:Session=Depends(get_db)):
    recipient=payload.recipient()
    enforce_rate_limit(request,scope='password-reset-ip',identity='all',limit=10,window_seconds=900)
    # A failed database lookup must not consume the recipient's resend allowance.
    # The IP limit above still bounds requests during a database outage.
    user=recovery_user(db,recipient)
    recipient_limit(request,scope='password-reset-recipient',identity=recipient,limit=3,window_seconds=900)
    recipient_limit(request,scope='password-reset-cooldown',identity=recipient,limit=1,window_seconds=get_settings().otp_resend_seconds)
    response={'message':'If an eligible account exists, a password recovery code will be sent'}
    if not user:
        log.warning("Password recovery: no unique active account with a verified recovery contact; no delivery queued request_id=%s", request.state.correlation_id)
        return response
    key=recovery_key(user)
    try:
        recipient_limit(request,scope='password-reset-account',identity=user.id,limit=3,window_seconds=900)
    except HTTPException as exc:
        if exc.status_code==429:return response
        raise
    try:
        recipient_limit(request,scope='password-reset-account-cooldown',identity=user.id,limit=1,window_seconds=get_settings().otp_resend_seconds)
    except HTTPException as exc:
        if exc.status_code==429:return response
        raise
    revoke_recovery(db,user)
    # Identifier+code is the legacy API contract (no challenge ID). Avoid
    # reissuing a historical code that could otherwise make an old OTP valid
    # against a newer request. The account lock serializes issuance.
    for _ in range(10):
        code=f'{secrets.randbelow(1000000):06d}'
        code_hash=otp_digest('password_reset',user.id,code)
        if not db.scalar(select(EmailOTP.id).where(EmailOTP.user_id==user.id,EmailOTP.code_hash==code_hash).limit(1)):
            break
    else:
        raise HTTPException(503,'Password recovery is temporarily unavailable')
    otp=EmailOTP(user_id=user.id,credential_hash=credential_stamp(user),email=key,purpose='password_reset',code_hash=code_hash,expires_at=datetime.now(UTC)+timedelta(minutes=min(15,get_settings().recovery_minutes)))
    db.add(otp);db.flush()
    link_token=secrets.token_hex(32)
    db.add(EmailOTP(user_id=user.id,credential_hash=credential_stamp(user),email=key,purpose='password_reset_link',code_hash=digest(link_token),expires_at=otp.expires_at))
    channels=[]
    if saudi_sms_eligible(user):channels.append('sms')
    if user.email and user.email_verified:channels.append('email')
    jobs=[]
    for channel in channels:
        # Both delivery secrets are encrypted by the existing outbox service.
        secret=json.dumps({'code':code,'link_token':link_token})
        jobs.append(queue_delivery(db,user_id=user.id,channel=channel,template='password_reset',secret=secret,context={'otp_id':otp.id,'recipient':user.phone if channel=='sms' else user.email},dedupe_key=f'password-reset:{otp.id}:{channel}',correlation_id=request.state.correlation_id).id)
    audit(db,user,'auth.password_recovery_requested',entity_id=user.id)
    db.commit()
    schedule_auth_deliveries(background_tasks,jobs)
    return response


@router.post('/password/verify',response_model=RecoveryVerified)
def password_verify(payload:RecoveryCode | RecoveryLinkCheck,request:Request,db:Session=Depends(get_db)):
    enforce_rate_limit(request,scope='password-reset-verify-ip',identity='all',limit=20,window_seconds=900)
    if isinstance(payload,RecoveryLinkCheck):
        user,otp=checked_recovery_link(db,payload.reset_token)
        recipient_limit(request,scope='password-reset-verify-recipient',identity=user.id,limit=10,window_seconds=900)
        # Checking a link never mints another authorization or consumes it.
        return {'verified':True,'reset_token':payload.reset_token}
    recipient=payload.recipient()
    user=recovery_user(db,recipient)
    recipient_limit(request,scope='password-reset-verify-recipient',identity=user.id if user else recipient,limit=10,window_seconds=900)
    otp=checked_recovery(db,user,payload.code)
    now=datetime.now(UTC)
    token=jwt.encode({'sub':user.id,'otp':otp.id,'stamp':credential_stamp(user),'typ':'password_reset','iat':now,'exp':min(as_utc(otp.expires_at),now+timedelta(minutes=5))},get_settings().jwt_secret,algorithm='HS256')
    otp.purpose='password_verified'
    otp.verified_at=now
    db.commit()
    return {'verified':True,'reset_token':token}


@router.post('/password/reset')
def password_reset(payload:PasswordReset,request:Request,background_tasks:BackgroundTasks,db:Session=Depends(get_db)):
    enforce_rate_limit(request,scope='password-reset-verify-ip',identity='all',limit=20,window_seconds=900)
    if payload.confirm_password is not None and payload.confirm_password!=payload.new_password:
        raise HTTPException(422,'Your passwords do not match')
    if payload.reset_token and len(payload.reset_token)==64 and all(c in '0123456789abcdef' for c in payload.reset_token):
        user,otp=checked_recovery_link(db,payload.reset_token)
    elif payload.reset_token:
        try:
            claims=jwt.decode(payload.reset_token,get_settings().jwt_secret,algorithms=['HS256'],options={'require':['sub','otp','stamp','typ','exp','iat']})
            if claims['typ']!='password_reset':raise jwt.InvalidTokenError()
        except jwt.PyJWTError as exc:
            raise HTTPException(400,RESET_ERROR) from exc
        user=db.scalar(select(User).where(User.id==claims['sub'],User.is_active==True).with_for_update().execution_options(populate_existing=True))
        otp=db.scalar(select(EmailOTP).where(EmailOTP.id==claims['otp']).with_for_update().execution_options(populate_existing=True))
        if not valid_recovery(otp,user,datetime.now(UTC)) or otp.purpose!='password_verified' or otp.verified_at is None or not isinstance(claims['stamp'],str) or not hmac.compare_digest(claims['stamp'],credential_stamp(user)):
            raise HTTPException(400,RESET_ERROR)
    else:
        recipient=payload.recipient()
        user=recovery_user(db,recipient)
        recipient_limit(request,scope='password-reset-verify-recipient',identity=user.id if user else recipient,limit=10,window_seconds=900)
        if not payload.code:raise HTTPException(422,'A verified reset session or recovery code is required')
        otp=checked_recovery(db,user,payload.code)
    if payload.reset_token:
        recipient_limit(request,scope='password-reset-verify-recipient',identity=user.id,limit=10,window_seconds=900)
    consume_recovery(db,user,otp)
    user.password_hash=password_hash(payload.new_password)
    revoke_recovery(db,user)
    db.execute(update(RefreshSession).where(RefreshSession.user_id==user.id).values(revoked=True))
    audit(db,user,'auth.password_reset',entity_id=user.id)
    jobs=[]
    for channel,verified in [('email',user.email and user.email_verified),('sms',saudi_sms_eligible(user))]:
        if verified:
            jobs.append(queue_delivery(db,user_id=user.id,channel=channel,template='password_changed',dedupe_key=f'password-changed:{otp.id}:{channel}').id)
    db.commit()
    schedule_auth_deliveries(background_tasks,jobs)
    return {'password_reset':True}


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)
    confirm_password: str = Field(min_length=6, max_length=128)
    _password = field_validator('new_password')(validate_password)


@router.post('/password/change')
def change_password(payload:PasswordChange, request:Request, background_tasks:BackgroundTasks,
                    response:Response, user:User=Depends(current_user), db:Session=Depends(get_db)):
    recipient_limit(request, scope='password-change', identity=user.id, limit=5, window_seconds=900)
    locked_user=db.scalar(select(User).where(User.id==user.id).with_for_update().execution_options(populate_existing=True))
    if locked_user is None or not locked_user.is_active:raise HTTPException(401,'Authentication required')
    user=locked_user
    if not check_password(user.password_hash, payload.current_password):
        audit(db,user,'auth.password_change_failed',entity_id=user.id);db.commit()
        raise HTTPException(400,'Current password is incorrect')
    if payload.new_password!=payload.confirm_password:
        raise HTTPException(422,'Your passwords do not match')
    if check_password(user.password_hash,payload.new_password):
        raise HTTPException(422,'New password must differ from current password')
    user.password_hash=password_hash(payload.new_password)
    revoke_recovery(db,user)
    db.execute(update(RefreshSession).where(RefreshSession.user_id==user.id).values(revoked=True))
    for model, identity in [(EmailOTP, EmailOTP.email==recovery_key(user)), (PhoneOTP, PhoneOTP.phone==user.phone_e164)]:
        db.execute(update(model).where(identity).values(used=True))
    audit(db,user,'auth.password_changed',entity_id=user.id)
    jobs=[]
    change_id=secrets.token_hex(16)
    for channel,verified in [('email',bool(user.email and user.email_verified)),('sms',saudi_sms_eligible(user))]:
        if verified:
            jobs.append(queue_delivery(db,user_id=user.id,channel=channel,template='password_changed',
                context={'changed_at':datetime.now(UTC).isoformat()},
                dedupe_key=f'password-changed:{change_id}:{channel}').id)
    db.commit()
    for name in ('procura_access','procura_refresh'):response.delete_cookie(name,path='/')
    schedule_auth_deliveries(background_tasks,jobs)
    return {'password_changed':True,'sign_in_required':True,'session_policy':'all_sessions_revoked'}


def current_family(request:Request):
    raw=request.headers.get('Authorization','').removeprefix('Bearer ') or request.cookies.get('procura_access','')
    from app.security import decode_access_token
    try:return decode_access_token(raw).get('sid')
    except jwt.PyJWTError:return None


@router.post('/sessions/revoke-others')
def revoke_other_sessions(request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    family=current_family(request)
    if not family:raise HTTPException(409,'Sign in again to identify the current browser session')
    db.execute(update(RefreshSession).where(RefreshSession.user_id==user.id,
        (RefreshSession.family_id!=family)|(RefreshSession.family_id.is_(None))).values(revoked=True))
    audit(db,user,'auth.other_sessions_revoked',entity_id=user.id);db.commit()
    return {'ok':True}


EVENT_PREFERENCES={'rfq_invitation','deadline_reminder','quotation_submitted','quotation_status_changed','supplier_matching_updated'}

class AccountPreferencesIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    events:dict[str,bool]|None=None
    in_app_enabled:bool|None=None


@router.get('/account-preferences')
def account_preferences(user:User=Depends(current_user),db:Session=Depends(get_db)):
    row=db.get(AccountPreference,user.id)
    settings=get_settings()
    return {'events':{key:(row.events.get(key,True) if row else True) for key in sorted(EVENT_PREFERENCES)},
        'in_app_enabled':row.in_app_enabled if row else True,
        'email_available':bool(not settings.mock_notifications and settings.smtp_host and settings.smtp_username and settings.smtp_password),
        'sms_available':bool(not settings.mock_notifications and settings.sms_gateway_enabled and settings.sms_gateway_mode=='live' and settings.sms_gate_username and settings.sms_gate_password)}


@router.patch('/account-preferences')
def save_account_preferences(payload:AccountPreferencesIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if payload.events and set(payload.events)-EVENT_PREFERENCES:raise HTTPException(422,'Unknown notification event')
    # Lock the account to serialize first-time preference creation and updates.
    db.scalar(select(User.id).where(User.id==user.id).with_for_update())
    row=db.get(AccountPreference,user.id)
    if not row:row=AccountPreference(user_id=user.id,events={});db.add(row)
    if payload.events is not None:row.events={**(row.events or {}),**payload.events}
    if payload.in_app_enabled is not None:row.in_app_enabled=payload.in_app_enabled
    audit(db,user,'notification.account_preferences_updated',entity_id=user.id)
    db.commit()
    return account_preferences(user,db)


class NotificationPreferences(BaseModel):
    language:Literal['en','ar']|None=None
    sms_enabled:bool|None=None
    email_enabled:bool|None=None


@router.patch('/notification-preferences')
def update_notification_preferences(payload:NotificationPreferences,user:User=Depends(current_user),db:Session=Depends(get_db)):
    values=payload.model_dump(exclude_unset=True,exclude_none=True)
    if not values:
        raise HTTPException(422,'At least one notification preference is required')
    if values.get('sms_enabled') is True and not user.phone_verified:
        raise HTTPException(409,'Verify your phone before enabling SMS notifications')
    if values.get('email_enabled') is True and (not user.email or not user.email_verified):
        raise HTTPException(409,'Verify your email before enabling email notifications')
    if 'language' in values:user.notification_language=values['language']
    if 'sms_enabled' in values:user.sms_notifications_enabled=values['sms_enabled']
    if 'email_enabled' in values:user.email_notifications_enabled=values['email_enabled']
    audit(db,user,'notification.preferences_updated',entity_id=user.id,**values)
    db.commit()
    return {'sms_enabled':user.sms_notifications_enabled,'email_enabled':user.email_notifications_enabled,'language':user.notification_language}

class SessionOut(BaseModel):
    id:str
    created_at:datetime
    expires_at:datetime
    current:bool=False

@router.get('/sessions',response_model=list[SessionOut])
def sessions(request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(RefreshSession).where(RefreshSession.user_id==user.id,RefreshSession.revoked==False,RefreshSession.expires_at>datetime.now(UTC)).order_by(RefreshSession.created_at.desc()).limit(100)).all()
    return [SessionOut(id=x.id,created_at=x.created_at,expires_at=x.expires_at,current=bool(x.family_id and x.family_id==current_family(request))) for x in rows]

@router.delete('/sessions/{session_id}')
def revoke_session(session_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    item=db.get(RefreshSession,session_id)
    if not item or item.user_id!=user.id:raise HTTPException(404,'Session not found')
    db.execute(update(RefreshSession).where(RefreshSession.user_id==user.id,RefreshSession.family_id==item.family_id).values(revoked=True)) if item.family_id else setattr(item,'revoked',True)
    audit(db,user,'auth.session_revoked',entity_id=item.id);db.commit()
    return {'ok':True}


class BrowserSessionOut(BaseModel):
    authenticated: bool


def set_browser_cookies(response:Response,pair):
    settings=get_settings()
    for name,value,seconds in [('procura_access',pair['access_token'],settings.token_minutes*60),('procura_refresh',pair['refresh_token'],settings.refresh_days*86400)]:
        response.set_cookie(name,value,max_age=seconds,httponly=True,secure=settings.is_production,samesite='lax',path='/')


@router.post('/browser/login',response_model=BrowserSessionOut)
def browser_login(payload:Login,request:Request,response:Response,db:Session=Depends(get_db)):
    set_browser_cookies(response,login(payload,request,db))
    return {'authenticated':True}


@router.post('/browser/refresh',response_model=BrowserSessionOut)
def browser_refresh(request:Request,response:Response,db:Session=Depends(get_db)):
    raw=request.cookies.get('procura_refresh')
    if not raw:raise HTTPException(401,'Authentication required')
    set_browser_cookies(response,refresh(RefreshRequest(refresh_token=raw),db))
    return {'authenticated':True}


@router.post('/browser/logout')
def browser_logout(request:Request,response:Response,db:Session=Depends(get_db)):
    raw=request.cookies.get('procura_refresh')
    if raw:
        session=db.scalar(select(RefreshSession).where(RefreshSession.token_hash==digest(raw)).with_for_update())
        if session:
            db.execute(update(RefreshSession).where(RefreshSession.user_id==session.user_id,RefreshSession.family_id==session.family_id).values(revoked=True))
            db.commit()
    for name in ('procura_access','procura_refresh'):response.delete_cookie(name,path='/')
    return {'ok':True}
