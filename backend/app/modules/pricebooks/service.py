"""Private per-unit rate invariants, history and explicit supplier authorization."""
from datetime import datetime, timezone
from hashlib import sha256
from fastapi import HTTPException
from sqlalchemy import select,func
from app.models import PriceBookItem, Material, Category, Capability, Organization, AuditEvent
from app.schemas import PriceIn, QuoteAutomationIn
from app.security import org_member, audit
from app.serializers import record
from app.modules.materials.service import normalize_unit, normalize_text, UNIT_NAMES
from app.models import MeasurementUnit
from app.config import get_settings
from app.modules.capabilities.policy import eligibility_predicates

def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def supplier(db,user,org_id,*,manage=False,lock=False):
    org_member(db,user,org_id,{'owner','manager'} if manage else {'owner','manager','supplier'})
    statement=select(Organization).where(Organization.id==org_id)
    org=db.scalar(statement.with_for_update() if lock else statement)
    if not org or not org.is_active or not org.is_supplier:raise HTTPException(422,'Company is not an active supplier')
    return org

def category_pair(db,material):
    main=material.main_category_id;sub=material.subcategory_id
    if not main:
        leaf=db.get(Category,material.category_id)
        if leaf:main=leaf.parent_id or leaf.id;sub=leaf.id if leaf.parent_id else None
    return main,sub

def capability_categories(db,org_id):
    """A category is approved if any saved service-area capability is approved."""
    key=('rate_capability_categories',org_id)
    if key not in db.info:
        selected={}
        for category_id,confirmed,status in db.execute(select(Capability.category_id,Capability.confirmed,Capability.eligibility_status).where(Capability.org_id==org_id)):
            value='approved' if confirmed and status=='enabled' else 'pending' if status=='enabled' else 'rejected'
            selected[category_id]='approved' if selected.get(category_id)!='approved' and value=='approved' else selected.get(category_id,value)
        db.info[key]=selected
    return db.info[key]

def ensure_material_capability(db,org,main,sub,material_id=None):
    """Create a pending declaration for the material's stable category IDs.

    Reuse each declared service area so approval and RFQ coverage remain scoped
    to that city. With no declared area, use the supplier's registered city.
    Existing approval/denial state is never changed by adding a private rate.
    """
    category_id=sub or main
    if not category_id: return
    cities=list(db.scalars(select(Capability.city).where(Capability.org_id==org.id)).all())
    if not cities and org.city and org.city.strip(): cities=[org.city.strip()]
    for city in dict.fromkeys(cities):
        exists=db.scalar(select(Capability).where(Capability.org_id==org.id,
            Capability.category_id==category_id,func.lower(func.trim(Capability.city))==city.casefold()))
        if not exists:
            exists=Capability(org_id=org.id,category_id=category_id,city=city,confirmed=False,material_ids=[])
            db.add(exists)
        if material_id and material_id not in (exists.material_ids or []):
            exists.material_ids=[*(exists.material_ids or []),material_id]
    db.flush()
    db.info.pop(('rate_capability_categories',org.id),None)


def validate_scope(db,org_id,main,sub,*,require_approval=False):
    parent=db.get(Category,main) if main else None;leaf=db.get(Category,sub) if sub else parent
    if not parent or not parent.active or parent.parent_id or not leaf or not leaf.active or (sub and leaf.parent_id!=main):raise HTTPException(422,'Material category hierarchy is inactive or invalid')
    selected=capability_categories(db,org_id)
    if require_approval and selected.get(sub or main)!='approved':raise HTTPException(422,'Capability approval is required by the current RFQ policy')


def validate_unit(db,unit):
    code=normalize_unit(unit or '')
    configured=db.scalar(select(MeasurementUnit).where(MeasurementUnit.code==code))
    if not code or (configured and not configured.active) or (not configured and code not in UNIT_NAMES):raise HTTPException(422,'Select an active approved measurement unit')
    return code


def validate_material(db,org_id,material_id,unit=None,*,allow_pending=False,require_approval=False):
    material=db.get(Material,material_id) if material_id else None
    if not material or not material.active:raise HTTPException(422,'Select an active Material Master item')
    main,sub=category_pair(db,material)
    validate_scope(db,org_id,main,sub,require_approval=require_approval)
    canonical=validate_unit(db,material.unit if unit is None else unit)
    # No approved conversion table exists. Aliases are safe; arbitrary unit
    # conversions must be entered as a separate, clearly custom private item.
    if canonical!=normalize_unit(material.unit):raise HTTPException(422,'No approved unit conversion exists for this material. Use a custom private item with the required unit instead.')
    return material,canonical


def validate_saved_rate(db,item):
    if item.material_id:return validate_material(db,item.org_id,item.material_id,item.unit)
    validate_scope(db,item.org_id,item.main_category_id,item.subcategory_id)
    return None,validate_unit(db,item.unit)


def custom_identity(main,sub,description,unit):
    return sha256('\x1f'.join([main or '',sub or '',normalize_text(description),unit]).encode()).hexdigest()


