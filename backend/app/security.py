from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.config import get_settings
from app.database import get_db
from app.models import User, Member, AuditEvent, Organization, RefreshSession

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
bearer = HTTPBearer(auto_error=False)

def password_hash(password:str): return hasher.hash(password)
def check_password(hashed:str,password:str):
    try: return hasher.verify(hashed,password)
    except (VerifyMismatchError, VerificationError, InvalidHashError): return False

def credential_stamp(user:User) -> str:
    """Opaque credential version; never put a password hash in a JWT."""
    material=f'access:{user.id}:{user.password_hash}'.encode()
    return hmac.new(get_settings().jwt_secret.encode(),material,sha256).hexdigest()


def token_for(user:User, family_id:str|None=None) -> str:
    now=datetime.now(timezone.utc)
    settings=get_settings()
    claims={'sub':user.id,'iat':now,'exp':now+timedelta(minutes=settings.token_minutes),'typ':'access','cv':credential_stamp(user),
            'iss':settings.jwt_issuer,'aud':settings.jwt_audience}
    if family_id:claims['sid']=family_id
    return jwt.encode(claims, get_settings().jwt_secret, algorithm='HS256')

def refresh_value(): return secrets.token_urlsafe(48)
def digest(value:str): return sha256(value.encode()).hexdigest()


def as_utc(value:datetime) -> datetime:
    """SQLite returns naive UTC; PostgreSQL may return any session offset."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def decode_access_token(raw:str) -> dict:
    """Use one cryptographic context for authentication and session operations."""
    settings=get_settings()
    payload=jwt.decode(raw,settings.jwt_secret,algorithms=['HS256'],issuer=settings.jwt_issuer,audience=settings.jwt_audience,
                       options={'require':['sub','exp','iat','typ','cv','iss','aud']})
    if payload.get('typ') != 'access':
        raise jwt.InvalidTokenError('Wrong token type')
    return payload


def current_user(request:Request,creds:HTTPAuthorizationCredentials|None=Depends(bearer), db:Session=Depends(get_db)) -> User:
    raw=creds.credentials if creds else request.cookies.get('procura_access')
    if not raw:
        raise HTTPException(401,'Authentication required')
    try:
        payload=decode_access_token(raw)
        user=db.get(User, payload['sub'])
        stamp=payload.get('cv')
        if user and (not isinstance(stamp,str) or not hmac.compare_digest(stamp,credential_stamp(user))):
            user=None
        if user and payload.get('sid'):
            active=db.scalar(select(RefreshSession.id).where(
                RefreshSession.user_id==user.id,RefreshSession.family_id==payload['sid'],
                RefreshSession.revoked.is_(False),RefreshSession.expires_at>datetime.now(timezone.utc)).limit(1))
            if not active:user=None
    except (jwt.PyJWTError,KeyError,TypeError): user=None
    if not user or not user.is_active:
        raise HTTPException(401,'Invalid or expired token')
    return user

def admin_user(user:User=Depends(current_user)):
    if not user.is_admin: raise HTTPException(403,'Administrator access required')
    return user

def org_member(db:Session, user:User, org_id:str, roles:set[str]|None=None):
    if user.is_admin: return None
    organization=db.get(Organization,org_id)
    if organization is None or not organization.is_active:
        raise HTTPException(404,'Organization not found')
    membership=db.scalar(select(Member).where(Member.user_id==user.id,Member.organization_id==org_id))
    if membership is None: raise HTTPException(404,'Organization not found')
    if roles and membership.role not in roles: raise HTTPException(403,'Insufficient organization privileges')
    return membership

def audit(db:Session,user:User|None,action:str,org_id:str|None=None,entity_id:str|None=None, **data):
    db.add(AuditEvent(actor_user_id=user.id if user else None,org_id=org_id,action=action,entity_id=entity_id,metadata_json=data))
