from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field, ConfigDict, model_validator
from app.database import get_db
from app.models import MembershipPlan, Subscription, MembershipSettings, Promotion, User, Organization, RFQ, Member
from app.security import admin_user, current_user, org_member, audit
from app.serializers import record
from app.response_contracts import PlanOut, SubscriptionOut

router=APIRouter(prefix='/memberships',tags=['Memberships'])
DEFAULTS=dict(free_launch_mode=True,paid_memberships_enabled=False,proposed_paid_launch_date=None,
              default_free_plan=None,premium_promotion_enabled=True,featured_rfqs_enabled=True)

def settings(db):
    row=db.get(MembershipSettings,1)
    return DEFAULTS | (row.config if row else {})

def save(db,user,action,obj,org_id=None):
    db.flush()
    audit(db,user,action,org_id,str(obj.id),snapshot=record(obj))
    db.commit()
    return record(obj)

from app.modules.memberships.plans import PlanInput, router as plan_router
router.include_router(plan_router)

@router.get('/settings')
def public_settings(response:Response,db:Session=Depends(get_db)):
    response.headers['Cache-Control']='no-store, max-age=0'
    return settings(db)

class SettingsInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    free_launch_mode:bool=True
    paid_memberships_enabled:bool=False
    proposed_paid_launch_date:datetime|None=None
    default_free_plan:str|None=None
    premium_promotion_enabled:bool=True
    featured_rfqs_enabled:bool=True

