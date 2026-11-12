import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy import and_, case, func, or_, select, inspect, table, column, delete, update
from sqlalchemy.orm import Session, aliased

from app import catalog_cache
from app.response_cache import cached_get
from app.database import get_db
from app.models import Category, Material, MaterialRevision, MeasurementUnit, User, uid, Capability
from app.modules.taxonomy.category_imports import router as category_imports
from app.response_contracts import (CategoryOut, CategorySummary, IndustryCatalogPage,
                                    IndustryTaxonomyReviewCandidate, MaterialOut, SubcategoryMaterialOut)
from app.schemas import CategoryIn, CategoryEdit, SubcategoryMaterialIn, MaterialIn, MaterialMasterIn, MaterialRevisionOut, MeasurementUnitIn, MaterialFilterPage
from app.security import admin_user, audit
from app.serializers import record
from app.modules.materials.service import (
    UNIT_NAMES, create_material_record, category_code, ensure_unit, find_exact_material, material_identity,
    material_snapshot, normalize_category_name, normalize_text, resolve_category,
)

from app.response_contracts import UnitOptionOut

logger=logging.getLogger(__name__)


class TaxonomyRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handle(request):
            try:
                return await original(request)
            except (IntegrityError,ProgrammingError) as error:
                state=getattr(error.orig,'sqlstate',None)
                constraint=getattr(getattr(error.orig,'diag',None),'constraint_name',None)
                # Never log SQL parameters, descriptions or connection details.
                logger.error('Taxonomy database error type=%s sqlstate=%s constraint=%s method=%s path=%s',
                    type(error.orig).__name__,state,constraint,request.method,request.url.path)
                if isinstance(error,IntegrityError):
                    raise HTTPException(409,'This record already exists or has linked records. Refresh the table and check the category and material.') from error
                if state in {'42703','42P01'}:
                    raise HTTPException(503,'A database update is required before this action can finish. Ask your administrator to run the pending backend migrations.') from error
                raise HTTPException(500,'The database could not complete this category action. Your administrator can check the backend diagnostic log.') from error
        return handle


router=APIRouter(prefix='/taxonomy',tags=['Material Master'],route_class=TaxonomyRoute)
router.include_router(category_imports)

@router.get('/industry-review-candidates',response_model=list[IndustryTaxonomyReviewCandidate])
def industry_review_candidates(user:User=Depends(admin_user),db:Session=Depends(get_db),
                               limit:int=Query(100,ge=1,le=250),offset:int=Query(0,ge=0)):
    """Read-only review queue for suspicious active labels or missing discipline assignments."""
    parent=aliased(Category)
    known=('civil','electrical','mechanical')
    name_flags=[func.lower(Category.name_en).like(f'%{word}%') for word in ('qa','test','sample','bulk')]
    name_flags.extend(func.lower(parent.name_en).like(f'%{word}%') for word in ('qa','test','sample','bulk'))
    material_link=or_(Material.category_id==Category.id,Material.main_category_id==Category.id,
                      Material.subcategory_id==Category.id)
    unknown_discipline=select(Material.id).where(material_link,Material.active.is_(True),or_(
        Material.discipline.is_(None),func.lower(Material.discipline).not_in(known))).exists()
    linked_category=or_(Material.subcategory_id==Category.id,and_(Material.subcategory_id.is_(None),
        Material.category_id==Category.id))
    mismatched_parent=select(Material.id).where(linked_category,Material.active.is_(True),
        Material.main_category_id.is_not(None),Material.main_category_id!=func.coalesce(Category.parent_id,Category.id)).exists()
    supported_material=select(Material.id).where(material_link,Material.active.is_(True),
        func.lower(Material.discipline).in_(known)).exists()
    rows=db.execute(select(Category,parent,(~supported_material).label('no_supported_material'),
        mismatched_parent.label('mismatched_parent')).outerjoin(parent,parent.id==Category.parent_id).where(
        Category.active.is_(True),or_(*name_flags,unknown_discipline,mismatched_parent,~supported_material)).distinct()
        .order_by(Category.name_en,Category.id).offset(offset).limit(limit)).all()
    return [{'id':category.id,'code':category.code,'name_en':category.name_en,'name_ar':category.name_ar or '',
        'parent_id':category.parent_id,'parent_name_en':parent.name_en if parent else None,
        'reason':'Category label resembles test or bulk data; review before public use.'
            if any(word in (category.name_en+' '+(parent.name_en if parent else '')).casefold()
                   for word in ('qa','test','sample','bulk'))
            else 'Material main category and subcategory parent do not agree.'
                if mismatched
            else 'No active Material Master record assigns a supported discipline to this category.'
                if no_supported else 'Linked active material has no supported discipline assignment.'}
        for category,parent,no_supported,mismatched in rows]