def save_rate(db,user,payload:PriceIn,item=None,*,expected_version=None):
    organization=supplier(db,user,payload.org_id,lock=True)
    if item and (item.org_id!=payload.org_id or (item.material_id and payload.material_id and item.material_id!=payload.material_id)):raise HTTPException(422,'Cannot change rate organization or material; create a separate rate')
    expected=expected_version if expected_version is not None else payload.expected_version
    if item and expected is not None and expected!=item.version:raise HTTPException(409,'This rate changed. Reload before saving your edits.')
    if payload.material_id:
        material=db.get(Material,payload.material_id)
        if not material or not material.active:raise HTTPException(422,'Select an active Material Master item')
        main,sub=category_pair(db,material)
        ensure_material_capability(db,organization,main,sub,material.id)
        material,unit=validate_material(db,payload.org_id,payload.material_id,payload.unit)
        if payload.main_category_id and payload.main_category_id!=main or payload.subcategory_id and payload.subcategory_id!=sub:raise HTTPException(422,'Selected categories do not match the Material Master item')
        description=payload.supplier_description or (item.supplier_description if item else '') or payload.offered_description or material.name_en
        custom_key=None
    else:
        material=None;main=payload.main_category_id;sub=payload.subcategory_id
        parent=db.get(Category,main) if main else None;leaf=db.get(Category,sub) if sub else None
        if not parent or not parent.active or parent.parent_id or not leaf or not leaf.active or leaf.parent_id!=main:
            raise HTTPException(422,'Material category hierarchy is inactive or invalid')
        ensure_material_capability(db,organization,main,sub)
        validate_scope(db,payload.org_id,main,sub)
        unit=validate_unit(db,payload.unit);description=payload.supplier_description
        custom_key=custom_identity(main,sub,description,unit)
    if payload.valid_until and utc(payload.valid_until)<=datetime.now(timezone.utc):raise HTTPException(422,'Price validity must be in future')
    if payload.active:
        query=select(PriceBookItem.id).where(PriceBookItem.org_id==payload.org_id,PriceBookItem.price_basis==payload.price_basis,PriceBookItem.active.is_(True))
        query=query.where(PriceBookItem.material_id==payload.material_id) if material else query.where(PriceBookItem.material_id.is_(None),PriceBookItem.custom_key==custom_key)
        if item:query=query.where(PriceBookItem.id!=item.id)
        if db.scalar(query):raise HTTPException(409,'An active rate already exists for this material and price basis. Edit it instead.')
    before=record(item) if item else None
    values=payload.model_dump(exclude={'expected_version','unit'})
    values.update(main_category_id=main,subcategory_id=sub,supplier_description=description,custom_key=custom_key)
    if item:
        for key,value in values.items():setattr(item,key,value)
        item.version+=1
    else:item=PriceBookItem(**values);db.add(item)
    item.unit=unit;item.material_version=material.version if material else 1;item.updated_at=datetime.now(timezone.utc)
    db.flush()
    audit(db,user,'price_book.updated' if before else 'price_book.created',item.org_id,item.id,before=before,after=record(item))
    return item


def rate_record(db,item,material=None,categories=None):
    material=material or (db.get(Material,item.material_id) if item.material_id else None)
    main,sub=category_pair(db,material) if material else (item.main_category_id,item.subcategory_id)
    if categories is None:categories={c.id:c for c in db.scalars(select(Category).where(Category.id.in_([main,sub]))).all()}
    stale=material and (not material.active or normalize_unit(material.unit)!=item.unit or material.version!=item.material_version)
    state='inactive' if not item.active else 'expired' if item.valid_until and utc(item.valid_until)<=datetime.now(timezone.utc) else 'review' if stale else 'active'
    selected=capability_categories(db,item.org_id)
    capability_status=selected.get(sub or main,'pending')
    return {**record(item),'main_category_id':main,'subcategory_id':sub,'main_category_name':categories[main].name_en if main in categories else '', 'subcategory_name':categories[sub].name_en if sub in categories else '', 'material_description':item.supplier_description or (material.name_en if material else ''),'status':state,'capability_status':capability_status}


def policy_record(db,org):
    policy=org.quote_automation or {}
    values=QuoteAutomationIn.model_validate(policy).model_dump(mode='json') if policy else QuoteAutomationIn().model_dump(mode='json')
    ids=values['material_ids']
    materials=db.scalars(select(Material).where(Material.id.in_(ids))).all() if ids else []
    return {**values,'revision':policy.get('revision',0),'authorized_by':policy.get('authorized_by'),'authorized_at':policy.get('authorized_at'),'materials':[{'id':m.id,'description':m.name_en,'unit':m.unit} for m in materials]}

def save_policy(db,user,org_id,payload:QuoteAutomationIn):
    org=supplier(db,user,org_id,manage=True,lock=True)
    if payload.enabled:
        if utc(payload.valid_until)<=datetime.now(timezone.utc):raise HTTPException(422,'Automatic authorization expiry must be in the future')
        if org.verified_status!='verified':raise HTTPException(422,'Verify the company before enabling automatic submission')
        rates=db.scalars(select(PriceBookItem).where(PriceBookItem.org_id==org.id,PriceBookItem.material_id.in_(payload.material_ids),PriceBookItem.price_basis==payload.price_basis,PriceBookItem.active.is_(True))).all()
        ready={r.material_id:r for r in rates if r.technical_confirmed and r.valid_until and utc(r.valid_until)>datetime.now(timezone.utc)}
        for material_id in payload.material_ids:
            material,unit=validate_material(db,org.id,material_id,require_approval=get_settings().require_capability_approval)
            rate=ready.get(material_id)
            if not rate or rate.material_version!=material.version or rate.unit!=unit:raise HTTPException(422,'Automatic coverage needs active, technically confirmed, unexpired rates matching the current Material Master')
        cities={c.casefold() for c in db.scalars(select(Capability.city).where(Capability.org_id==org.id,*eligibility_predicates())).all()}
        if any(c.casefold() not in cities for c in payload.service_cities):raise HTTPException(422,'Automatic service cities require approved capabilities')
    before=org.quote_automation or {}
    org.quote_automation={**payload.model_dump(mode='json'),'revision':before.get('revision',0)+1,'authorized_by':user.id,'authorized_at':datetime.now(timezone.utc).isoformat()}
    audit(db,user,'quotation.automation_authorized' if payload.enabled else 'quotation.automation_disabled',org.id,org.id,before=before,after=org.quote_automation)
    db.flush();return policy_record(db,org)
