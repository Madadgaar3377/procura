from datetime import datetime, timezone
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from sqlalchemy import select,func,and_,or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import PriceBookItem,Material,Category,User,AuditEvent
from app.schemas import PriceIn,RateStatus,QuoteAutomationIn
from app.response_contracts import PriceOut,PricePage,QuoteAutomationOut,RateHistory,PricePolicyOut
from app.security import current_user,audit
from app.serializers import record
from app.modules.pricebooks.service import supplier,save_rate,rate_record,policy_record,save_policy,utc,validate_material,validate_saved_rate

from app.rate_limit import enforce_rate_limit
from app.config import get_settings

router=APIRouter(prefix='/pricebooks',tags=['Private Supplier Rates'])

def write_limit(request,user):
    enforce_rate_limit(request,scope='private-rate-write',identity=user.id,limit=120,window_seconds=60)

@router.get('/policy',response_model=PricePolicyOut)
def pricing_policy(user:User=Depends(current_user)):
    return {'require_capability_approval':get_settings().require_capability_approval}


def commit(db):
    try:db.commit()
    except IntegrityError as error:
        db.rollback();raise HTTPException(409,'An active rate already exists for this material and price basis. Reload your rates.') from error

@router.post('',status_code=201,response_model=PriceOut)
def create(payload:PriceIn,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    write_limit(request,user)
    item=save_rate(db,user,payload);commit(db);return rate_record(db,item)

def listing(db,user,org_id,q,main_category_id,subcategory_id,status,limit,offset):
    supplier(db,user,org_id)
    query=select(PriceBookItem,Material).outerjoin(Material,Material.id==PriceBookItem.material_id).where(PriceBookItem.org_id==org_id)
    if q:
        escaped=q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_');term='%'+escaped+'%'
        query=query.where(or_(*(column.ilike(term,escape='\\') for column in [PriceBookItem.supplier_description,Material.name_en,Material.name_ar,Material.arabic_description,Material.code,PriceBookItem.offered_description,PriceBookItem.brand,PriceBookItem.model])))
    if main_category_id:query=query.where(or_(PriceBookItem.main_category_id==main_category_id,Material.main_category_id==main_category_id,Material.category_id==main_category_id))
    if subcategory_id:query=query.where(or_(PriceBookItem.subcategory_id==subcategory_id,Material.subcategory_id==subcategory_id,Material.category_id==subcategory_id))
    now=datetime.now(timezone.utc)
    valid=or_(PriceBookItem.valid_until.is_(None),PriceBookItem.valid_until>now)
    current=or_(PriceBookItem.material_id.is_(None),and_(Material.active.is_(True),Material.version==PriceBookItem.material_version,func.upper(func.trim(Material.unit))==PriceBookItem.unit))
    if status=='active':query=query.where(PriceBookItem.active.is_(True),valid,current)
    elif status=='inactive':query=query.where(PriceBookItem.active.is_(False))
    elif status=='expired':query=query.where(PriceBookItem.active.is_(True),PriceBookItem.valid_until<=now)
    elif status=='review':query=query.where(PriceBookItem.active.is_(True),valid,~current)
    total=int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows=db.execute(query.order_by(PriceBookItem.created_at.desc(),PriceBookItem.id).limit(limit).offset(offset)).all()
    ids={key for p,m in rows for key in [p.main_category_id,p.subcategory_id,*([m.category_id,m.main_category_id,m.subcategory_id] if m else [])] if key}
    cats={c.id:c for c in db.scalars(select(Category).where(Category.id.in_(ids))).all()}
    parents={c.parent_id for c in cats.values() if c.parent_id and c.parent_id not in cats}
    if parents:cats.update({c.id:c for c in db.scalars(select(Category).where(Category.id.in_(parents))).all()})
    return {'items':[rate_record(db,p,m,cats) for p,m in rows],'total':total,'limit':limit,'offset':offset}

@router.get('/{org_id}/page',response_model=PricePage)
def page(org_id:str,q:str=Query('',max_length=200),main_category_id:str|None=None,subcategory_id:str|None=None,status:str=Query('all',pattern='^(all|active|inactive|expired|review)$'),limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),user:User=Depends(current_user),db:Session=Depends(get_db)):
    return listing(db,user,org_id,q,main_category_id,subcategory_id,status,limit,offset)