@router.get('/industry-catalog',response_model=IndustryCatalogPage)
@cached_get('public-industry-catalog', 300)
def industry_catalog(q:str=Query('',max_length=100),discipline:str|None=None,
                     page:int=Query(1,ge=1,le=10000),limit:int=Query(9,ge=1,le=24),
                     db:Session=Depends(get_db)):
    """Bounded public industry categories with grouped Material Master previews."""
    names=('civil','electrical','mechanical')
    discipline_labels={'civil':('Civil','مدني'),'electrical':('Electrical','كهربائي'),'mechanical':('Mechanical','ميكانيكي')}
    stats_card=aliased(Category);stats_parent=aliased(Category);stats_main=aliased(Category)
    stats_card_id=func.coalesce(Material.subcategory_id,Material.category_id)
    stats_main_id=func.coalesce(Material.main_category_id,stats_parent.id,stats_card.id)
    discipline_stats=db.execute(select(func.lower(Material.discipline),
        func.count(func.distinct(stats_main.id)),func.count(func.distinct(Material.id))).select_from(Material)
        .join(stats_card,stats_card.id==stats_card_id).outerjoin(stats_parent,stats_parent.id==stats_card.parent_id)
        .join(stats_main,stats_main.id==stats_main_id).where(Material.active.is_(True),
        stats_card.active.is_(True),stats_main.active.is_(True),func.lower(Material.discipline).in_(names),
        or_(Material.main_category_id.is_(None),Material.main_category_id==func.coalesce(stats_parent.id,stats_card.id)))
        .group_by(func.lower(Material.discipline))).all()
    stats={name:(int(categories or 0),int(materials or 0)) for name,categories,materials in discipline_stats}
    disciplines=[{'id':discipline_labels[name][0],'name_en':discipline_labels[name][0],
        'name_ar':discipline_labels[name][1],'category_count':stats.get(name,(0,0))[0],
        'material_count':stats.get(name,(0,0))[1]} for name in names]
    selected=discipline.casefold() if discipline else 'civil'
    if selected not in names:selected='civil'

    card=aliased(Category);parent=aliased(Category);main=aliased(Category)
    card_id=func.coalesce(Material.subcategory_id,Material.category_id)
    main_id=func.coalesce(Material.main_category_id,parent.id,card.id)
    predicates=[Material.active.is_(True),func.lower(Material.discipline)==selected,
        card.active.is_(True),main.active.is_(True),
        or_(Material.main_category_id.is_(None),Material.main_category_id==func.coalesce(parent.id,card.id))]
    normalized=' '.join(q.casefold().split())
    if normalized:
        escaped=normalized.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        pattern=f'%{escaped}%'
        predicates.append(or_(card.name_en.ilike(pattern,escape='\\'),card.name_ar.ilike(pattern,escape='\\'),
            main.name_en.ilike(pattern,escape='\\'),main.name_ar.ilike(pattern,escape='\\'),
            Material.name_en.ilike(pattern,escape='\\'),Material.name_ar.ilike(pattern,escape='\\'),
            Material.description.ilike(pattern,escape='\\'),Material.normalized_description.ilike(pattern,escape='\\')))
    grouped=(select(func.lower(Material.discipline).label('discipline'),card.id.label('id'),
        card.name_en.label('name_en'),card.name_ar.label('name_ar'),main.id.label('main_category_id'),
        main.name_en.label('main_category'),main.name_ar.label('main_category_ar'),
        func.count(func.distinct(Material.id)).label('material_count')).select_from(Material)
        .join(card,card.id==card_id).outerjoin(parent,parent.id==card.parent_id)
        .join(main,main.id==main_id).where(*predicates)
        .group_by(func.lower(Material.discipline),card.id,card.name_en,card.name_ar,main.id,main.name_en,main.name_ar))
    total=int(db.scalar(select(func.count()).select_from(grouped.subquery())) or 0)
    rows=db.execute(grouped.order_by('main_category','name_en','id')
        .offset((page-1)*limit).limit(limit)).all()
    preview_by_category={item.id:[] for item in rows}
    if rows:
        row_ids=[item.id for item in rows]
        category_key=func.coalesce(Material.subcategory_id,Material.category_id)
        material_main_id=func.coalesce(Material.main_category_id,parent.id,card.id)
        numbered=(select(Material.id,category_key.label('category_id'),material_main_id.label('main_category_id'),
            func.lower(Material.discipline).label('discipline'),Material.name_en,Material.name_ar,
            Material.description,Material.arabic_description,Material.normalized_description,Material.unit,
            func.row_number().over(partition_by=(func.lower(Material.discipline),material_main_id,category_key),
                order_by=(Material.code,Material.id)).label('position')).select_from(Material)
            .join(card,card.id==category_key).outerjoin(parent,parent.id==card.parent_id)
            .where(Material.active.is_(True),func.lower(Material.discipline)==selected,card.active.is_(True),
                material_main_id.in_({item.main_category_id for item in rows}),category_key.in_(row_ids),
                or_(Material.main_category_id.is_(None),Material.main_category_id==func.coalesce(parent.id,card.id))))
        numbered_query=numbered.subquery()
        preview_rows=db.execute(select(numbered_query).where(numbered_query.c.position<=5)
            .order_by(numbered_query.c.main_category_id,numbered_query.c.category_id,numbered_query.c.position)).all()
        for item in preview_rows:
            if item.category_id in preview_by_category:
                preview_by_category[item.category_id].append({'id':item.id,'name_en':item.name_en,
                    'name_ar':item.name_ar or '','description':item.description or item.normalized_description or item.name_en,
                    'description_ar':item.arabic_description or item.name_ar or '',
                    'unit':item.unit or ''})
    cards=[{'id':item.id,'name_en':item.name_en,'name_ar':item.name_ar or item.name_en,
        'main_category_id':item.main_category_id,'main_category':item.main_category,
        'main_category_ar':item.main_category_ar or item.main_category,
        'subcategory_id':item.id if item.id!=item.main_category_id else None,
        'subcategory':item.name_en if item.id!=item.main_category_id else None,
        'subcategory_ar':item.name_ar or item.name_en if item.id!=item.main_category_id else '',
        'material_count':int(item.material_count or 0),'materials':preview_by_category.get(item.id,[])} for item in rows]
    return {'disciplines':disciplines,'categories':cards,'total':total,'page':page,'limit':limit}

