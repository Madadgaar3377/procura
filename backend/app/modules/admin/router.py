from app.search import ListSearch
from app.response_contracts import AuditOut, UserOut
from fastapi import APIRouter,Depends,HTTPException,Query
from sqlalchemy.orm import Session
from sqlalchemy import select,func,update,delete
from app.database import get_db
from app.models import User,Organization,RFQ,Quotation,Invitation,Capability,ImportBatch,AuditEvent,RefreshSession,Member,Notification,CompanyClaim,Verification,MaterialRevision,ContactInquiry,PhoneOTP,EmailOTP,Outbox
from app.security import admin_user,audit,password_hash
from app.serializers import record

router=APIRouter(prefix='/admin',tags=['Administration & Reporting'])

@router.get('/dashboard')
def dashboard(admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    count=lambda model: db.scalar(select(func.count()).select_from(model))
    return {'companies':count(Organization),'verified_suppliers':db.scalar(select(func.count()).select_from(Organization).where(Organization.is_supplier==True,Organization.verified_status=='verified')),'rfqs':count(RFQ),'quotes_submitted':db.scalar(select(func.count()).select_from(Quotation).where(Quotation.status=='submitted')),'invitations':count(Invitation),'supplier_capabilities':count(Capability),'imports':count(ImportBatch)}

@router.get('/audit',response_model=list[AuditOut])
def logs(search:ListSearch=Depends(),admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    return [record(a) for a in db.scalars(search.apply(select(AuditEvent),AuditEvent.action,AuditEvent.org_id,AuditEvent.actor_user_id,AuditEvent.entity_id,order=(AuditEvent.created_at.desc(),AuditEvent.id))).all()]

@router.get('/settings')
def platform_settings(admin:User=Depends(admin_user)):
    from app.config import get_settings
    settings=get_settings()
    return {'environment':settings.app_env,'mock_notifications':settings.mock_notifications,'private_storage':'local_development_only','production_enabled':False,'matching_batch_size':settings.matching_batch_size,'max_document_bytes':settings.max_upload_bytes}


from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlalchemy.exc import IntegrityError
from app.modules.auth.router import norm_phone, norm_email
from app.identity import normalize_username, normalize_saudi_phone, normalize_email, validate_password
from app.identity_columns import email_identity

class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3,max_length=80)
    phone_country: str | None = Field(default=None,min_length=2,max_length=2)
    phone: str = Field(min_length=1,max_length=80)
    email: str | None = Field(default=None,max_length=200)
    password: str = Field(min_length=6,max_length=128)
    is_admin: bool = False
    phone_verified: bool = False
    email_verified: bool = False
    _username = field_validator('username')(normalize_username)
    @model_validator(mode='after')
    def canonical_phone(self):
        self.phone=normalize_saudi_phone(self.phone,self.phone_country,sms=True)
        return self
    _password = field_validator('password')(validate_password)

    @field_validator('email')
    @classmethod
    def valid_email(cls,value):
        return normalize_email(value) if value is not None else None

class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str | None = Field(default=None,min_length=3,max_length=80)
    phone: str | None = Field(default=None,min_length=8,max_length=30)
    email: str | None = Field(default=None,max_length=200)
    password: str | None = Field(default=None,min_length=6,max_length=128)
    is_admin: bool | None = None
    is_active: bool | None = None
    phone_verified: bool | None = None
    email_verified: bool | None = None

    @field_validator('username','phone','email','password')
    @classmethod
    def validate_identity(cls,value,info):
        if value is None:return None
        # Existing email-like usernames may be echoed unchanged by an edit form.
        # Changed usernames are checked after loading the original record below.
        if info.field_name=='username':return value
        return {'username':normalize_username,'phone':normalize_saudi_phone,'email':normalize_email,'password':validate_password}[info.field_name](value)

    @model_validator(mode='after')
    def reject_null_required(self):
        for field in self.model_fields_set - {'email'}:
            if getattr(self,field) is None:
                raise ValueError(f'{field} cannot be null')
        return self

class UserPage(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int


def save_admin_user(db, user):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409,'Username, phone number or email is already registered') from exc
    db.refresh(user)
    return user


def ensure_identity_available(db,user_id,username,phone,email):
    condition=(func.lower(func.trim(User.username))==username)|(User.phone_e164==phone)
    if email:condition=condition|(email_identity(User.email)==email)|(func.lower(func.trim(User.username))==email)
    if db.scalar(select(User.id).where(condition,User.id!=user_id).limit(1)):
        raise HTTPException(409,'Username, phone number or email is already registered')

def set_manual_verification(db, admin, user, values):
    """Admin attestation, distinct from an OTP ownership verification event."""
    changes={}
    for field in ('phone_verified','email_verified'):
        if field not in values:continue
        approved=values[field]
        if approved:
            try:
                if field=='phone_verified':normalize_saudi_phone(user.phone,sms=True)
                elif not user.email:raise ValueError('Add a valid email before verifying it')
                else:normalize_email(user.email)
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from exc
        previous=getattr(user,field)
        setattr(user,field,approved)
        if previous!=approved:
            changes[field]={'before':previous,'after':approved}
            if field=='phone_verified':
                db.execute(update(PhoneOTP).where((PhoneOTP.user_id==user.id)|(PhoneOTP.phone==user.phone)).values(used=True))
                if not approved:user.sms_notifications_enabled=False
            else:
                if user.email:db.execute(update(EmailOTP).where(EmailOTP.email==user.email).values(used=True))
                if not approved:user.email_notifications_enabled=False
    if changes:
        audit(db,admin,'admin.user_verification_changed',entity_id=user.id,source='manual_admin',changes=changes)

@router.get('/users',response_model=UserPage)
def list_users(q:str=Query(default='',max_length=200),limit:int=Query(default=25,ge=1,le=100),offset:int=Query(default=0,ge=0),admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    query=select(User)
    if q.strip():
        term='%'+q.strip().casefold().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        query=query.where(func.lower(User.username).like(term,escape='\\')|User.phone.like(term,escape='\\')|func.lower(User.email).like(term,escape='\\'))
    rows=db.execute(query.add_columns(func.count().over()).order_by(User.created_at.desc(),User.id).offset(offset).limit(limit)).all()
    total=rows[0][1] if rows else (db.scalar(select(func.count()).select_from(query.subquery())) if offset else 0)
    return {'items':[row[0] for row in rows],'total':total,'limit':limit,'offset':offset}

@router.get('/users/{user_id}',response_model=UserOut)
def get_user(user_id:str,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    user=db.get(User,user_id)
    if not user:raise HTTPException(404,'User not found')
    return user

@router.post('/users',status_code=201,response_model=UserOut)
def create_user(payload:AdminUserCreate,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    username=payload.username.strip().casefold()
    if len(username)<3:raise HTTPException(422,'Username must contain at least 3 characters')
    user=User(username=username,phone=norm_phone(payload.phone),email=norm_email(payload.email) if payload.email else None,password_hash=password_hash(payload.password),is_admin=payload.is_admin,phone_verified=False)
    ensure_identity_available(db,'',user.username,user.phone,user.email)
    db.add(user)
    db.flush()
    set_manual_verification(db,admin,user,{field:getattr(payload,field) for field in ('phone_verified','email_verified')})
    audit(db,admin,'admin.user_created',username=username)
    return save_admin_user(db,user)

@router.patch('/users/{user_id}',response_model=UserOut)
def edit_user(user_id:str,payload:AdminUserUpdate,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    user=db.scalar(select(User).where(User.id==user_id).with_for_update())
    if not user:raise HTTPException(404,'User not found')
    values=payload.model_dump(exclude_unset=True)
    verification={key:values.pop(key) for key in ('phone_verified','email_verified') if key in values}
    if not values and not verification:raise HTTPException(422,'Provide at least one field')
    if user.id==admin.id and (values.get('is_active') is False or values.get('is_admin') is False):
        raise HTTPException(409,'You cannot deactivate or remove your own administrator access')
    db.execute(update(RefreshSession).where(RefreshSession.user_id==user.id).values(revoked=True))
    if 'username' in values:
        if values['username'].strip().casefold()==user.username.strip().casefold():
            values.pop('username')
        else:
            try:values['username']=normalize_username(values['username'])
            except ValueError as exc:
                raise HTTPException(422,[{'loc':['body','username'],'msg':str(exc),'type':'value_error'}]) from exc
    if 'phone' in values:
        values['phone']=norm_phone(values['phone'], sms=True)
        if user.phone_verified and values['phone']!=user.phone_e164:
            raise HTTPException(409,'Use authenticated phone-change verification to replace a verified phone')
        try:same_phone=values['phone']==normalize_saudi_phone(user.phone)
        except ValueError:same_phone=False
        if same_phone:values['phone']=user.phone
        else:user.phone_verified=False;user.sms_notifications_enabled=False
    if 'email' in values:
        values['email']=norm_email(values['email']) if values['email'] else None
        try:same_email=values['email']==(normalize_email(user.email) if user.email else None)
        except ValueError:same_email=False
        if same_email:values['email']=user.email
        else:user.email_verified=False;user.email_notifications_enabled=False
    if 'password' in values:user.password_hash=password_hash(values.pop('password'))
    ensure_identity_available(db,user.id,values.get('username',user.username),values.get('phone',user.phone),values.get('email',user.email))
    for key,value in values.items():setattr(user,key,value)
    set_manual_verification(db,admin,user,verification)
    audit(db,admin,'admin.user_updated',entity_id=user.id,fields=sorted(payload.model_fields_set))
    return save_admin_user(db,user)

@router.delete('/users/{user_id}')
def delete_user(user_id:str,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Permanently remove the account, authentication data and personal dependencies."""
    user=db.scalar(select(User).where(User.id==user_id).with_for_update())
    if not user:raise HTTPException(404,'User not found')
    if user.id==admin.id:raise HTTPException(409,'You cannot delete your own administrator account')
    # Keep shared company and procurement records independent of account deletion.
    for model,field in ((CompanyClaim,CompanyClaim.reviewer_id),(Verification,Verification.reviewer_id),(MaterialRevision,MaterialRevision.actor_user_id),(ContactInquiry,ContactInquiry.handled_by),(AuditEvent,AuditEvent.actor_user_id)):
        db.execute(update(model).where(field==user.id).values({field.key:None}))
    db.execute(update(ImportBatch).where(ImportBatch.initiated_by==user.id).values(initiated_by=admin.id))
    for model,field in ((RefreshSession,RefreshSession.user_id),(Member,Member.user_id),(Notification,Notification.recipient_user_id),(CompanyClaim,CompanyClaim.claimant_id)):
        db.execute(delete(model).where(field==user.id))
    db.execute(delete(PhoneOTP).where(PhoneOTP.phone==user.phone))
    db.execute(delete(EmailOTP).where(EmailOTP.email.in_([value for value in (user.phone,user.email) if value])))
    delivery_ids=select(Outbox.id).where(Outbox.payload['user_id'].as_string()==user.id)
    db.execute(update(Notification).where(Notification.delivery_outbox_id.in_(delivery_ids)).values(delivery_outbox_id=None))
    db.execute(delete(Outbox).where(Outbox.payload['user_id'].as_string()==user.id))
    audit(db,admin,'admin.user_deleted',entity_id=user.id)
    db.delete(user)
    try:db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409,'This user has a linked record that could not be removed; no changes were saved') from exc
    return {'deleted':True}
