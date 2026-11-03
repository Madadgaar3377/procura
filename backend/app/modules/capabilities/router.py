from app.response_contracts import CapabilityOut
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select,func
from app.database import get_db
from app.models import Capability,Organization,Category,Material,User
from app.schemas import CapabilityIn, CapabilitySelection, CapabilityEligibilityIn
from app.security import current_user,org_member,audit
from app.serializers import record

router=APIRouter(prefix='/capabilities',tags=['Supplier Capabilities'])

@router.put('/selections',response_model=list[CapabilityOut])
def save_selections(payload:CapabilitySelection,user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Replace only the selected service city's category declarations atomically.

    Unchanged records retain their approval; new records always need approval.
    Other cities and legacy main-category declarations are preserved.
    """
    org_member(db,user,payload.org_id,{'owner','manager'})
    org=db.scalar(select(Organization).where(Organization.id==payload.org_id).with_for_update())
    if not org or not org.is_supplier:raise HTTPException(422,'Company is not a supplier')
    mains=set(payload.main_category_ids);subs=set(payload.subcategory_ids)
    categories={c.id:c for c in db.scalars(select(Category).where(Category.id.in_(mains|subs))).all()}
    if any(i not in categories or not categories[i].active or categories[i].parent_id for i in mains):
        raise HTTPException(422,'Select active main categories')
    if any(i not in categories or not categories[i].active or categories[i].parent_id not in mains for i in subs):
        raise HTTPException(422,'Every subcategory must belong to a selected main category')
    city=payload.city.strip()
    existing=db.scalars(select(Capability).where(Capability.org_id==org.id,func.lower(func.trim(Capability.city))==city.casefold())).all()
    existing_ids={c.category_id for c in existing}
    for cap in existing:
        category=db.get(Category,cap.category_id)
        if category and category.active and category.parent_id and cap.category_id not in subs:
            # Preserve explicit admin denials: deselect/reselect must not reset them.
            if cap.eligibility_status!='enabled':cap.accepts_rfqs=False
            else:db.delete(cap)
    for category_id in subs-existing_ids:
        db.add(Capability(org_id=org.id,category_id=category_id,city=city,confirmed=False))
    audit(db,user,'supplier.capabilities_selected',org.id,category_ids=sorted(subs),city=city)
    db.commit()
    return [record(c) for c in db.scalars(select(Capability).where(Capability.org_id==org.id)).all()]

@router.post('',status_code=201,response_model=CapabilityOut)
def create(payload:CapabilityIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,payload.org_id,{'owner','manager'})
    org=db.get(Organization,payload.org_id)
    if not org or not org.is_supplier:raise HTTPException(422,'Company is not a supplier')
    category=db.get(Category,payload.category_id)
    if not category or not category.active:raise HTTPException(404,'Active category not found')
    existing=db.scalar(select(Capability).where(Capability.org_id==payload.org_id,Capability.category_id==payload.category_id,func.lower(func.trim(Capability.city))==payload.city.strip().casefold()))
    if existing:raise HTTPException(409,'Capability already exists')
    # Supplier self-declaration does not equal platform-verified capability.
    material_ids=sorted(set(payload.material_ids))
    if material_ids:
        materials=db.scalars(select(Material).where(Material.id.in_(material_ids),Material.active.is_(True))).all()
        if len(materials)!=len(material_ids):raise HTTPException(422,'Every selected material must exist and be active')
        if any(payload.category_id not in {m.category_id,m.main_category_id,m.subcategory_id} for m in materials):
            raise HTTPException(422,'Selected materials must belong to the declared category hierarchy')
    values=payload.model_dump();values['material_ids']=material_ids;values['city']=payload.city.strip()
    c=Capability(**{**values,'confirmed':False});db.add(c)
    db.flush();audit(db,user,'supplier.capability_declared',payload.org_id,c.id)
    db.commit();return record(c)

@router.get('/{org_id}',response_model=list[CapabilityOut])
def list_capabilities(org_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id)
    return [record(c) for c in db.scalars(select(Capability).where(Capability.org_id==org_id)).all()]

@router.post('/{capability_id}/confirm',response_model=CapabilityOut)
def confirm(capability_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if not user.is_admin:raise HTTPException(403,'Admin required')
    cap=db.get(Capability,capability_id)
    if not cap:raise HTTPException(404,'Capability not found')
    cap.confirmed=True;audit(db,user,'supplier.capability_confirmed',cap.org_id,cap.id);db.commit()
    return record(cap)

@router.patch('/{capability_id}/eligibility',response_model=CapabilityOut)
def eligibility(capability_id:str,payload:CapabilityEligibilityIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if not user.is_admin:raise HTTPException(403,'Admin required')
    cap=db.get(Capability,capability_id)
    if not cap:raise HTTPException(404,'Capability not found')
    previous=cap.eligibility_status;cap.eligibility_status=payload.eligibility_status
    audit(db,user,'supplier.capability_eligibility_changed',cap.org_id,cap.id,before=previous,after=cap.eligibility_status)
    db.commit();return record(cap)