@router.get('/categories',response_model=list[CategoryOut])
def categories(parent_id:str|None=None,q:str='',limit:int|None=Query(None,ge=1,le=250),
               offset:int=Query(0,ge=0),roots_only:bool=False,db:Session=Depends(get_db)):
    if roots_only or parent_id or q or limit is not None or offset:
        params={'parent_id':parent_id,'q':q,'limit':limit,'offset':offset,'roots_only':roots_only}
        epoch=catalog_cache.version()
        cached=catalog_cache.get('category-options-v2',params,epoch)
        if cached is not None:return cached
        query=select(Category)
        if roots_only:query=query.where(Category.parent_id.is_(None))
        if parent_id:
            query=query.where(Category.parent_id==parent_id)
        query=query.where(Category.active.is_(True))
        if q:
            if len(q)>100:raise HTTPException(422,'Category search must be 100 characters or fewer')
            term=q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            query=query.where(or_(Category.name_en.ilike(f'%{term}%',escape='\\'),Category.name_ar.ilike(f'%{term}%',escape='\\')))
        query=query.order_by(Category.sort_order,Category.name_en,Category.id).offset(offset)
        if limit is not None:query=query.limit(limit)
        value=[record(c) for c in db.scalars(query).all()]
        catalog_cache.set('category-options-v2',params,value,ttl=1800,epoch=epoch)
        return value
    cache_epoch = catalog_cache.version()
    cached = catalog_cache.get('categories', {'active': True, 'sort': 'name_en'}, cache_epoch)
    if cached is not None:
        return cached
    value = [record(c) for c in db.scalars(select(Category).where(Category.active.is_(True)).order_by(Category.name_en, Category.id)).all()]
    catalog_cache.set('categories', {'active': True, 'sort': 'name_en'}, value, ttl=1800, epoch=cache_epoch)
    return value

@router.get('/categories/archived',response_model=list[CategoryOut])
def archived_categories(user:User=Depends(admin_user),db:Session=Depends(get_db)):
    return [record(c) for c in db.scalars(select(Category).where(Category.active.is_(False)).order_by(Category.sort_order,Category.name_en).limit(500)).all()]


