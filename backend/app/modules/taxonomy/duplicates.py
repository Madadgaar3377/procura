"""Explicit, conservative category duplicate review; never deletes a category."""
from collections import defaultdict
from hashlib import sha256
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func, text
from sqlalchemy.orm import Session
from app import catalog_cache
from app.database import Base, get_db
from app.models import Category, Capability, Material, User
from app.modules.materials.service import normalize_category_name
from app.security import admin_user, audit

router=APIRouter(prefix='/categories/duplicates',tags=['Material Master'])

class DuplicateMember(BaseModel):
    id:str
    name_en:str
    name_ar:str
    code:str
    references:dict[str,int]

class DuplicateGroup(BaseModel):
    canonical_id:str
    parent_id:str|None
    parent_name:str|None
    normalized_name:str
    preview_token:str
    safe_to_merge:bool
    reasons:list[str]
    categories:list[DuplicateMember]

class DuplicateMergeIn(BaseModel):
    category_ids:list[str]=Field(min_length=2,max_length=100)
    preview_token:str

class DuplicateMergeOut(BaseModel):
    canonical_id:str
    archived_ids:list[str]
    references_moved:int


def category_lock(db):
    if db.bind.dialect.name=='postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':717341395})


def category_references():
    # Include every relational category reference, including RFQ snapshots and rates.
    return [(table,column) for table in Base.metadata.sorted_tables for column in table.columns
            if table.name!='categories' and any(f.target_fullname=='categories.id' for f in column.foreign_keys)]


def _group(db,categories,all_categories):
    ids={c.id for c in categories};reasons=[]
    if any(c.parent_id in ids for c in all_categories):
        reasons.append('Categories with children require hierarchy review; merge leaves first.')
    arabic={normalize_category_name(c.name_ar) for c in categories if c.name_ar}
    if len(arabic)>1:reasons.append('Arabic category names disagree.')
    caps=list(db.scalars(select(Capability).where(Capability.category_id.in_(ids))))
    keys=[(c.org_id,c.city) for c in caps]
    if len(set(keys))!=len(keys):reasons.append('Supplier capability areas overlap; their approvals and contact assignments require review.')
    materials=list(db.scalars(select(Material).where(Material.category_id.in_(ids))))
    keys=[(normalize_category_name(m.name_en),m.unit) for m in materials]
    if len(set(keys))!=len(keys):reasons.append('Material identities overlap across these categories; preserve material IDs and review first.')
    ordered=sorted(categories,key=lambda c:(c.created_at,c.id));canonical=ordered[0]
    counts={cid:{} for cid in ids}
    for table,column in category_references():
        for cid,count in db.execute(select(column,func.count()).where(column.in_(ids)).group_by(column)):
            counts[cid][f'{table.name}.{column.name}']=count
    token=sha256(repr([(c.id,str(c.updated_at),sorted(counts[c.id].items())) for c in ordered]).encode()).hexdigest()
    parent=next((c for c in all_categories if c.id==canonical.parent_id),None)
    return dict(canonical_id=canonical.id,parent_id=canonical.parent_id,parent_name=parent.name_en if parent else None,
        normalized_name=normalize_category_name(canonical.name_en),preview_token=token,safe_to_merge=not reasons,reasons=reasons,
        categories=[dict(id=c.id,name_en=c.name_en,name_ar=c.name_ar,code=c.code,references=counts[c.id]) for c in ordered])


@router.get('',response_model=list[DuplicateGroup])
def preview_duplicates(user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Actual active IDs grouped by normalized name AND exact parent; read-only."""
    categories=list(db.scalars(select(Category).order_by(Category.id)));groups=defaultdict(list)
    for c in categories:
        if c.active:groups[(c.parent_id,normalize_category_name(c.name_en))].append(c)
    return [_group(db,items,categories) for items in groups.values() if len(items)>1]


@router.post('/merge',response_model=DuplicateMergeOut)
def merge_duplicates(payload:DuplicateMergeIn,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Recheck a preview, move all relational references and archive aliases atomically.

    Capability/material collisions or hierarchy/translation ambiguity block merging.
    Archived IDs and historical JSON revision snapshots remain available for audit.
    """
    category_lock(db)
    categories=list(db.scalars(select(Category).order_by(Category.id).with_for_update()))
    selected=[c for c in categories if c.id in set(payload.category_ids)]
    if len(selected)!=len(set(payload.category_ids)) or any(not c.active for c in selected):
        raise HTTPException(409,'Selected active categories changed; refresh the duplicate preview')
    identities={(c.parent_id,normalize_category_name(c.name_en)) for c in selected}
    if len(identities)!=1:raise HTTPException(409,'Only the same normalized name under the same parent can be merged')
    if any(c.active and c.id not in payload.category_ids and (c.parent_id,normalize_category_name(c.name_en)) in identities for c in categories):
        raise HTTPException(409,'Include the complete duplicate group from a fresh preview')
    group=_group(db,selected,categories)
    if group['preview_token']!=payload.preview_token or not group['safe_to_merge']:
        raise HTTPException(409,'Duplicate preview changed or requires manual review; refresh before merging')
    target=next(c for c in selected if c.id==group['canonical_id']);sources=[c for c in selected if c.id!=target.id]
    source_ids=[c.id for c in sources];moved=0
    # Archive first so the active uniqueness guard permits the surviving identity.
    for c in sources:c.active=False
    db.flush()
    for table,column in category_references():
        result=db.execute(update(table).where(column.in_(source_ids)).values({column.name:target.id}))
        moved+=result.rowcount
    if not target.name_ar:target.name_ar=next((c.name_ar for c in sources if c.name_ar),'')
    target.normalized_name=normalize_category_name(target.name_en)
    audit(db,user,'category.duplicates_merged',entity_id=target.id,archived_ids=source_ids,references_moved=moved)
    db.commit();catalog_cache.invalidate()
    return dict(canonical_id=target.id,archived_ids=source_ids,references_moved=moved)