@router.get('/{org_id}',response_model=list[PriceOut])
def list_private(org_id:str,q:str=Query('',max_length=200),main_category_id:str|None=None,subcategory_id:str|None=None,status:str=Query('all',pattern='^(all|active|inactive|expired|review)$'),limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0),user:User=Depends(current_user),db:Session=Depends(get_db)):
    return listing(db,user,org_id,q,main_category_id,subcategory_id,status,limit,offset)['items']

@router.put('/{price_id}',response_model=PriceOut)
def update_price(price_id:str,payload:PriceIn,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    item=db.get(PriceBookItem,price_id)
    if not item:raise HTTPException(404,'Rate not found')
    supplier(db,user,item.org_id,lock=True);write_limit(request,user);db.refresh(item)
    item=save_rate(db,user,payload,item);commit(db);return rate_record(db,item)

@router.patch('/{price_id}/status',response_model=PriceOut)
def status_change(price_id:str,payload:RateStatus,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    item=db.get(PriceBookItem,price_id)
    if not item:raise HTTPException(404,'Rate not found')
    supplier(db,user,item.org_id,lock=True);write_limit(request,user);db.refresh(item)
    if payload.expected_version is not None and payload.expected_version!=item.version:raise HTTPException(409,'This rate changed. Reload before saving your edits.')
    if payload.active==item.active:return rate_record(db,item)
    if payload.active:
        material,unit=validate_saved_rate(db,item)
        if item.valid_until and utc(item.valid_until)<=datetime.now(timezone.utc):raise HTTPException(422,'Extend the validity before reactivating this expired rate')
        if db.scalar(select(PriceBookItem.id).where(PriceBookItem.org_id==item.org_id,(PriceBookItem.material_id==item.material_id if item.material_id else PriceBookItem.custom_key==item.custom_key),PriceBookItem.price_basis==item.price_basis,PriceBookItem.active.is_(True),PriceBookItem.id!=item.id)):raise HTTPException(409,'Another active rate exists for this material and price basis')
    before=record(item);item.active=payload.active;item.version+=1;item.updated_at=datetime.now(timezone.utc);db.flush()
    audit(db,user,'price_book.status_changed',item.org_id,item.id,before=before,after=record(item));commit(db);return rate_record(db,item)

@router.get('/{price_id}/history',response_model=list[RateHistory])
def history(price_id:str,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),user:User=Depends(current_user),db:Session=Depends(get_db)):
    item=db.get(PriceBookItem,price_id)
    if not item:raise HTTPException(404,'Rate not found')
    supplier(db,user,item.org_id)
    events=db.scalars(select(AuditEvent).where(AuditEvent.org_id==item.org_id,AuditEvent.entity_id==item.id,AuditEvent.action.like('price_book.%')).order_by(AuditEvent.created_at.desc(),AuditEvent.id).limit(limit).offset(offset)).all()
    return [{'id':e.id,'created_at':e.created_at.isoformat(),'action':e.action,'before':e.metadata_json.get('before'),'after':e.metadata_json.get('after')} for e in events]

@router.get('/{org_id}/automation',response_model=QuoteAutomationOut)
def automation(org_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    return policy_record(db,supplier(db,user,org_id))

@router.put('/{org_id}/automation',response_model=QuoteAutomationOut)
def authorize(org_id:str,payload:QuoteAutomationIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    result=save_policy(db,user,org_id,payload);commit(db);return result

from app.modules.pricebooks.imports import router as imports_router
router.include_router(imports_router)
