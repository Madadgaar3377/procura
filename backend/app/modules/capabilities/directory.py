from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.models import Capability, Category, Material, Organization
from app.modules.capabilities.public_details import published_details
from app.response_contracts import PublicSupplier, PublicSupplierPage, PublicSupplierProductPage
from app.response_cache import cached_get

router=APIRouter(prefix='/directory',tags=['Verified Supplier Directory'])

@router.get('/suppliers/{supplier_id}/products',response_model=PublicSupplierProductPage)
@cached_get('public-supplier-products', 120)
def supplier_products(supplier_id:str,db:Annotated[Session,Depends(get_db)],q:str=Query('',max_length=100),
        main_category_id:str|None=None,subcategory_id:str|None=None,page:int=Query(1,ge=1,le=10000),
        limit:int=Query(20,ge=1,le=100)):
    """Paginated public product view built only from approved capabilities and linked Material Master rows."""
    from fastapi import HTTPException
    org=db.scalar(select(Organization.id).where(Organization.id==supplier_id,Organization.is_supplier.is_(True),
        Organization.is_active.is_(True),Organization.verified_status=='verified'))
    if not org:raise HTTPException(404,'Supplier not found')
    capabilities=db.execute(select(Capability,Category).join(Category,Category.id==Capability.category_id).where(
        Capability.org_id==supplier_id,Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),
        Capability.eligibility_status=='enabled',Category.active.is_(True))).all()
    sub_ids={category.id for _,category in capabilities if category.parent_id}
    main_ids={category.parent_id or category.id for _,category in capabilities}
    if main_category_id:main_ids &= {main_category_id}
    if subcategory_id:sub_ids &= {subcategory_id}
    caps=[(cap,cat) for cap,cat in capabilities if (cat.parent_id or cat.id) in main_ids and
        (not subcategory_id or cat.id==subcategory_id) and (not main_category_id or cat.parent_id==main_category_id or cat.id==main_category_id)]
    ids={material_id for cap,_ in caps for material_id in (cap.material_ids or [])}
    materials=db.scalars(select(Material).where(Material.id.in_(ids),Material.active.is_(True))).all() if ids else []
    taxonomy_ids=main_ids|sub_ids|{value for material in materials for value in (material.category_id,material.main_category_id,material.subcategory_id) if value}
    taxonomy={category.id:category for category in db.scalars(select(Category).where(Category.id.in_(taxonomy_ids))).all()}
    rows=[];needle=' '.join(q.split()).casefold()
    for material in materials:
        main_id=material.main_category_id or (taxonomy[material.category_id].parent_id if material.category_id in taxonomy else None) or material.category_id
        sub_id=material.subcategory_id or (material.category_id if material.category_id!=main_id else None)
        main=taxonomy.get(main_id);sub=taxonomy.get(sub_id) if sub_id else None
        if not main or (main_category_id and main.id!=main_category_id) or (subcategory_id and (not sub or sub.id!=subcategory_id)):continue
        haystack=' '.join([material.name_en,material.name_ar,material.description,material.arabic_description,material.unit,
            str(material.specs_template or {}),main.name_en,main.name_ar,sub.name_en if sub else '',sub.name_ar if sub else '']).casefold()
        if needle and needle not in haystack:continue
        structured=dict(material.specs_template or {})
        normalized_fields={str(key).strip().casefold().replace(' ','_'):value for key,value in structured.items()}
        brand=next((str(normalized_fields[key]).strip() for key in ('brand','manufacturer')
                    if normalized_fields.get(key) not in (None,'')), '')
        model=next((str(normalized_fields[key]).strip() for key in ('model','model_number')
                    if normalized_fields.get(key) not in (None,'')), '')
        rows.append({'id':material.id,'name':material.name_en,'name_ar':material.name_ar or material.arabic_description or '',
            'description':material.description or '', 'description_ar':material.arabic_description or '',
            'brand':brand,'model':model,
            'unit':material.unit,'specifications':material.specs_template or {},'main_category_id':main.id,'main_category':main.name_en,
            'main_category_ar':main.name_ar or main.name_en,'subcategory_id':sub.id if sub else None,
            'subcategory':sub.name_en if sub else None,'subcategory_ar':(sub.name_ar or sub.name_en) if sub else '',
            'source':'material_master'})
    seen_descriptions=set()
    for cap,cat in caps:
        main=taxonomy.get(cat.parent_id) if cat.parent_id else cat
        for idx,description in enumerate(x.strip() for x in (cap.trade_keywords or '').split(' | ') if x.strip()):
            identity=(cat.id,description.casefold())
            if identity in seen_descriptions or (needle and needle not in f'{description} {cat.name_en} {main.name_en}'.casefold()):continue
            seen_descriptions.add(identity)
            rows.append({'id':f'{cap.id}:description:{idx}','name':description,'name_ar':'','description':'','description_ar':'','brand':'','model':'',
                'unit':None,'specifications':{},'main_category_id':main.id,'main_category':main.name_en,
                'main_category_ar':main.name_ar or main.name_en,'subcategory_id':cat.id if cat.parent_id else None,
                'subcategory':cat.name_en if cat.parent_id else None,'subcategory_ar':(cat.name_ar or cat.name_en) if cat.parent_id else '',
                'source':'supplier_description'})
    rows.sort(key=lambda row:(row['main_category'].casefold(),(row['subcategory'] or '').casefold(),row['name'].casefold(),row['id']))
    total=len(rows);start=(page-1)*limit
    return {'items':rows[start:start+limit],'total':total,'page':page,'limit':limit}


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

@router.get('/suppliers',response_model=list[PublicSupplier]|PublicSupplierPage,response_model_exclude_unset=True)
@cached_get('public-supplier-directory', 60)
def discover(db:Annotated[Session,Depends(get_db)],category_id:str|None=None,city:str|None=None,q:str=Query('',max_length=80),
             main_category_id:str|None=None,subcategory_id:str|None=None,
             service_city:str|None=Query(None,max_length=100),verified:bool|None=None,
             page:int=Query(1,ge=1,le=10000),limit:int=Query(50,ge=1,le=100),
             include_categories:bool=False,include_coverage:bool=False,paginated:bool=False):
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
    total = db.scalar(select(func.count()).select_from(query.subquery())) if paginated else None
    organizations=db.execute(query.order_by(Organization.name,Organization.id).offset((page-1)*limit).limit(limit)).all()
    profiles=public_profiles(db,organizations,include_categories and not include_coverage)
    if include_coverage:
        add_coverage(db,profiles,include_categories)
    if paginated:
        return {'items':profiles,'total':total,'page':page,'limit':limit}
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
@cached_get('public-supplier-profile', 120)
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
@cached_get('public-related-suppliers', 60)
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