@router.put('/admin/settings')
def change_settings(payload:SettingsInput,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    if payload.paid_memberships_enabled:raise HTTPException(409,'Payment provider and approved billing integration are not configured')
    if payload.default_free_plan:
        plan=db.get(MembershipPlan,payload.default_free_plan)
        if not plan or not plan.active or plan.archived or plan.monthly_price or plan.yearly_price:raise HTTPException(422,'Default plan must be active and free')
    row=db.get(MembershipSettings,1)
    if not row:row=MembershipSettings(id=1);db.add(row)
    row.config=payload.model_dump(mode='json')
    save(db,user,'membership.settings.updated',row)
    return settings(db)

class SubscriptionDetailOut(SubscriptionOut):
    plan_name:str

@router.get('/organizations/{org_id}',response_model=list[SubscriptionDetailOut])
def org_plan(org_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id)
    return [dict(record(sub),plan_name=(sub.plan_snapshot or {}).get('name',name)) for sub,name in db.execute(select(Subscription,MembershipPlan.name).join(MembershipPlan,MembershipPlan.id==Subscription.plan_id).where(Subscription.org_id==org_id).order_by(Subscription.created_at.desc()))]

class Assign(BaseModel):
    org_id:str
    plan_id:str
    billing_interval:Literal['monthly','yearly']='monthly'
    expires_at:datetime|None=None

@router.post('/admin/assign',response_model=SubscriptionOut)
def assign(payload:Assign,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    org=db.scalar(select(Organization).where(Organization.id==payload.org_id).with_for_update());plan=db.scalar(select(MembershipPlan).where(MembershipPlan.id==payload.plan_id).with_for_update())
    if not org or not org.is_supplier or not plan:raise HTTPException(404,'Supplier or plan not found')
    if not plan.active or plan.archived or not plan.available_for_subscription:raise HTTPException(409,'Plan unavailable')
    if payload.billing_interval not in plan.billing_intervals:raise HTTPException(422,'Billing interval unavailable')
    from app.security import as_utc
    if (plan.available_from and as_utc(plan.available_from)>datetime.now(UTC)) or (plan.available_until and as_utc(plan.available_until)<=datetime.now(UTC)):raise HTTPException(409,'Plan outside availability window')
    now=datetime.now(UTC)
    if payload.expires_at and (payload.expires_at.tzinfo is None or payload.expires_at<=now):raise HTTPException(422,'Expiration must be a future timezone-aware date')
    for old in db.scalars(select(Subscription).where(Subscription.org_id==org.id,Subscription.status.in_(['active','suspended'])).with_for_update()):
        old.status='cancelled';audit(db,user,'membership.replaced',org.id,old.id)
    sub=Subscription(**payload.model_dump(),starts_at=now,price_snapshot=plan.monthly_price if payload.billing_interval=='monthly' else plan.yearly_price,currency_snapshot=plan.currency,premium_snapshot=plan.premium,plan_snapshot=record(plan))
    db.add(sub)
    return save(db,user,'membership.assigned',sub,org.id)

@router.get('/admin/subscriptions',response_model=list[SubscriptionOut])
def subscriptions(user:User=Depends(admin_user),db:Session=Depends(get_db),offset:int=Query(0,ge=0),limit:int=Query(25,ge=1,le=100)):
    return [record(s) for s in db.scalars(select(Subscription).order_by(Subscription.created_at.desc()).offset(offset).limit(limit))]

class SubscriptionChange(BaseModel):
    status:Literal['active','suspended','cancelled']
    expires_at:datetime|None=None

@router.patch('/admin/subscriptions/{sub_id}',response_model=SubscriptionOut)
def change_subscription(sub_id:str,payload:SubscriptionChange,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    sub=db.get(Subscription,sub_id)
    if not sub:raise HTTPException(404,'Subscription not found')
    if payload.expires_at and (payload.expires_at.tzinfo is None or payload.expires_at<=datetime.now(UTC)):raise HTTPException(422,'Expiration must be in the future with timezone')
    if sub.status=='cancelled' and payload.status=='active':raise HTTPException(409,'Assign a new membership to preserve cancellation history')
    sub.status=payload.status
    if payload.expires_at:sub.expires_at=payload.expires_at
    return save(db,user,'membership.status.changed',sub,sub.org_id)

class PromotionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['premium','supplier_featured','rfq_featured','rfq_urgent']
    org_id:str|None=None
    rfq_id:str|None=None
    placement:Literal['homepage','directory','industry','related']='homepage'
    priority:int=Field(default=0,ge=0,le=10000)
    starts_at:datetime
    expires_at:datetime
    note:str=Field(default='',max_length=2000)
    @model_validator(mode='after')
    def valid(self):
        if not self.starts_at.tzinfo or not self.expires_at.tzinfo or self.expires_at<=self.starts_at:raise ValueError('Require timezone-aware start before expiration')
        if self.kind.startswith('rfq'):
            if not self.rfq_id or self.org_id:raise ValueError('RFQ target required')
        elif not self.org_id or self.rfq_id:raise ValueError('Supplier target required')
        return self

@router.post('/admin/promotions',status_code=201)
def promote(payload:PromotionInput,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    if payload.org_id:
        org=db.get(Organization,payload.org_id)
        if not org or not org.is_supplier:raise HTTPException(404,'Supplier not found')
        if payload.kind=='supplier_featured' and (not org.is_active or org.verified_status!='verified'):raise HTTPException(409,'Supplier must be verified and active')
    else:
        rfq=db.get(RFQ,payload.rfq_id)
        if not rfq or rfq.status!='published' or rfq.response_deadline.replace(tzinfo=UTC)<=datetime.now(UTC):raise HTTPException(409,'RFQ must be published and open')
    obj=Promotion(**payload.model_dump());db.add(obj)
    return save(db,user,'promotion.manual.granted',obj,payload.org_id)

@router.get('/admin/promotions')
def promotions(user:User=Depends(admin_user),db:Session=Depends(get_db),offset:int=Query(0,ge=0),limit:int=Query(25,ge=1,le=100)):
    return [record(p) for p in db.scalars(select(Promotion).order_by(Promotion.created_at.desc()).offset(offset).limit(limit))]

@router.delete('/admin/promotions/{promotion_id}')
def revoke(promotion_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    obj=db.get(Promotion,promotion_id)
    if not obj:raise HTTPException(404,'Promotion not found')
    obj.active=False
    return save(db,user,'promotion.revoked',obj,obj.org_id)

@router.get('/public/featured-suppliers')
def featured(placement:Literal['homepage','directory','industry','related']='homepage',db:Session=Depends(get_db),limit:int=Query(10,ge=1,le=50)):
    from app.modules.capabilities.directory import public_profiles
    now=datetime.now(UTC)
    ranked=select(Promotion.org_id,func.max(Promotion.priority).label('rank')).where(Promotion.kind=='supplier_featured',Promotion.placement==placement,Promotion.active.is_(True),Promotion.starts_at<=now,Promotion.expires_at>now).group_by(Promotion.org_id).subquery()
    organizations=db.scalars(select(Organization).join(ranked,ranked.c.org_id==Organization.id).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.verified_status=='verified').order_by(ranked.c.rank.desc(),Organization.name).limit(limit)).all()
    return [dict(p,featured=True) for p in public_profiles(db,organizations,True)]

@router.put('/rfqs/{rfq_id}/public-consent')
def authorize_rfq(rfq_id:str,authorized:bool,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    member=db.scalar(select(Member).where(Member.user_id==user.id,Member.organization_id==rfq.buyer_org_id,Member.role.in_(['owner','manager','buyer'])))
    if not member:raise HTTPException(403,'Buyer organization authorization required')
    rfq.public_display_authorized=authorized
    save(db,user,'rfq.public.consent',rfq,rfq.buyer_org_id)
    return {'authorized':authorized}

@router.get('/public/featured-rfqs')
def featured_rfqs(db:Session=Depends(get_db),limit:int=Query(10,ge=1,le=50)):
    if not settings(db)['featured_rfqs_enabled']:return []
    now=datetime.now(UTC)
    rows=db.execute(select(RFQ,Promotion).join(Promotion,Promotion.rfq_id==RFQ.id).where(
        Promotion.kind.in_(['rfq_featured','rfq_urgent']),Promotion.active.is_(True),
        Promotion.starts_at<=now,Promotion.expires_at>now,RFQ.status=='published',
        RFQ.response_deadline>now,RFQ.public_display_authorized.is_(True)).order_by(Promotion.priority.desc()).limit(limit)).all()
    output={}
    for r,p in rows:
        item=output.setdefault(r.id,dict(id=r.id,title=r.title,city=r.city,response_deadline=r.response_deadline,urgent=False,featured=False))
        item['urgent' if p.kind=='rfq_urgent' else 'featured']=True
    return list(output.values())

@router.get('/public/suppliers/{org_id}/status')
def supplier_status(org_id:str,db:Session=Depends(get_db)):
    org=db.get(Organization,org_id)
    if not org or not org.is_supplier or not org.is_active or org.verified_status!='verified':raise HTTPException(404,'Supplier not found')
    now=datetime.now(UTC)
    active=db.scalars(select(Promotion.kind).where(Promotion.org_id==org_id,Promotion.active.is_(True),Promotion.starts_at<=now,Promotion.expires_at>now)).all()
    entitled=db.scalar(select(Subscription.id).where(Subscription.org_id==org_id,Subscription.status=='active',Subscription.premium_snapshot.is_(True),
        (Subscription.starts_at.is_(None)) | (Subscription.starts_at<=now),
        (Subscription.expires_at.is_(None)) | (Subscription.expires_at>now)).limit(1))
    return dict(verified=True,premium=bool(entitled or ('premium' in active and settings(db)['premium_promotion_enabled'])),featured='supplier_featured' in active)

@router.put('/admin/promotions/{promotion_id}')
def update_promotion(promotion_id:str,payload:PromotionInput,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    old=db.get(Promotion,promotion_id)
    if not old:raise HTTPException(404,'Promotion not found')
    if (payload.org_id,payload.rfq_id,payload.kind)!=(old.org_id,old.rfq_id,old.kind):raise HTTPException(422,'Promotion target and kind cannot change')
    for key,value in payload.model_dump().items():setattr(old,key,value)
    return save(db,user,'promotion.updated',old,old.org_id)