@router.get('/categories/manage',response_model=list[CategorySummary])
def category_summary(user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Admin category rows with active material counts; no records are rewritten."""
    cache_epoch=catalog_cache.version()
    cached=catalog_cache.get('admin-category-summary',{},cache_epoch)
    if cached is not None:return cached
    counts=dict(db.execute(select(Material.category_id,func.count()).where(Material.active.is_(True)).group_by(Material.category_id)).all())
    categories=db.scalars(select(Category).order_by(Category.name_en,Category.id)).all()
    children={}
    for c in categories:children.setdefault(c.parent_id,[]).append(c.id)
    def count(category_id,seen):
        if category_id in seen:return 0
        return counts.get(category_id,0)+sum(count(child,seen|{category_id}) for child in children.get(category_id,[]))
    result=[{**record(c),'materials_count':count(c.id,set())} for c in categories]
    catalog_cache.set('admin-category-summary',{},result,ttl=30,epoch=cache_epoch)
    return result

@router.post('/categories',status_code=201,response_model=CategoryOut)
def create_category(payload:CategoryIn,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    c=_new_category(payload,user,db)
    db.commit();catalog_cache.invalidate()
    return record(c)


def _new_category(payload,user,db):
    parent=db.get(Category,payload.parent_id) if payload.parent_id else None
    if payload.parent_id and (not parent or not parent.active):raise HTTPException(404,'Parent category not found')
    normalized=normalize_category_name(payload.name_en)
    if not normalized:raise HTTPException(422,'Enter a category name')
    if parent and parent.parent_id:raise HTTPException(422,'Choose a main category as the parent; only two levels are supported')
    candidates=db.scalars(select(Category).where(Category.parent_id==payload.parent_id,Category.active.is_(True))).all()
    if any(normalize_category_name(c.normalized_name or c.name_en)==normalized for c in candidates):
        raise HTTPException(409,'A category with this name already exists under the selected parent')
    code=(payload.code or category_code(payload.parent_id,normalized)).strip().upper()
    if db.scalar(select(Category.id).where(Category.code==code)):raise HTTPException(409,'Category code already exists')
    c=Category(code=code,name_en=payload.name_en.strip(),normalized_name=normalized,name_ar=payload.name_ar[:180],
               parent_id=payload.parent_id,active=True,sort_order=payload.sort_order)
    db.add(c);db.flush();audit(db,user,'category.created',entity_id=c.id)
    return c


@router.post('/categories/with-material',status_code=201,response_model=SubcategoryMaterialOut)
def create_subcategory_material(payload:SubcategoryMaterialIn,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Reuse/create a subcategory; optionally add a material in one transaction."""
    # Serialize saves under this parent without changing existing records.
    parent=db.scalar(select(Category).where(Category.id==payload.parent_id).with_for_update())
    if not parent or not parent.active:raise HTTPException(422,'Choose an active main category')
    if parent.parent_id:raise HTTPException(422,'Choose a main category; only two category levels are supported')
    normalized=normalize_category_name(payload.name_en)
    archived=db.scalars(select(Category).where(Category.parent_id==parent.id,Category.active.is_(False))).all()
    if any(normalize_category_name(item.name_en)==normalized for item in archived):
        raise HTTPException(409,'This subcategory is archived. Restore it before adding materials.')
    category,created=resolve_category(db,payload.name_en,parent,create=True,name_ar=payload.name_ar,sort_order=payload.sort_order)
    if created:audit(db,user,'category.created',entity_id=category.id)
    if not payload.material_description:
        db.commit();catalog_cache.invalidate()
        return {'category':record(category),'material':None}
    material=_save_master_material(MaterialMasterIn(main_category_id=payload.parent_id,subcategory_id=category.id,
        description=payload.material_description,unit=payload.unit),user,db)
    return {'category':record(category),'material':material}


@router.put('/categories/{category_id}',response_model=CategoryOut)
def edit_category(category_id:str,payload:CategoryEdit,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Rename in place without changing category IDs, material IDs or RFQ references.

    Subcategory moves update material identities; procurement snapshots stay intact.
    """
    c=db.scalar(select(Category).where(Category.id==category_id).with_for_update())
    if not c:raise HTTPException(404,'Category not found')
    if payload.parent_id!=c.parent_id:
        parent=db.scalar(select(Category).where(Category.id==payload.parent_id).with_for_update()) if payload.parent_id else None
        if not c.parent_id or not parent or parent.parent_id or not parent.active:
            raise HTTPException(422,'Choose an active main category for this subcategory')
    normalized=normalize_category_name(payload.name_en)
    if not normalized:raise HTTPException(422,'Enter a category name')
    siblings=db.scalars(select(Category).where(Category.parent_id==payload.parent_id,Category.id!=c.id)).all()
    if any(normalize_category_name(s.name_en)==normalized for s in siblings):raise HTTPException(409,'A category with this name already exists under the selected parent')
    affected=db.scalars(select(Material).where(or_(Material.category_id==c.id,Material.main_category_id==c.id,Material.subcategory_id==c.id))).all()
    c.name_en=' '.join(payload.name_en.split());c.normalized_name=normalized;c.name_ar=payload.name_ar[:180];c.sort_order=payload.sort_order
    if c.parent_id!=payload.parent_id:
        c.parent_id=payload.parent_id
        for m in affected:
            m.main_category_id=payload.parent_id;m.subcategory_id=c.id;m.category_id=c.id
            m.discipline=parent.name_en.title() if parent.name_en.casefold() in {'civil','electrical','mechanical'} else None
    identities={}
    for m in affected:
        main=db.get(Category,m.main_category_id or m.category_id)
        sub=db.get(Category,m.subcategory_id) if m.subcategory_id else None
        identities[m.id]=material_identity(main_category=main.normalized_name or main.name_en,subcategory=(sub.normalized_name or sub.name_en) if sub else '',description=m.name_en,unit=m.unit,specifications=m.description)
    for m in affected:m.source_identity=None
    db.flush()
    for m in affected:
        if db.scalar(select(Material.id).where(Material.source_identity==identities[m.id],Material.id!=m.id)):
            raise HTTPException(409,'Renaming would create duplicate material identities')
        m.source_identity=identities[m.id]
    audit(db,user,'category.updated',entity_id=c.id)
    if payload.material_description:
        material=db.get(Material,payload.material_id) if payload.material_id else None
        if payload.material_id and (not material or (material not in affected)):
            raise HTTPException(422,'Choose a material belonging to this category')
        sub_id=c.id if c.parent_id else payload.material_subcategory_id
        if not c.parent_id and not sub_id:
            raise HTTPException(422,'Choose a subcategory for the material')
        _save_master_material(MaterialMasterIn(main_category_id=c.parent_id or c.id,subcategory_id=sub_id,description=payload.material_description,unit=payload.unit),user,db,material)
    else:db.commit();catalog_cache.invalidate()
    return record(c)


@router.patch('/categories/{category_id}/archive',response_model=CategoryOut)
def archive_category(category_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Archive an unused category. Move/archive active materials and children first."""
    c=db.get(Category,category_id)
    if not c:raise HTTPException(404,'Category not found')
    if db.scalar(select(Category.id).where(Category.parent_id==c.id,Category.active.is_(True)).limit(1)):
        raise HTTPException(409,'Archive or delete active subcategories first')
    if db.scalar(select(Material.id).where(or_(Material.category_id==c.id,Material.main_category_id==c.id,Material.subcategory_id==c.id),Material.active.is_(True)).limit(1)):
        raise HTTPException(409,'Move or archive active materials in this category first')
    c.active=False;audit(db,user,'category.archived',entity_id=c.id);db.commit();catalog_cache.invalidate()
    return record(c)


@router.patch('/categories/{category_id}/restore',response_model=CategoryOut)
def restore_category(category_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Restore without changing IDs; the parent must be active."""
    c=db.get(Category,category_id)
    if not c:raise HTTPException(404,'Category not found')
    parent=db.get(Category,c.parent_id) if c.parent_id else None
    if c.parent_id and (not parent or not parent.active):raise HTTPException(409,'Restore the main category first')
    siblings=db.scalars(select(Category).where(Category.parent_id==c.parent_id,Category.id!=c.id,Category.active.is_(True))).all()
    if any(normalize_category_name(s.name_en)==normalize_category_name(c.name_en) for s in siblings):raise HTTPException(409,'An active category already has this name')
    c.active=True;audit(db,user,'category.restored',entity_id=c.id);db.commit();catalog_cache.invalidate()
    return record(c)


@router.delete('/categories/{category_id}',response_model=CategoryOut)
def delete_category(category_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Delete a category subtree and its materials; retain procurement snapshots."""
    c=db.scalar(select(Category).where(Category.id==category_id).with_for_update())
    if not c:raise HTTPException(404,'Category not found')
    all_categories=db.scalars(select(Category).with_for_update()).all()
    ordered=[c.id];seen={c.id}
    for parent_id in ordered:
        for child in all_categories:
            if child.parent_id==parent_id and child.id not in seen:
                seen.add(child.id);ordered.append(child.id)
    materials=db.scalars(select(Material.id).where(or_(Material.category_id.in_(seen),Material.main_category_id.in_(seen),Material.subcategory_id.in_(seen))).with_for_update()).all()
    # Read deployed foreign keys, so legacy RFQ tables without the newer
    # category columns remain supported. Only known dependents are removed.
    inspector=inspect(db.connection())
    foreign_keys=inspector.get_multi_foreign_keys()
    for target,ids,removable in [('materials',materials,{'material_revisions','price_book_items'}),('categories',ordered,{'supplier_capabilities'})]:
        if not ids:continue
        for (schema,table_name),keys in foreign_keys.items():
            for key in keys:
                if key['referred_table']!=target or table_name in {'categories','materials'}:continue
                columns={item['name']:item for item in inspector.get_columns(table_name,schema=schema)}
                for local,remote in zip(key['constrained_columns'],key['referred_columns']):
                    if remote!='id':continue
                    deployed=table(table_name,column(local),schema=schema)
                    condition=deployed.c[local].in_(ids)
                    if table_name=='price_book_items':
                        if target=='materials':
                            # Preserve the existing explicitly authorized cascade
                            # for linked rates despite material_id now being nullable.
                            db.execute(delete(deployed).where(condition));continue
                        if db.scalar(select(deployed.c[local]).where(condition).limit(1)):
                            raise HTTPException(409,'Private custom rates reference this category. Archive the category instead; no records were deleted.')
                    if columns[local]['nullable']:
                        db.execute(update(deployed).where(condition).values({local:None}))
                    elif table_name in removable:
                        db.execute(delete(deployed).where(condition))
                    elif db.scalar(select(deployed.c[local]).where(condition).limit(1)):
                        raise HTTPException(409,'A linked record prevents deletion; no changes were saved')
    if materials:
        for capability in db.scalars(select(Capability).where(~Capability.category_id.in_(seen))).all():
            capability.material_ids=[mid for mid in capability.material_ids if mid not in materials]
        db.execute(delete(Material).where(Material.id.in_(materials)))
    result=record(c)
    for item_id in reversed(ordered):db.execute(delete(Category).where(Category.id==item_id))
    audit(db,user,'category.deleted',entity_id=c.id,categories_deleted=len(ordered),materials_deleted=len(materials))
    db.commit();catalog_cache.invalidate()
    return result

@router.get('/units',response_model=list[UnitOptionOut])
def list_units(db:Session=Depends(get_db)):
    cache_epoch=catalog_cache.version()
    cached=catalog_cache.get('measurement-units',{},cache_epoch)
    if cached is not None:return cached
    configured={unit.code:{'code':unit.code,'name_en':unit.name_en,'sort_order':unit.sort_order}
                for unit in db.scalars(select(MeasurementUnit).where(MeasurementUnit.active.is_(True))).all()}
    disabled=set(db.scalars(select(MeasurementUnit.code).where(MeasurementUnit.active.is_(False))).all())
    for code,name in UNIT_NAMES.items():
        if code in disabled:continue
        configured.setdefault(code,{'code':code,'name_en':name,'sort_order':1000})
    result=sorted(configured.values(),key=lambda item:(item['sort_order'],item['code']))
    catalog_cache.set('measurement-units',{},result,ttl=180,epoch=cache_epoch)
    return result

@router.post('/units',status_code=201)
def create_unit(payload:MeasurementUnitIn,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    code=payload.code.strip().upper()
    if db.scalar(select(MeasurementUnit.id).where(MeasurementUnit.code==code)):
        raise HTTPException(409,'This unit is already configured')
    item=MeasurementUnit(code=code,name_en=payload.name_en.strip(),sort_order=payload.sort_order,active=True)
    db.add(item);db.flush();audit(db,user,'measurement_unit.created',entity_id=item.id);db.commit()
    catalog_cache.invalidate()
    return {'id':item.id,'code':item.code,'name_en':item.name_en,'sort_order':item.sort_order}

@router.get('/materials',response_model=list[MaterialOut])
def materials(q:str='',category_id:str|None=None,main_category_id:str|None=None,subcategory_id:str|None=None,
              discipline:str|None=None,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),autocomplete:bool=False,db:Session=Depends(get_db)):
    if len(q) > 100:
        raise HTTPException(422, 'Search text must be 100 characters or fewer')
    normalized = ' '.join(q.casefold().split())
    if autocomplete:limit=min(limit,20)
    params = {'q': normalized, 'category_id': category_id, 'main_category_id':main_category_id,'subcategory_id':subcategory_id,'discipline': discipline.casefold() if discipline else None,
              'limit': limit, 'offset': offset, 'autocomplete':autocomplete,'sort': 'relevance-or-code-v1'}
    cache_epoch = catalog_cache.version()
    cached = catalog_cache.get('taxonomy-materials', params, cache_epoch)
    if cached is not None:
        return cached
    parent_category=aliased(Category)
    query=select(Material).join(Category,Category.id==Material.category_id).outerjoin(
        parent_category,parent_category.id==Category.parent_id).where(Material.active.is_(True))
    if category_id:query=query.where(Material.category_id==category_id)
    if main_category_id:query=query.where(Material.main_category_id==main_category_id)
    if subcategory_id:query=query.where(Material.subcategory_id==subcategory_id)
    if discipline:
        if discipline.casefold() not in {'civil','electrical','mechanical'}:raise HTTPException(422,'Discipline must be Civil, Electrical, or Mechanical')
        query=query.where(Material.discipline==discipline.title())
    if normalized:
        escaped=normalized.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        predicates=[Material.code.ilike(f'{escaped}%',escape='\\'),Material.name_en.ilike(f'{escaped}%',escape='\\'),Material.name_ar.ilike(f'{escaped}%',escape='\\'),
                    Material.source_material_code.ilike(f'{escaped}%',escape='\\'),Material.expenditure_code.ilike(f'{escaped}%',escape='\\'),
                    Category.name_en.ilike(f'{escaped}%',escape='\\'),parent_category.name_en.ilike(f'{escaped}%',escape='\\')]
        predicates.append(Material.name_en.ilike(f'%{escaped}%',escape='\\'))
        if len(normalized)>=3:
            term=f'%{escaped}%'
            predicates.extend([Material.code.ilike(term,escape='\\'),Material.name_en.ilike(term,escape='\\'),Material.normalized_description.ilike(term,escape='\\'),Material.name_ar.ilike(term,escape='\\'),
                               Material.description.ilike(term,escape='\\'),Material.source_material_code.ilike(term,escape='\\'),Material.expenditure_code.ilike(term,escape='\\')])
            predicates.extend([Category.name_en.ilike(term,escape='\\'),parent_category.name_en.ilike(term,escape='\\')])
            if db.bind is not None and db.bind.dialect.name=='postgresql':
                predicates.extend([Material.normalized_description.op('%')(normalized),Material.description.op('%')(normalized)])
        if autocomplete:
            tokens=normalized.split()[:12]
            query=query.where(and_(*[Material.normalized_description.ilike('%'+token.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',escape='\\') | Material.name_en.ilike('%'+token.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',escape='\\') for token in tokens]))
        else:query=query.where(or_(*predicates))
        rank=case((func.lower(Material.code)==normalized,0),(func.lower(Material.name_en)==normalized,1),
                  (func.lower(Material.code).like(f'{escaped}%',escape='\\'),2),
                  (func.lower(Material.name_en).like(f'{escaped}%',escape='\\'),3),else_=4)
        ordering=(rank,Material.code,Material.id)
    else:
        ordering=(Material.code,Material.id)
    rows=[record(m) for m in db.scalars(query.order_by(*ordering).limit(limit).offset(offset)).all()]
    catalog_cache.set('taxonomy-materials',params,rows,ttl=300,epoch=cache_epoch)
    return rows

@router.get('/materials/manage',response_model=MaterialFilterPage)
def manage_materials(q:str='',main_category_id:str|None=None,subcategory_id:str|None=None,unit:str|None=None,
                     archived:bool=False,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),
                     sort:str='code',direction:str='asc',user:User=Depends(admin_user),db:Session=Depends(get_db)):
    if len(q)>100:raise HTTPException(422,'Search text must be 100 characters or fewer')
    if sort not in {'code','name_en','unit','updated_at'} or direction not in {'asc','desc'}:
        raise HTTPException(422,'Unsupported material sort order')
    main_category=aliased(Category);subcategory=aliased(Category)
    query=select(Material,main_category.name_en.label('main_category_name'),subcategory.name_en.label('subcategory_name')).outerjoin(
        main_category,main_category.id==Material.main_category_id).outerjoin(subcategory,subcategory.id==Material.subcategory_id).where(Material.active.is_(not archived))
    if main_category_id:query=query.where(Material.main_category_id==main_category_id)
    if subcategory_id:query=query.where(Material.subcategory_id==subcategory_id)
    if unit:query=query.where(Material.unit==unit.strip().upper())
    normalized=normalize_text(q)
    if normalized:
        escaped=normalized.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        conditions=[Material.code.ilike(f'{escaped}%',escape='\\'),Material.name_en.ilike(f'{escaped}%',escape='\\'),
                    Material.name_en.ilike(f'%{escaped}%',escape='\\'),Material.description.ilike(f'%{escaped}%',escape='\\'),Material.source_material_code.ilike(f'{escaped}%',escape='\\'),
                    Material.expenditure_code.ilike(f'{escaped}%',escape='\\')]
        if len(normalized)>=3 and db.bind and db.bind.dialect.name=='postgresql':
            conditions.extend([Material.name_en.op('%')(normalized),Material.description.op('%')(normalized)])
        query=query.where(or_(*conditions))
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    column={'code':Material.code,'name_en':Material.name_en,'unit':Material.unit,'updated_at':Material.updated_at}[sort]
    ordering=column.desc() if direction=='desc' else column.asc()
    items=[]
    for item,main_name,sub_name in db.execute(query.order_by(ordering,Material.id).limit(limit).offset(offset)).all():
        value=record(item);value['main_category']=main_name;value['subcategory']=sub_name;items.append(value)
    return {'items':items,'total':total,'limit':limit,'offset':offset,
            'filters':{'q':q,'main_category_id':main_category_id,'subcategory_id':subcategory_id,'unit':unit,'archived':archived,'sort':sort,'direction':direction}}

@router.get('/materials/{material_id}',response_model=MaterialOut)
def material_detail(material_id:str,db:Session=Depends(get_db)):
    m=db.get(Material,material_id)
    if not m or not m.active:raise HTTPException(404,'Material not found')
    return record(m)

@router.post('/materials',status_code=201,response_model=MaterialOut)
def create_material(payload:MaterialIn,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    category=db.get(Category,payload.category_id)
    if not category or not category.active:raise HTTPException(404,'Category not found')
    parent=db.get(Category,category.parent_id) if category.parent_id else None
    main=parent or category
    sub=category if main.id!=category.id else None
    return _save_master_material(MaterialMasterIn(main_category_id=main.id,subcategory_id=sub.id if sub else None,
        description=payload.name_en,unit=payload.unit,code=payload.code,arabic_description=payload.name_ar,
        specifications=payload.description,metadata=payload.specs_template),user,db)

def _save_master_material(payload:MaterialMasterIn,user:User,db:Session,existing:Material|None=None):
    main=db.get(Category,payload.main_category_id)
    if not main or not main.active:raise HTTPException(422,'Choose an active main category')
    sub=db.get(Category,payload.subcategory_id) if payload.subcategory_id else None
    if payload.subcategory_id and (not sub or not sub.active or sub.parent_id!=main.id):
        raise HTTPException(422,'The selected subcategory does not belong to the selected main category')
    if existing:
        for field, value in {'code': existing.code, 'source_sn': existing.source_sn,
                             'expenditure_code': existing.expenditure_code, 'specifications': existing.description,
                             'arabic_description': existing.arabic_description or existing.name_ar,
                             'metadata': existing.specs_template}.items():
            if field not in payload.model_fields_set:
                setattr(payload, field, value)
    description=' '.join(payload.description.split())
    normalized=normalize_text(description)
    if len(normalized)<3:raise HTTPException(422,'Enter a meaningful material description')
    unit,source_unit=ensure_unit(db,payload.unit,create_unknown=False)
    leaf=sub or main
    specs=payload.specifications.strip()
    duplicate=find_exact_material(db,category_id=leaf.id,description=description,unit=unit,specifications=specs)
    if duplicate and (existing is None or duplicate.id!=existing.id):
        raise HTTPException(409,detail={'message':'An active material with this description, category and unit already exists','material_id':duplicate.id,'material_code':duplicate.code})
    identity=material_identity(main_category=main.normalized_name or main.name_en,
                               subcategory=(sub.normalized_name or sub.name_en) if sub else '',
                               description=description,unit=unit,specifications=specs)
    code=(payload.code or (existing.code if existing else 'MAT-'+identity[:28].upper())).strip().upper()
    code_owner=db.scalar(select(Material).where(Material.code==code))
    if code_owner and (existing is None or code_owner.id!=existing.id):raise HTTPException(409,'Material code already exists')
    root=main
    depth=0
    while root.parent_id and depth<8:
        parent=db.get(Category,root.parent_id)
        if not parent:break
        root=parent;depth+=1
    discipline=root.name_en if root.name_en.casefold() in {'civil','electrical','mechanical'} else None
    if existing:
        old=material_snapshot(existing)
        identity_owner=db.scalar(select(Material).where(Material.source_identity==identity,Material.id!=existing.id))
        if identity_owner:raise HTTPException(409,'This material identity already exists')
        existing.source_identity=identity
        existing.code=code;existing.category_id=leaf.id;existing.main_category_id=main.id;existing.subcategory_id=sub.id if sub else None
        existing.name_en=description;existing.normalized_description=normalized;existing.unit=unit;existing.source_unit=source_unit
        existing.description=specs;existing.specs_template={**payload.metadata,'technical_specifications':specs} if specs else dict(payload.metadata)
        existing.name_ar=payload.arabic_description.strip();existing.arabic_description=payload.arabic_description.strip()
        existing.source_sn=payload.source_sn.strip() if payload.source_sn else None
        existing.expenditure_code=payload.expenditure_code.strip() if payload.expenditure_code else None
        existing.discipline=discipline;existing.version+=1
        db.add(MaterialRevision(material_id=existing.id,version=existing.version,actor_user_id=user.id,action='updated',snapshot=material_snapshot(existing)))
        audit(db,user,'material.updated',entity_id=existing.id,version=existing.version)
        material=existing
    else:
        identity_owner=db.scalar(select(Material).where(Material.source_identity==identity))
        if identity_owner:raise HTTPException(409,detail={'message':'This material identity already exists','material_id':identity_owner.id,'material_code':identity_owner.code})
        material,_=create_material_record(db,actor=user,main_category=main,subcategory=sub,description=description,
            unit=unit,source_unit=source_unit,specifications=specs,arabic_description=payload.arabic_description,
            source_sn=payload.source_sn,expenditure_code=payload.expenditure_code,discipline=discipline,code=code,metadata=payload.metadata)
        audit(db,user,'material.created',entity_id=material.id,version=1)
    db.commit()
    catalog_cache.invalidate()
    return record(material)

@router.post('/materials/manual',status_code=201,response_model=MaterialOut)
def create_master_material(payload:MaterialMasterIn,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Admin entry: Main Category, Subcategory, Description and Unit.

    Codes are generated. Legacy optional metadata remains accepted for existing
    API clients, but is excluded from the four-field duplicate identity.
    """
    return _save_master_material(payload,user,db)

@router.put('/materials/{material_id}',response_model=MaterialOut)
def edit_master_material(material_id:str,payload:MaterialMasterIn,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Edit in place, preserving material ID, RFQ references and omitted legacy metadata."""
    material=db.get(Material,material_id)
    if not material:raise HTTPException(404,'Material not found')
    return _save_master_material(payload,user,db,material)

@router.get('/materials/{material_id}/revisions',response_model=list[MaterialRevisionOut])
def material_revisions(material_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    if not db.get(Material,material_id):raise HTTPException(404,'Material not found')
    return db.scalars(select(MaterialRevision).where(MaterialRevision.material_id==material_id).order_by(MaterialRevision.version.desc()).limit(100)).all()

@router.patch('/materials/{material_id}/archive',response_model=MaterialOut)
def archive_material(material_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    m=db.get(Material,material_id)
    if not m:raise HTTPException(404,'Material not found')
    if m.active:
        m.active=False;m.version+=1;db.flush()
        db.add(MaterialRevision(material_id=m.id,version=m.version,actor_user_id=user.id,action='archived',snapshot=material_snapshot(m)))
    audit(db,user,'material.archived',entity_id=m.id);db.commit()
    catalog_cache.invalidate()
    return record(m)

@router.patch('/materials/{material_id}/restore',response_model=MaterialOut)
def restore_material(material_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    m=db.get(Material,material_id)
    if not m:raise HTTPException(404,'Material not found')
    if not m.active:
        for category_id in {m.category_id,m.main_category_id,m.subcategory_id}-{None}:
            category=db.get(Category,category_id)
            if not category or not category.active:raise HTTPException(409,'Restore the material categories first')
        duplicate=find_exact_material(db,category_id=m.category_id,description=m.name_en,unit=m.unit,specifications=m.description)
        if duplicate and duplicate.id!=m.id:raise HTTPException(409,'An active duplicate material already exists')
        m.active=True;m.version+=1;db.flush()
        db.add(MaterialRevision(material_id=m.id,version=m.version,actor_user_id=user.id,action='restored',snapshot=material_snapshot(m)))
    audit(db,user,'material.restored',entity_id=m.id);db.commit();catalog_cache.invalidate()
    return record(m)
