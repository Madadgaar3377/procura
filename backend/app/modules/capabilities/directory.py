from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.models import Capability, Category, Material, Organization
from app.modules.capabilities.public_details import published_details
from app.response_contracts import PublicSupplier

router=APIRouter(prefix='/directory',tags=['Verified Supplier Directory'])


def public_profiles(db: Session, organizations, include_categories=False):
    """Only approved, active capability labels; never contacts or private rates."""
    if not include_categories:
        return [{'id':org.id,'name':org.name,'city':org.city,'verified':True} for org in organizations]
    ids=[org.id for org in organizations]
    main=aliased(Category)
    labels={org_id:set() for org_id in ids}
    if ids:
        rows=db.execute(select(Capability.org_id,Category.name_en,Category.name_ar,
            main.name_en,main.name_ar,Category.parent_id).join(Category,Category.id==Capability.category_id)
            .outerjoin(main,main.id==Category.parent_id).where(Capability.org_id.in_(ids),
                Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),
                Capability.eligibility_status=='enabled',Category.active.is_(True),
                or_(Category.parent_id.is_(None),main.active.is_(True)))).all()
        for org_id,name,name_ar,parent,parent_ar,parent_id in rows:
            labels[org_id].add((parent if parent_id else name,(parent_ar or parent) if parent_id else (name_ar or name)))
    return [{'id':org.id,'name':org.name,'city':org.city,'verified':True,
             'main_categories':[en for en,ar in sorted(labels[org.id])],
             'main_categories_ar':[ar for en,ar in sorted(labels[org.id])]}
            for org in organizations]

@router.get('/suppliers',response_model=list[PublicSupplier],response_model_exclude_unset=True)
def discover(db:Annotated[Session,Depends(get_db)],category_id:str|None=None,city:str|None=None,q:str=Query('',max_length=80),
             main_category_id:str|None=None,subcategory_id:str|None=None,
             service_city:str|None=Query(None,max_length=100),verified:bool|None=None,
             page:int=Query(1,ge=1,le=10000),limit:int=Query(25,ge=1,le=100),
             include_categories:bool=False,include_coverage:bool=False):
    # A set of organization IDs keeps pagination stable across multiple capabilities.
    parent=aliased(Category)
    capability=select(Capability.org_id).join(Category,Category.id==Capability.category_id).outerjoin(
        parent,parent.id==Category.parent_id).where(
        Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),
        Capability.eligibility_status=='enabled',Category.active.is_(True),
        or_(Category.parent_id.is_(None),parent.active.is_(True)))
    if main_category_id:
        capability=capability.where(or_(and_(Category.id==main_category_id,Category.parent_id.is_(None)),Category.parent_id==main_category_id))
    if subcategory_id:
        capability=capability.where(Category.id==subcategory_id,Category.parent_id.is_not(None))
    if category_id:
        capability=capability.where(or_(Category.id==category_id,Category.parent_id==category_id))
    coverage=(service_city or city or '').strip()
    if coverage:
        # Literal comparison: wildcard characters must never broaden service coverage.
        capability=capability.where(func.lower(func.trim(Capability.city))==coverage.lower())
    query=select(Organization.id,Organization.name,Organization.city).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),
        Organization.verified_status=='verified')
    if verified is False:
        query=query.where(False)
    normalized=' '.join(q.split())
    if normalized:
        escaped=normalized.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        pattern=f'%{escaped}%'
        matching=capability.where(or_(Category.name_en.ilike(pattern,escape='\\'),
            Category.name_ar.ilike(pattern,escape='\\'),parent.name_en.ilike(pattern,escape='\\'),
            parent.name_ar.ilike(pattern,escape='\\'),Capability.trade_keywords.ilike(pattern,escape='\\')))
        query=query.where(or_(Organization.name.ilike(pattern,escape='\\'),Organization.id.in_(matching)))
    if main_category_id or subcategory_id or category_id or coverage:
        query=query.where(Organization.id.in_(capability))
    organizations=db.execute(query.order_by(Organization.name,Organization.id).offset((page-1)*limit).limit(limit)).all()
    profiles=public_profiles(db,organizations,include_categories and not include_coverage)
    if include_coverage:
        add_coverage(db,profiles,include_categories)
    return profiles


