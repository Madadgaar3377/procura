"""Plan lifecycle. All catalog reads bypass RAM/Redis; HTTP responses are no-store."""
from datetime import UTC, datetime
from decimal import Decimal
import re
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import MembershipPlan, Subscription, MembershipSettings, User
from app.response_contracts import PlanOut
from app.security import admin_user, audit
from app.serializers import record

router=APIRouter()

class Feature(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=250)
    included:bool=True
    @field_validator('label')
    @classmethod
    def label_not_empty(cls,value):
        if not value.strip():raise ValueError('Feature cannot be blank')
        return value.strip()

class PlanInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=80,pattern=r'\S')
    slug:str|None=Field(default=None,min_length=1,max_length=100,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    description:str=Field(default='',max_length=4000)
    monthly_price:Decimal=Field(default=Decimal('0'),ge=0,max_digits=12,decimal_places=2)
    yearly_price:Decimal=Field(default=Decimal('0'),ge=0,max_digits=12,decimal_places=2)
    currency:Literal['SAR']='SAR'
    features:list[str]=Field(default_factory=list,max_length=50)
    feature_details:list[Feature]=Field(default_factory=list,max_length=50)
    billing_intervals:list[Literal['monthly','yearly']]=Field(default_factory=lambda:['monthly','yearly'],min_length=1,max_length=2)
    quote_limit:int=Field(default=10,ge=0,le=2147483647)
    team_limit:int=Field(default=1,ge=1,le=2147483647)
    auto_drafts:bool=False
    active:bool=True
    recommended:bool=False
    premium:bool=False
    display_order:int=Field(default=0,ge=0,le=2147483647)
    publication_status:Literal['draft','published','unpublished']='draft'
    visible_on_website:bool=True
    available_for_subscription:bool=True
    available_from:datetime|None=None
    available_until:datetime|None=None
    expected_version:int|None=Field(default=None,ge=1)

    @field_validator('name')
    @classmethod
    def trim_name(cls,value):return value.strip()
    @field_validator('features')
    @classmethod
    def validate_features(cls,values):
        if any(not x.strip() or len(x)>250 for x in values):raise ValueError('Features must be nonempty and at most 250 characters')
        return [x.strip() for x in values]
    @model_validator(mode='after')
    def dates(self):
        for value in (self.available_from,self.available_until):
            if value and value.tzinfo is None:raise ValueError('Availability dates require timezone')
        if self.available_from and self.available_until and self.available_from>=self.available_until:raise ValueError('Availability end must follow start')
        if len(set(self.billing_intervals))!=len(self.billing_intervals):raise ValueError('Duplicate billing interval')
        return self

class AdminPlanOut(PlanOut):
    subscribers:int=0

class PlanPage(BaseModel):
    items:list[AdminPlanOut]
    total:int
    limit:int
    offset:int


def lock_catalog(db):
    # One transaction lock serializes recommendation/reorder/delete operations.
    if db.bind.dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(71309412)'))

def load(db,plan_id,expected=None):
    obj=db.scalar(select(MembershipPlan).where(MembershipPlan.id==plan_id).with_for_update())
    if not obj:raise HTTPException(404,'Plan not found')
    if expected is not None and obj.version!=expected:raise HTTPException(409,'This plan changed. Reload it before saving.')
    return obj

def public_query():
    now=datetime.now(UTC)
    return select(MembershipPlan).where(MembershipPlan.active.is_(True),MembershipPlan.archived.is_(False),
        MembershipPlan.publication_status=='published',MembershipPlan.visible_on_website.is_(True),
        (MembershipPlan.available_from.is_(None))|(MembershipPlan.available_from<=now),
        (MembershipPlan.available_until.is_(None))|(MembershipPlan.available_until>now))

def no_store(response):response.headers['Cache-Control']='no-store, max-age=0'

def write(db,user,obj,action):
    try:
        obj.updated_at=datetime.now(UTC)
        db.flush()
        audit(db,user,action,entity_id=obj.id,snapshot=record(obj))
        db.commit()
        return record(obj)
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Plan name/code conflicts or a dependent record prevents this change')

def recommend(db,user,obj):
    if obj.recommended and not obj.archived:
        for other in db.scalars(select(MembershipPlan).where(MembershipPlan.recommended.is_(True),MembershipPlan.archived.is_(False),MembershipPlan.id!=obj.id).with_for_update()):
            other.recommended=False;other.version+=1;other.updated_at=datetime.now(UTC)
            audit(db,user,'membership.plan.recommendation_removed',entity_id=other.id)
        # Remove the old recommendation before the unique-index-protected insert.
        with db.no_autoflush:
            db.execute(update(MembershipPlan).where(MembershipPlan.recommended.is_(True),MembershipPlan.archived.is_(False),MembershipPlan.id!=obj.id).values(recommended=False))

def values(payload):
    data=payload.model_dump(exclude={'expected_version'},mode='python')
    data['feature_details']=[f.model_dump() for f in payload.feature_details]
    if payload.feature_details:data['features']=[f.label for f in payload.feature_details if f.included]
    return data

@router.get('/plans',response_model=list[PlanOut])
def plans(response:Response,db:Session=Depends(get_db)):
    no_store(response)
    return [record(p) for p in db.scalars(public_query().order_by(MembershipPlan.display_order,MembershipPlan.id))]

@router.get('/admin/plans',response_model=list[AdminPlanOut])
def admin_plans(response:Response,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    no_store(response)
    counts=select(Subscription.plan_id,func.count(func.distinct(Subscription.org_id)).label('count')).where(Subscription.status=='active',(Subscription.expires_at.is_(None))|(Subscription.expires_at>datetime.now(UTC))).group_by(Subscription.plan_id).subquery()
    return [dict(record(p),subscribers=count or 0) for p,count in db.execute(select(MembershipPlan,counts.c.count).outerjoin(counts,counts.c.plan_id==MembershipPlan.id).order_by(MembershipPlan.display_order,MembershipPlan.id))]

@router.get('/admin/plan-page',response_model=PlanPage)
def plan_page(response:Response,user:User=Depends(admin_user),db:Session=Depends(get_db),q:str=Query('',max_length=100),
              status:Literal['all','draft','published','unpublished','archived']='all',
              sort:Literal['name','monthly_price','yearly_price','display_order','created_at']='display_order',
              descending:bool=False,offset:int=Query(0,ge=0),limit:int=Query(25,ge=1,le=100)):
    no_store(response)
    query=select(MembershipPlan)
    if q.strip():
        escaped=q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        query=query.where(MembershipPlan.name.ilike('%'+escaped+'%',escape='\\')|MembershipPlan.slug.ilike('%'+escaped+'%',escape='\\'))
    if status=='archived':query=query.where(MembershipPlan.archived.is_(True))
    elif status!='all':query=query.where(MembershipPlan.archived.is_(False),MembershipPlan.publication_status==status)
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    counts=select(Subscription.plan_id,func.count(func.distinct(Subscription.org_id)).label('count')).where(Subscription.status=='active',(Subscription.expires_at.is_(None))|(Subscription.expires_at>datetime.now(UTC))).group_by(Subscription.plan_id).subquery()
    column=getattr(MembershipPlan,sort)
    rows=db.execute(query.add_columns(counts.c.count).outerjoin(counts,counts.c.plan_id==MembershipPlan.id).order_by(column.desc() if descending else column,MembershipPlan.id).offset(offset).limit(limit))
    return dict(items=[dict(record(p),subscribers=n or 0) for p,n in rows],total=total,limit=limit,offset=offset)

@router.get('/admin/plans/{plan_id}',response_model=PlanOut)
def get_plan(plan_id:str,response:Response,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    no_store(response);return record(load(db,plan_id))

@router.post('/admin/plans',response_model=PlanOut,status_code=201)
def create_plan(payload:PlanInput,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    lock_catalog(db)
    data=values(payload)
    if not data['slug']:data['slug']=(re.sub('[^a-z0-9]+','-',payload.name.lower()).strip('-')[:60] or 'plan')+'-'+uuid4().hex[:8]
    obj=MembershipPlan(**data);obj.id=str(uuid4())
    recommend(db,user,obj);db.add(obj)
    return write(db,user,obj,'membership.plan.created')

@router.put('/admin/plans/{plan_id}',response_model=PlanOut)
def update_plan(plan_id:str,payload:PlanInput,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    lock_catalog(db);obj=load(db,plan_id,payload.expected_version)
    if obj.archived:raise HTTPException(409,'Archived plans are retained unchanged for history')
    data=values(payload)
    # Old PUT clients do not know the new publication/availability fields.
    for field in ('publication_status','visible_on_website','available_for_subscription','billing_intervals','available_from','available_until'):
        if field not in payload.model_fields_set:data.pop(field,None)
    if 'features' not in payload.model_fields_set and 'feature_details' not in payload.model_fields_set:
        data.pop('features',None);data.pop('feature_details',None)
    if not data['slug']:data['slug']=obj.slug
    config=db.get(MembershipSettings,1)
    if config and config.config.get('default_free_plan')==plan_id and (data['monthly_price'] or data['yearly_price'] or not data['active']):
        raise HTTPException(409,'Choose another default free plan before changing its price or status')
    # Clear previous recommendation before enabling this one.
    proposed=data.pop('recommended')
    if proposed:
        with db.no_autoflush:
            previous_ids=db.scalars(select(MembershipPlan.id).where(MembershipPlan.id!=obj.id,MembershipPlan.recommended.is_(True),MembershipPlan.archived.is_(False))).all()
            for previous_id in previous_ids:audit(db,user,'membership.plan.recommendation_removed',entity_id=previous_id)
            db.execute(update(MembershipPlan).where(MembershipPlan.id!=obj.id,MembershipPlan.recommended.is_(True),MembershipPlan.archived.is_(False)).values(recommended=False,version=MembershipPlan.version+1,updated_at=datetime.now(UTC)))
    for key,value in data.items():setattr(obj,key,value)
    obj.recommended=proposed;obj.version+=1
    return write(db,user,obj,'membership.plan.updated')

class PlanAction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
    publication_status:Literal['draft','published','unpublished']|None=None

@router.patch('/admin/plans/{plan_id}/publication',response_model=PlanOut)
def publish(plan_id:str,payload:PlanAction,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    lock_catalog(db);obj=load(db,plan_id,payload.expected_version)
    if obj.archived:raise HTTPException(409,'Archived plans cannot be published')
    if payload.publication_status is None:raise HTTPException(422,'Publication status is required')
    obj.publication_status=payload.publication_status;obj.version+=1
    return write(db,user,obj,'membership.plan.publication_changed')

@router.post('/admin/plans/{plan_id}/duplicate',response_model=PlanOut,status_code=201)
def duplicate(plan_id:str,payload:PlanAction,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    lock_catalog(db);original=load(db,plan_id,payload.expected_version)
    data={k:v for k,v in record(original).items() if k not in {'id','created_at','updated_at','version','archived_at','available_from','available_until'}}
    data.update(name=original.name[:60]+' copy '+uuid4().hex[:6],slug=original.slug[:80]+'-copy-'+uuid4().hex[:6],recommended=False,archived=False,publication_status='draft')
    data['monthly_price']=Decimal(data['monthly_price']);data['yearly_price']=Decimal(data['yearly_price'])
    obj=MembershipPlan(**data);db.add(obj)
    return write(db,user,obj,'membership.plan.duplicated')

@router.delete('/admin/plans/{plan_id}',response_model=PlanOut)
def archive(plan_id:str,expected_version:int|None=Query(None,ge=1),user:User=Depends(admin_user),db:Session=Depends(get_db)):
    lock_catalog(db);obj=load(db,plan_id,expected_version)
    config=db.get(MembershipSettings,1)
    if config and config.config.get('default_free_plan')==plan_id:raise HTTPException(409,'Choose another default free plan first')
    obj.archived=True;obj.active=False;obj.recommended=False;obj.available_for_subscription=False
    obj.archived_at=obj.archived_at or datetime.now(UTC);obj.version+=1
    return write(db,user,obj,'membership.plan.archived')

@router.delete('/admin/plans/{plan_id}/permanent')
def permanent(plan_id:str,confirmation:Literal['DELETE'],expected_version:int=Query(ge=1),user:User=Depends(admin_user),db:Session=Depends(get_db)):
    lock_catalog(db);obj=load(db,plan_id,expected_version)
    if db.scalar(select(Subscription.id).where(Subscription.plan_id==plan_id).limit(1)):
        raise HTTPException(409,'This plan cannot be permanently deleted because existing subscriptions reference it. Archive it instead.')
    config=db.get(MembershipSettings,1)
    if config and config.config.get('default_free_plan')==plan_id:raise HTTPException(409,'Choose another default free plan first')
    audit(db,user,'membership.plan.deleted',entity_id=plan_id,snapshot=record(obj))
    try:
        db.delete(obj);db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Dependent records prevent deletion. Archive this plan instead.')
    return {'deleted':True}

class OrderEntry(BaseModel):
    id:str
    expected_version:int=Field(ge=1)
    display_order:int=Field(ge=0,le=2147483647)
class Reorder(BaseModel):
    items:list[OrderEntry]=Field(min_length=1,max_length=100)
@router.post('/admin/plan-order')
def reorder(payload:Reorder,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    lock_catalog(db)
    if len({entry.id for entry in payload.items})!=len(payload.items):raise HTTPException(422,'Duplicate plan IDs')
    for entry in payload.items:
        obj=load(db,entry.id,entry.expected_version)
        obj.display_order=entry.display_order;obj.version+=1;obj.updated_at=datetime.now(UTC)
        audit(db,user,'membership.plan.reordered',entity_id=obj.id,display_order=obj.display_order)
    db.commit();return {'updated':len(payload.items)}