def add_coverage(db,profiles,include_categories=False,include_groups=False,shared_ids=None):
    ids=[profile['id'] for profile in profiles]
    if not ids:
        return
    parent=aliased(Category)
    rows=db.execute(select(Capability.org_id,Capability.city,Category.name_en,Category.name_ar,
        parent.name_en.label("main_en"),parent.name_ar.label("main_ar"),
        Category.id.label("category_id"),Category.parent_id,Capability.trade_keywords,Capability.material_ids,
        Capability.mapping_source).join(
        Category,Category.id==Capability.category_id).outerjoin(parent,parent.id==Category.parent_id).where(
        Capability.org_id.in_(ids),Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),
        Capability.eligibility_status=='enabled',Category.active.is_(True),
        or_(Category.parent_id.is_(None),parent.active.is_(True)))).all()
    grouped={org_id:[] for org_id in ids}
    for row in rows:
        grouped[row.org_id].append(row)
    material_ids={item for row in rows for item in (row.material_ids or [])}
    material_names={}
    if material_ids:
        material_names={m.id:(m.name_en,m.unit or '') for m in db.scalars(select(Material).where(
            Material.id.in_(material_ids),Material.active.is_(True))).all()}
    for profile in profiles:
        selected=grouped[profile['id']]
        if include_categories:
            labels=sorted({(row.main_en or row.name_en,row.main_ar or row.main_en or row.name_ar or row.name_en) for row in selected})
            profile['main_categories']=[en for en,ar in labels]
            profile['main_categories_ar']=[ar for en,ar in labels]
        if include_groups:
            groups={}
            shared={}
            for row in selected:
                main_id=row.parent_id or row.category_id
                group=groups.setdefault(main_id,{'id':main_id,'name_en':row.main_en or row.name_en,
                    'name_ar':row.main_ar or row.main_en or row.name_ar or row.name_en,'subcategories':{}})
                if row.parent_id:
                    child=group['subcategories'].setdefault(row.category_id,{'id':row.category_id,'name_en':row.name_en,
                        'name_ar':row.name_ar or row.name_en,'trade_keywords':set(),'materials':set(),'units':set()})
                    child['trade_keywords'].update(part.strip() for part in (row.trade_keywords or '').split('|') if part.strip())
                    for material_id in row.material_ids or []:
                        if material_id in material_names:
                            material_name,unit=material_names[material_id]
                            child['materials'].add(material_name)
                            if unit:child['units'].add(unit)
                    if shared_ids and row.category_id in shared_ids:shared[row.category_id]=child
            profile['capability_groups']=[{**g,'subcategories':[
                {**child,'trade_keywords':sorted(child['trade_keywords']),'materials':sorted(child['materials']),'units':sorted(child['units'])}
                for child in sorted(g['subcategories'].values(),key=lambda c:(c['name_en'],c['id']))]}
                for g in sorted(groups.values(),key=lambda c:(c['name_en'],c['id']))]
            if shared_ids is not None:profile['shared_subcategories']=[
                {**child,'trade_keywords':sorted(child['trade_keywords']),'materials':sorted(child['materials']),'units':sorted(child['units'])}
                for child in sorted(shared.values(),key=lambda c:(c['name_en'],c['id']))[:3]]
        profile['service_cities']=sorted({row.city.strip() for row in selected if row.city and row.city.strip()
            and row.mapping_source!='supplier_import'})
        profile['capability_labels']=sorted({row.name_en for row in selected})
        profile['capability_labels_ar']=sorted({row.name_ar or row.name_en for row in selected})

@router.get('/suppliers/{supplier_id}',response_model=PublicSupplier,response_model_exclude_unset=True)
def supplier_detail(db:Annotated[Session,Depends(get_db)],supplier_id:str,include_categories:bool=False,include_coverage:bool=False,include_profile:bool=False):
    from fastapi import HTTPException
    org=db.execute(select(Organization.id,Organization.name,Organization.city).where(
        Organization.id==supplier_id,Organization.is_supplier.is_(True),
        Organization.is_active.is_(True),Organization.verified_status=='verified')).first()
    if not org:
        raise HTTPException(404,'Supplier not found')
    profiles=public_profiles(db,[org],include_categories and not (include_coverage or include_profile))
    if include_coverage or include_profile:add_coverage(db,profiles,include_categories,include_groups=include_profile)
    if include_profile:profiles[0]['profile']=published_details(db,supplier_id)
    return profiles[0]


@router.get('/suppliers/{supplier_id}/related',response_model=list[PublicSupplier],response_model_exclude_unset=True)
def related_suppliers(supplier_id:str,db:Annotated[Session,Depends(get_db)],limit:int=Query(4,ge=1,le=8)):
    from fastapi import HTTPException
    current=db.scalar(select(Organization.id).where(Organization.id==supplier_id,
        Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.verified_status=='verified'))
    if not current:raise HTTPException(404,'Supplier not found')
    parent=aliased(Category)
    eligible=select(Capability.org_id.label('org_id'),
        func.coalesce(Category.parent_id,Category.id).label('main_id'),
        case((Category.parent_id.is_not(None),Category.id),else_=None).label('sub_id'),
        func.lower(func.trim(Capability.city)).label('city')).join(Category,Category.id==Capability.category_id).outerjoin(
        parent,parent.id==Category.parent_id).where(Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),
        Capability.eligibility_status=='enabled',Category.active.is_(True),
        or_(Category.parent_id.is_(None),parent.active.is_(True)))
    source=eligible.where(Capability.org_id==supplier_id).cte('source_capabilities')
    main_ids=select(source.c.main_id).distinct()
    candidate=eligible.where(Capability.org_id!=supplier_id,or_(Category.id.in_(main_ids),Category.parent_id.in_(main_ids))).cte('candidate_capabilities')
    sub_ids=select(source.c.sub_id).where(source.c.sub_id.is_not(None)).distinct()
    city_ids=select(source.c.city).where(source.c.city!='').distinct()
    sub_overlap=func.count(func.distinct(case((candidate.c.sub_id.in_(sub_ids),candidate.c.sub_id),else_=None)))
    main_overlap=func.count(func.distinct(candidate.c.main_id))
    city_overlap=func.count(func.distinct(case((and_(candidate.c.city.in_(city_ids),candidate.c.city!=''),candidate.c.city),else_=None)))
    query=select(Organization.id,Organization.name,Organization.city).join(candidate,candidate.c.org_id==Organization.id).where(Organization.id!=supplier_id,
        Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.verified_status=='verified').group_by(
        Organization.id,Organization.name,Organization.city).order_by(
        sub_overlap.desc(),main_overlap.desc(),city_overlap.desc(),Organization.name,Organization.id).limit(limit)
    organizations=db.execute(query).all()
    if not organizations:return []
    shared_ids=set(db.scalars(select(source.c.sub_id).where(source.c.sub_id.is_not(None)).distinct()).all())
    profiles=public_profiles(db,organizations)
    add_coverage(db,profiles,include_categories=True,include_groups=True,shared_ids=shared_ids)
    for profile in profiles:profile.pop('capability_groups',None)
    return profiles
