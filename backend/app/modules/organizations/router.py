from app.search import ListSearch
from app.response_contracts import ClaimOut, MemberOut, OrganizationOut, ReviewQueue, VerificationOut
import os, uuid
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, func, literal
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models import Organization,User,Member,Verification,CompanyClaim,Outbox,Contact,ContactChannel,ContactImportSource,ContactCapability,ContactSupplierTrade,StoredFile,Capability,Category,Material,ImportedSupplierTrade,AuditEvent,utcnow
from app.modules.auth.router import norm_phone, norm_email
from app.identity import normalize_phone as normalize_business_phone
from app.security import current_user,admin_user,org_member,audit
from app.schemas import OrgCreate,OrgUpdate,VerifyCreate,ClaimCreate,Decision,AdminSupplierCreate
from app.serializers import record
from app.config import get_settings
from app.profile_schema import missing_profile_columns
from fastapi.responses import FileResponse,RedirectResponse
from pathlib import Path
from pydantic import BaseModel, Field
import re

from app.modules.organizations.public_profile import router as public_profile_router
from app.modules.organizations.contact_import import parse_emails, parse_phones, norm_name, _add_channel, import_contact_row

router=APIRouter(prefix='/organizations',tags=['Organizations & Verification'])

class SupplierProfileAdminUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=220)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=300)
    cr_number: str | None = Field(default=None, max_length=80)
    vat_number: str | None = Field(default=None, max_length=80)
    website: str | None = Field(default=None, max_length=300)
    description: str | None = None
    contact_name: str | None = Field(default=None, max_length=150)
    designation: str | None = Field(default=None, max_length=100)
    business_email: str | None = Field(default=None, max_length=200)
    business_phone: str | None = Field(default=None, max_length=80)

class SupplierContactAdminInput(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    role: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=300)
    fax: str | None = Field(default=None, max_length=80)
    is_primary: bool | None = None

class ContactChannelAdminInput(BaseModel):
    kind: str = Field(pattern='^(email|phone|fax)$')
    value: str = Field(min_length=3, max_length=300)
    label: str = Field(default='other', pattern='^(mobile|office|other|fax)$')

class ContactActionConfirmation(BaseModel):
    confirm: bool

class ContactChannelUpdateInput(BaseModel):
    is_primary: bool | None = None
    needs_review: bool | None = None
    label: str | None = Field(default=None, pattern='^(mobile|office|other|fax)$')

class ContactMergeInput(BaseModel):
    canonical_contact_id: str
    duplicate_contact_ids: list[str] = Field(min_length=1,max_length=30)
    confirm: bool
    reason: str = Field(min_length=4,max_length=1000)

class ContactReviewInput(BaseModel):
    needs_review: bool

class SupplierReviewAction(BaseModel):
    reason: str | None = Field(default=None, min_length=4, max_length=2000)

class SupplierCapabilityAdminInput(BaseModel):
    category_id: str = Field(min_length=1, max_length=36)
    city: str = Field(default='', max_length=100)
    trade_keywords: str = Field(default='', max_length=5000)
    material_ids: list[str] = Field(default_factory=list, max_length=500)

class SupplierContactCapabilityInput(BaseModel):
    category_id: str = Field(min_length=1,max_length=36)
    material_ids: list[str] = Field(default_factory=list,max_length=500)
    is_primary: bool = False

class SupplierArchiveInput(BaseModel):
    confirm: bool
    reason: str = Field(default='',max_length=1000)

def _supplier_org(db, org_id):
    org=db.get(Organization,org_id)
    if not org or not org.is_supplier: raise HTTPException(404,'Supplier not found')
    return org

def _refresh_supplier_profile_status(org,db):
    contacts=db.scalars(select(Contact).where(Contact.org_id==org.id,Contact.merged_into_contact_id.is_(None),Contact.deleted_at.is_(None))).all()
    has_contact=bool(org.contact_name or any(c.name for c in contacts))
    has_email=False
    for value in [org.business_email,*[c.email for c in contacts]]:
        if value:
            try: norm_email(value);has_email=True;break
            except ValueError: pass
    has_phone=bool(any(c.phone_normalized for c in contacts))
    if org.business_phone:
        try: normalize_business_phone(org.business_phone);has_phone=True
        except ValueError: pass
    has_real_name=bool(org.name and not org.name.startswith('[Imported supplier placeholder:'))
    org.profile_status='complete' if all((has_real_name,org.city,org.address,org.cr_number,has_contact,has_email,has_phone)) else 'incomplete'

def _contact_data(contact,db):
    channels=db.scalars(select(ContactChannel).where(ContactChannel.contact_id==contact.id).order_by(ContactChannel.kind,ContactChannel.is_primary.desc(),ContactChannel.created_at)).all()
    emails=[{'id':c.id,'value':c.value,'normalized_value':c.normalized_value,'primary':c.is_primary,'verified':c.is_verified,'needs_review':c.needs_review,'source':c.source_value} for c in channels if c.kind=='email']
    phones=[{'id':c.id,'value':c.value,'normalized_value':c.normalized_value,'label':c.label,'extension':c.extension,'primary':c.is_primary,'verified':c.is_verified,'needs_review':c.needs_review,'source':c.source_value} for c in channels if c.kind=='phone']
    faxes=[{'id':c.id,'value':c.value,'normalized_value':c.normalized_value,'primary':c.is_primary,'source':c.source_value} for c in channels if c.kind=='fax']
    if not emails and contact.email: emails=[{'id':None,'value':contact.email,'normalized_value':contact.email.casefold(),'primary':True,'verified':False,'needs_review':contact.needs_review,'source':contact.raw_email}]
    if not phones and contact.phone: phones=[{'id':None,'value':contact.phone,'normalized_value':contact.phone_normalized,'label':'other','extension':None,'primary':True,'verified':False,'needs_review':contact.needs_review,'source':contact.raw_phone}]
    if not faxes and contact.fax: faxes=[{'id':None,'value':contact.fax,'normalized_value':contact.fax,'primary':True,'source':contact.raw_phone}]
    related_ids=[contact.id,*db.scalars(select(Contact.id).where(Contact.merged_into_contact_id==contact.id))]
    sources=db.scalars(select(ContactImportSource).where(ContactImportSource.contact_id.in_(related_ids)).order_by(ContactImportSource.created_at)).all()
    responsibilities = db.scalars(select(ImportedSupplierTrade).join(ContactSupplierTrade,
        ContactSupplierTrade.trade_id == ImportedSupplierTrade.id).where(
            ContactSupplierTrade.contact_id.in_(related_ids), ImportedSupplierTrade.org_id == contact.org_id)).all()
    return {**{key:getattr(contact,key) for key in ('id','name','role','email','raw_email','phone','raw_phone','phone_normalized','fax','needs_review','is_primary','created_at')},'emails':emails,'phones':phones,'faxes':faxes,'import_sources':[{'source_reference':s.source_reference,'raw_name':s.raw_name,'raw_email':s.raw_email,'raw_phone':s.raw_phone} for s in sources],'source':'imported' if sources else 'admin', 'source_responsibilities':[{'id':item.id,'material':item.raw_trade,'category':item.raw_type,'mapping_status':item.mapping_status} for item in responsibilities]}

@router.get('/admin/suppliers')
def admin_supplier_list(q:str='', status_filter:str='all', limit:int=Query(26,ge=1,le=100), offset:int=Query(0,ge=0), admin:User=Depends(admin_user), db:Session=Depends(get_db)):
    if status_filter not in {'all','verified','unverified','incomplete','imported','pending_verification'}: raise HTTPException(422,'Invalid supplier filter')
    missing=missing_profile_columns(db)
    stmt=select(Organization).where(Organization.is_supplier.is_(True))
    stmt=stmt.where(Organization.is_active.is_(True))
    if status_filter=='verified': stmt=stmt.where(Organization.verified_status=='verified')
    elif status_filter=='unverified': stmt=stmt.where(Organization.verified_status=='unverified')
    elif status_filter=='incomplete': stmt=stmt.where(literal(True) if 'profile_status' in missing else Organization.profile_status.in_(['incomplete','pending_onboarding']))
    elif status_filter=='imported': stmt=stmt.where(Organization.source=='imported')
    elif status_filter=='pending_verification': stmt=stmt.where(Organization.verified_status=='pending')
    if q.strip():
        needle='%'+q.strip().replace('!','!!').replace('%','!%').replace('_','!_')+'%'
        contact_match=select(Contact.id).where(Contact.org_id==Organization.id,or_(Contact.email.ilike(needle,escape='!'),Contact.phone.ilike(needle,escape='!'))).exists()
        terms=[Organization.name.ilike(needle,escape='!'),Organization.city.ilike(needle,escape='!'),Organization.cr_number.ilike(needle,escape='!'),contact_match]
        if 'business_email' not in missing: terms.append(Organization.business_email.ilike(needle,escape='!'))
        if 'business_phone' not in missing: terms.append(Organization.business_phone.ilike(needle,escape='!'))
        stmt=stmt.where(or_(*terms))
    # Do not use DISTINCT on the full Organization entity: PostgreSQL cannot
    # compare its JSON quote_automation column for DISTINCT. Contact search is
    # correlated with EXISTS above, so a supplier can only appear once.
    stmt=stmt.order_by(Organization.name,Organization.id).offset(offset).limit(limit)
    return [record(org) for org in db.scalars(stmt).all()]

@router.get('/admin/suppliers/{org_id}')
def admin_supplier_details(org_id:str, admin:User=Depends(admin_user), db:Session=Depends(get_db)):
    org=_supplier_org(db,org_id)
    contacts=db.scalars(select(Contact).where(Contact.org_id==org_id,Contact.merged_into_contact_id.is_(None),Contact.deleted_at.is_(None)).order_by(Contact.created_at,Contact.id)).all()
    contact_capabilities={contact.id:[] for contact in contacts}
    if contacts:
        contact_links=db.execute(select(ContactCapability,Capability,Category).join(Capability,Capability.id==ContactCapability.capability_id).join(Category,Category.id==Capability.category_id).where(ContactCapability.contact_id.in_([c.id for c in contacts]))).all()
        for link,cap,category in contact_links:
            parent=db.get(Category,category.parent_id) if category.parent_id else None
            assigned_material_ids = link.material_ids if link.material_ids is not None else cap.material_ids
            materials=db.scalars(select(Material).where(Material.id.in_(assigned_material_ids or []))).all() if assigned_material_ids else []
            contact_capabilities[link.contact_id].append({'capability_id':cap.id,'category_id':category.id,
                'category':f'{parent.name_en} / {category.name_en}' if parent else category.name_en,
                'materials':[m.name_en for m in materials],'is_primary':link.is_primary})
    verifications=db.scalars(select(Verification).where(Verification.org_id==org_id).order_by(Verification.created_at.desc())).all()
    latest=verifications[0] if verifications else None
    caps=db.scalars(select(Capability).where(Capability.org_id==org_id).order_by(Capability.created_at,Capability.id)).all()
    cap_items=[]
    for cap in caps:
        category=db.get(Category,cap.category_id)
        parent=db.get(Category,category.parent_id) if category and category.parent_id else None
        materials=db.scalars(select(Material).where(Material.id.in_(cap.material_ids or []))).all() if cap.material_ids else []
        cap_items.append({'id':cap.id,'main_category':parent.name_en if parent else (category.name_en if category else None),'subcategory':category.name_en if parent else None,'category_id':cap.category_id,'materials':[{'id':m.id,'name':m.name_en,'unit':m.unit} for m in materials],'units':sorted({m.unit for m in materials if m.unit}),'trade_keywords':cap.trade_keywords,'confirmed':cap.confirmed,'eligibility_status':cap.eligibility_status,'mapping_source':cap.mapping_source,'created_at':cap.created_at,'city':cap.city})
    trades=db.scalars(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id==org_id).order_by(ImportedSupplierTrade.created_at)).all()
    files=db.scalars(select(StoredFile).where(StoredFile.organization_id==org_id,StoredFile.status=='available',StoredFile.deleted_at.is_(None))).all()
    docs=[{'id':v.id,'name':v.document_key.rsplit('/',1)[-1] if v.document_key else 'Verification document','status':v.status,'download_url':f'/organizations/admin/review/verification/{v.id}/document-url'} for v in verifications if v.document_key]
    for f in files:
        if not any(x['id']==f.id for x in docs):
            docs.append({'id':f.id,'name':f.safe_filename,'purpose':f.purpose,'status':f.status,'download_url':f'/files/{f.id}/download-url'})
    last_audit=db.scalar(select(func.max(AuditEvent.created_at)).where(AuditEvent.org_id==org_id))
    return {'company':{key:getattr(org,key) for key in ('id','name','country','city','address','cr_number','vat_number','website','description','source','created_at','contact_name','designation','business_email','business_phone')},
        'contacts':[{**_contact_data(c,db),'assigned_capabilities':contact_capabilities[c.id]} for c in contacts],
        'status':{'profile_status':org.profile_status,'verification_status':org.verified_status,'onboarding_status':'complete' if db.scalar(select(Member.id).where(Member.organization_id==org_id)) else 'pending_onboarding','source':org.source,'last_updated_at':last_audit or org.created_at,'verified_by':latest.reviewer_id if latest and latest.status=='approved' else None,'verified_at':latest.reviewed_at if latest and latest.status=='approved' else None,'rejected_by':latest.reviewer_id if latest and latest.status=='rejected' else None,'rejected_at':latest.reviewed_at if latest and latest.status=='rejected' else None,'rejection_reason':latest.reviewer_notes if latest and latest.status=='rejected' else None},
        'capabilities':cap_items,'pending_category_mappings':[{'id':x.id,'raw_trade':x.raw_trade,'raw_type':x.raw_type,'raw_location':x.raw_location,'mapping_status':x.mapping_status} for x in trades if x.mapping_status not in {'approved','mapped'}],
        'documents':docs,'verification_history':[{'id':v.id,'status':v.status,'cr_number':v.cr_number,'reviewer_id':v.reviewer_id,'reviewer_notes':v.reviewer_notes,'reviewed_at':v.reviewed_at,'created_at':v.created_at} for v in verifications]}

@router.patch('/admin/suppliers/{org_id}')
def admin_update_supplier(org_id:str,payload:SupplierProfileAdminUpdate,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    org=_supplier_org(db,org_id)
    values=payload.model_dump(exclude_unset=True)
    if 'name' in values and values['name'] is None: raise HTTPException(422,'Company name cannot be cleared')
    for key,value in values.items():
        if value is not None and isinstance(value,str): value=value.strip() or None
        setattr(org,key,value)
    if 'name' in values and org.name: org.name_normalized=org.name.strip().casefold()
    _refresh_supplier_profile_status(org,db)
    audit(db,admin,'supplier.profile_updated',org_id,org_id,fields=list(values))
    db.commit(); return {'saved':True,'profile_status':org.profile_status}

def _save_admin_contact(org_id,payload,admin,db,contact=None):
    values=payload.model_dump(exclude_unset=True)
    is_new=contact is None
    if contact is None:
        contact=Contact(org_id=org_id,permission_to_notify=False);db.add(contact);db.flush()
    if 'name' in values: contact.name=(values['name'] or '').strip() or None
    if 'role' in values: contact.role=(values['role'] or '').strip() or None
    if values.get('is_primary'):
        db.query(Contact).filter(Contact.org_id==org_id,Contact.id!=contact.id,Contact.deleted_at.is_(None)).update({Contact.is_primary:False},synchronize_session=False)
    if 'is_primary' in values:contact.is_primary=bool(values['is_primary'])
    if 'fax' in values: contact.fax=(values['fax'] or '').strip() or None
    review=False
    if 'email' in values:
        raw=(values['email'] or '').strip()
        contact.raw_email=raw or None
        emails=[part.strip() for part in re.split(r'[,;/]',raw) if part.strip()]
        valid=next((e.casefold() for e in emails if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',e)),None)
        contact.email=valid
        review=bool(raw and not valid)
    if 'phone' in values:
        raw=(values['phone'] or '').strip();contact.raw_phone=raw or None;contact.phone=raw or None;contact.phone_normalized=None
        if raw:
            try: contact.phone_normalized=normalize_business_phone(raw);contact.phone=contact.phone_normalized
            except ValueError: review=True
    if 'email' in values:
        parsed,invalid=parse_emails(values['email'])
        review=review or invalid
        for email in parsed: _add_channel(db,contact,'email',email,email,'other',values['email'] or '',None)
    if 'phone' in values or 'fax' in values:
        parsed,invalid=parse_phones(values.get('phone') or values.get('fax'), 'SA')
        review=review or invalid
        for item in parsed:
            kind='fax' if 'fax' in values and not values.get('phone') else item['kind']
            _add_channel(db,contact,kind,item['value'],item['normalized'],'fax' if kind=='fax' else item['label'],values.get('phone') or values.get('fax') or '',None,item['extension'])
    contact.needs_review=contact.needs_review or review
    db.add(contact);db.flush();_refresh_supplier_profile_status(db.get(Organization,org_id),db);audit(db,admin,'supplier.contact_added' if is_new else 'supplier.contact_updated',org_id,contact.id)
    db.commit();db.refresh(contact);return _contact_data(contact,db)

@router.post('/admin/suppliers/{org_id}/contacts',status_code=201)
def admin_add_supplier_contact(org_id:str,payload:SupplierContactAdminInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);return _save_admin_contact(org_id,payload,admin,db)

@router.patch('/admin/suppliers/{org_id}/contacts/{contact_id}')
def admin_edit_supplier_contact(org_id:str,contact_id:str,payload:SupplierContactAdminInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);contact=db.get(Contact,contact_id)
    if not contact or contact.org_id!=org_id:raise HTTPException(404,'Supplier contact not found')
    return _save_admin_contact(org_id,payload,admin,db,contact)

def _contact_duplicate_preview(db,org_id):
    contacts=db.scalars(select(Contact).where(Contact.org_id==org_id,Contact.merged_into_contact_id.is_(None),Contact.deleted_at.is_(None)).order_by(Contact.created_at,Contact.id)).all()
    channels=db.scalars(select(ContactChannel).where(ContactChannel.org_id==org_id)).all()
    by_contact={c.id:[] for c in contacts}
    for channel in channels:
        if channel.contact_id in by_contact: by_contact[channel.contact_id].append(channel)
    def identities(contact):
        rows=list(by_contact[contact.id])
        raw_emails,_=parse_emails(contact.raw_email)
        for value in set(raw_emails + ([contact.email.strip().casefold()] if contact.email else [])):
            if not any(c.kind=='email' and c.normalized_value==value for c in rows): rows.append(SimpleNamespace(kind='email',normalized_value=value,label='other'))
        raw_phones,_=parse_phones(contact.raw_phone,'SA')
        phone_ids={p['normalized'] for p in raw_phones}
        if contact.phone:
            phone_ids.add(contact.phone_normalized or re.sub(r'[^0-9+]', '', contact.phone))
        for value in phone_ids:
            if value and not any(c.kind=='phone' and c.normalized_value==value for c in rows): rows.append(SimpleNamespace(kind='phone',normalized_value=value,label='other'))
        if contact.fax:
            value=re.sub(r'[^0-9+]', '', contact.fax)
            if value and not any(c.kind=='fax' and c.normalized_value==value for c in rows): rows.append(SimpleNamespace(kind='fax',normalized_value=value,label='fax'))
        return rows
    for contact in contacts: by_contact[contact.id]=identities(contact)
    safe_edges=[]; review_edges=[]
    for i,left in enumerate(contacts):
        for right in contacts[i+1:]:
            emails={c.normalized_value for c in by_contact[left.id]+by_contact[right.id] if c.kind=='email'}
            shared_email=bool({c.normalized_value for c in by_contact[left.id] if c.kind=='email'} & {c.normalized_value for c in by_contact[right.id] if c.kind=='email'})
            left_name,right_name=norm_name(left.name),norm_name(right.name)
            conflict=bool(left_name and right_name and left_name!=right_name)
            overlapping = bool({(c.kind,c.normalized_value) for c in by_contact[left.id]} & {(c.kind,c.normalized_value) for c in by_contact[right.id]})
            mobile_overlap=bool({c.normalized_value for c in by_contact[left.id] if c.kind=='phone' and c.label=='mobile'} & {c.normalized_value for c in by_contact[right.id] if c.kind=='phone' and c.label=='mobile'})
            if shared_email or (overlapping and left_name and left_name==right_name) or (mobile_overlap and not conflict):
                group={'contact_ids':[left.id,right.id],'names':[left.name,right.name],'shared_emails':sorted(emails)}
                (review_edges if conflict else safe_edges).append(group)
    parent={c.id:c.id for c in contacts}
    def find(value):
        while parent[value]!=value:
            parent[value]=parent[parent[value]];value=parent[value]
        return value
    for edge in safe_edges+review_edges:
        a,b=edge['contact_ids'];parent[find(b)]=find(a)
    clusters={}
    for edge in safe_edges+review_edges:
        root=find(edge['contact_ids'][0]);clusters.setdefault(root,{'ids':set(),'manual':False,'emails':set()})
        clusters[root]['ids'].update(edge['contact_ids']);clusters[root]['manual']|=edge in review_edges
        clusters[root]['emails'].update(edge['shared_emails'])
    safe=[];review=[]
    contacts_by_id={c.id:c for c in contacts}
    for cluster in clusters.values():
        ids=sorted(cluster['ids']);names=[contacts_by_id[cid].name for cid in ids]
        unique_names={norm_name(name) for name in names if norm_name(name)}
        group={'contact_ids':ids,'names':names,'shared_emails':sorted(cluster['emails'])}
        (review if cluster['manual'] or len(unique_names)>1 else safe).append(group)
    involved={cid for group in safe+review for cid in group['contact_ids']}
    return {'existing_contact_count':len(contacts),'duplicate_groups_detected':len(safe)+len(review),'safe_groups':safe,
        'contacts_requiring_manual_review':review,'records_remaining_separate':[{'id':c.id,'name':c.name} for c in contacts if c.id not in involved],
        'expected_contact_count_after_cleanup':len(contacts)-sum(len(group['contact_ids'])-1 for group in safe)}

def _merge_contact_capabilities(db,source_id,target_id):
    for link in db.scalars(select(ContactCapability).where(ContactCapability.contact_id==source_id)).all():
        existing=db.scalar(select(ContactCapability).where(ContactCapability.contact_id==target_id,ContactCapability.capability_id==link.capability_id))
        if existing:
            existing.is_primary=existing.is_primary or link.is_primary;db.delete(link)
        else:link.contact_id=target_id

@router.get('/admin/suppliers/{org_id}/contacts/deduplication-preview')
def admin_preview_supplier_contact_deduplication(org_id:str,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id)
    return _contact_duplicate_preview(db,org_id)

@router.post('/admin/suppliers/{org_id}/contacts/deduplication')
def admin_execute_supplier_contact_deduplication(org_id:str,payload:ContactActionConfirmation,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    org=_supplier_org(db,org_id)
    if not payload.confirm: raise HTTPException(422,'Explicit confirmation is required')
    preview=_contact_duplicate_preview(db,org_id)
    merged=[]
    try:
        for group in preview['safe_groups']:
            contacts=[db.get(Contact,cid) for cid in group['contact_ids']]
            contacts=[c for c in contacts if c and c.deleted_at is None and c.merged_into_contact_id is None]
            if len(contacts)<2: continue
            canonical=min(contacts,key=lambda c:(c.created_at,c.id))
            for duplicate in contacts:
                if duplicate.id==canonical.id: continue
                # Recover valid addresses and numbers that older imports left only in raw cells.
                emails=set(parse_emails(canonical.raw_email)[0]+parse_emails(duplicate.raw_email)[0])
                emails.update(value for value in (canonical.email,duplicate.email) if value)
                for email in emails:
                    _add_channel(db,canonical,'email',email,email,'other',duplicate.raw_email or canonical.raw_email or '',None)
                for source_contact in (canonical,duplicate):
                    recovered,_=parse_phones(source_contact.raw_phone,'SA')
                    if source_contact.phone:
                        scalar,_=parse_phones(source_contact.phone,'SA');recovered.extend(scalar)
                        if source_contact.phone_normalized and not any(item['normalized']==source_contact.phone_normalized for item in recovered):
                            recovered.append({'value':source_contact.phone,'normalized':source_contact.phone_normalized,'kind':'phone','label':'other','extension':None})
                    if source_contact.fax:
                        fax_numbers,_=parse_phones(source_contact.fax,'SA')
                        recovered.extend({**item,'kind':'fax','label':'fax'} for item in fax_numbers)
                    for item in recovered:
                        _add_channel(db,canonical,item['kind'],item['value'],item['normalized'],item['label'],source_contact.raw_phone or '',None,item['extension'])
                channels=db.scalars(select(ContactChannel).where(ContactChannel.contact_id==duplicate.id)).all()
                for channel in channels:
                    existing=db.scalar(select(ContactChannel).where(ContactChannel.contact_id==canonical.id,
                        ContactChannel.kind==channel.kind,ContactChannel.normalized_value==channel.normalized_value))
                    if existing:
                        if channel.source_value and channel.source_value not in (existing.source_value or ''):
                            existing.source_value=((existing.source_value or '')+' | '+channel.source_value)[:500]
                        db.delete(channel)
                    else: channel.contact_id=canonical.id
                _merge_contact_capabilities(db,duplicate.id,canonical.id)
                canonical.name=canonical.name or duplicate.name
                canonical.role=canonical.role or duplicate.role
                canonical.raw_email=canonical.raw_email or duplicate.raw_email
                canonical.raw_phone=canonical.raw_phone or duplicate.raw_phone
                canonical.needs_review=canonical.needs_review or duplicate.needs_review
                duplicate.merged_into_contact_id=canonical.id
                merged.append({'from':duplicate.id,'to':canonical.id})
        if merged:
            audit(db,admin,'supplier.contacts_deduplicated',org_id,org_id,merged=merged,preview=preview)
        db.commit()
    except Exception:
        db.rollback();raise
    return {'merged_contacts':merged,'contacts_merged':len(merged),'preview':_contact_duplicate_preview(db,org_id)}

@router.post('/admin/suppliers/{org_id}/contacts/{contact_id}/channels',status_code=201)
def admin_add_supplier_contact_channel(org_id:str,contact_id:str,payload:ContactChannelAdminInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);contact=db.get(Contact,contact_id)
    if not contact or contact.org_id!=org_id or contact.deleted_at or contact.merged_into_contact_id: raise HTTPException(404,'Supplier contact not found')
    if payload.kind=='email':
        emails,invalid=parse_emails(payload.value)
        if invalid or len(emails)!=1: raise HTTPException(422,'Enter one valid email address')
        value=normalized=emails[0]
    else:
        phones,invalid=parse_phones(payload.value,'SA')
        if invalid or len(phones)!=1: raise HTTPException(422,'Enter one valid phone or fax number')
        item=phones[0];value=item['value'];normalized=item['normalized']
    if db.scalar(select(ContactChannel.id).where(ContactChannel.contact_id==contact.id,ContactChannel.kind==payload.kind,ContactChannel.normalized_value==normalized)):
        return _contact_data(contact,db)
    db.add(ContactChannel(contact_id=contact.id,org_id=org_id,kind=payload.kind,value=value,normalized_value=normalized,
        label='fax' if payload.kind=='fax' else payload.label,is_primary=False,source_value='Admin added'))
    audit(db,admin,'supplier.contact_channel_added',org_id,contact.id,kind=payload.kind,normalized_value=normalized)
    db.commit();return _contact_data(contact,db)

@router.delete('/admin/suppliers/{org_id}/contacts/{contact_id}/channels/{channel_id}')
def admin_remove_supplier_contact_channel(org_id:str,contact_id:str,channel_id:str,payload:ContactActionConfirmation,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);contact=db.get(Contact,contact_id);channel=db.get(ContactChannel,channel_id)
    if not payload.confirm: raise HTTPException(422,'Explicit confirmation is required')
    if not contact or contact.org_id!=org_id or not channel or channel.contact_id!=contact_id: raise HTTPException(404,'Contact channel not found')
    was_primary=channel.is_primary;kind=channel.kind
    audit(db,admin,'supplier.contact_channel_removed',org_id,contact.id,channel={k:getattr(channel,k) for k in ('kind','value','normalized_value','label','source_value')})
    db.delete(channel);db.flush()
    if was_primary:
        replacement=db.scalar(select(ContactChannel).where(ContactChannel.contact_id==contact.id,ContactChannel.kind==kind).order_by(ContactChannel.created_at,ContactChannel.id).limit(1))
        if replacement:replacement.is_primary=True
    db.commit();return {'deleted':True}

@router.patch('/admin/suppliers/{org_id}/contacts/{contact_id}/channels/{channel_id}')
def admin_update_supplier_contact_channel(org_id:str,contact_id:str,channel_id:str,payload:ContactChannelUpdateInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);contact=db.get(Contact,contact_id);channel=db.get(ContactChannel,channel_id)
    if not contact or contact.org_id!=org_id or not channel or channel.contact_id!=contact_id: raise HTTPException(404,'Contact channel not found')
    fields=payload.model_dump(exclude_unset=True)
    if fields.get('is_primary'):
        db.query(ContactChannel).filter(ContactChannel.contact_id==contact_id,ContactChannel.kind==channel.kind).update({ContactChannel.is_primary:False},synchronize_session=False)
    for key,value in fields.items():setattr(channel,key,value)
    audit(db,admin,'supplier.contact_channel_updated',org_id,contact.id,channel_id=channel_id,fields=fields)
    db.commit();return _contact_data(contact,db)

@router.patch('/admin/suppliers/{org_id}/contacts/{contact_id}/review')
def admin_mark_supplier_contact_review(org_id:str,contact_id:str,payload:ContactReviewInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);contact=db.get(Contact,contact_id)
    if not contact or contact.org_id!=org_id or contact.deleted_at or contact.merged_into_contact_id:raise HTTPException(404,'Supplier contact not found')
    contact.needs_review=payload.needs_review
    audit(db,admin,'supplier.contact_review_status_updated',org_id,contact.id,needs_review=payload.needs_review)
    db.commit();return _contact_data(contact,db)

@router.post('/admin/suppliers/{org_id}/contacts/merge')
def admin_merge_supplier_contacts(org_id:str,payload:ContactMergeInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id)
    if not payload.confirm:raise HTTPException(422,'Explicit confirmation is required')
    if payload.canonical_contact_id in payload.duplicate_contact_ids or len(set(payload.duplicate_contact_ids))!=len(payload.duplicate_contact_ids):raise HTTPException(422,'Choose distinct contacts')
    contacts=[db.get(Contact,cid) for cid in [payload.canonical_contact_id,*payload.duplicate_contact_ids]]
    if any(not c or c.org_id!=org_id or c.deleted_at or c.merged_into_contact_id for c in contacts):raise HTTPException(404,'Supplier contact not found')
    canonical=contacts[0];merged=[]
    try:
        for duplicate in contacts[1:]:
            for channel in db.scalars(select(ContactChannel).where(ContactChannel.contact_id==duplicate.id)).all():
                existing=db.scalar(select(ContactChannel).where(ContactChannel.contact_id==canonical.id,ContactChannel.kind==channel.kind,ContactChannel.normalized_value==channel.normalized_value))
                if existing:
                    if channel.source_value and channel.source_value not in (existing.source_value or ''):existing.source_value=((existing.source_value or '')+' | '+channel.source_value)[:500]
                    db.delete(channel)
                else:channel.contact_id=canonical.id
            _merge_contact_capabilities(db,duplicate.id,canonical.id)
            canonical.name=canonical.name or duplicate.name;canonical.role=canonical.role or duplicate.role
            canonical.raw_email=canonical.raw_email or duplicate.raw_email;canonical.raw_phone=canonical.raw_phone or duplicate.raw_phone
            canonical.needs_review=canonical.needs_review or duplicate.needs_review
            duplicate.merged_into_contact_id=canonical.id;merged.append(duplicate.id)
        audit(db,admin,'supplier.contacts_manually_merged',org_id,canonical.id,merged_contact_ids=merged,reason=payload.reason)
        db.commit()
    except Exception:db.rollback();raise
    return _contact_data(canonical,db)

@router.delete('/admin/suppliers/{org_id}/contacts/{contact_id}')
def admin_delete_supplier_contact(org_id:str,contact_id:str,payload:ContactActionConfirmation,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);contact=db.get(Contact,contact_id)
    if not payload.confirm: raise HTTPException(422,'Explicit confirmation is required')
    if not contact or contact.org_id!=org_id or contact.deleted_at or contact.merged_into_contact_id: raise HTTPException(404,'Supplier contact not found')
    contact.deleted_at=utcnow();audit(db,admin,'supplier.contact_deleted',org_id,contact.id,name=contact.name,email=contact.email,phone=contact.phone)
    db.commit();return {'deleted':True}

@router.post('/admin/suppliers/{org_id}/contacts/{contact_id}/capabilities',status_code=201)
def admin_assign_contact_capability(org_id:str,contact_id:str,payload:SupplierContactCapabilityInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    org=_supplier_org(db,org_id);contact=db.get(Contact,contact_id)
    if not contact or contact.org_id!=org_id or contact.deleted_at or contact.merged_into_contact_id:raise HTTPException(404,'Supplier contact not found')
    category,material_ids,city=_admin_capability_values(org_id,SupplierCapabilityAdminInput(category_id=payload.category_id,city=org.city or '',material_ids=payload.material_ids),db)
    capability=db.scalar(select(Capability).where(Capability.org_id==org_id,Capability.category_id==category.id,
        func.lower(func.trim(Capability.city))==city.casefold()).with_for_update())
    created=capability is None
    if capability is None:
        capability=Capability(org_id=org_id,category_id=category.id,city=city,material_ids=[],trade_keywords='',confirmed=True,
            accepts_rfqs=True,eligibility_status='enabled',mapping_source='admin');db.add(capability);db.flush()
    capability.material_ids=sorted(set([*(capability.material_ids or []),*material_ids]))
    relation=db.scalar(select(ContactCapability).where(ContactCapability.contact_id==contact.id,ContactCapability.capability_id==capability.id))
    if relation is None:
        relation=ContactCapability(contact_id=contact.id,capability_id=capability.id,is_primary=payload.is_primary);db.add(relation)
    elif payload.is_primary:relation.is_primary=True
    if payload.is_primary:
        db.query(ContactCapability).filter(ContactCapability.capability_id==capability.id,ContactCapability.contact_id!=contact.id).update({ContactCapability.is_primary:False},synchronize_session=False)
    audit(db,admin,'supplier.contact_capability_assigned',org_id,contact.id,capability_id=capability.id,category_id=category.id,created=created)
    db.commit();return {'capability_id':capability.id,'created':created,'assigned':True}

@router.delete('/admin/suppliers/{org_id}/contacts/{contact_id}/capabilities/{capability_id}')
def admin_unassign_contact_capability(org_id:str,contact_id:str,capability_id:str,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id)
    relation=db.scalar(select(ContactCapability).where(ContactCapability.contact_id==contact_id,ContactCapability.capability_id==capability_id))
    if not relation or not db.scalar(select(Contact.id).where(Contact.id==contact_id,Contact.org_id==org_id)):raise HTTPException(404,'Contact capability assignment not found')
    db.delete(relation);audit(db,admin,'supplier.contact_capability_unassigned',org_id,contact_id,capability_id=capability_id);db.commit();return {'unassigned':True}

@router.post('/admin/suppliers/{org_id}/archive')
def admin_archive_supplier(org_id:str,payload:SupplierArchiveInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    org=_supplier_org(db,org_id)
    if not payload.confirm:raise HTTPException(422,'Explicit confirmation is required')
    org.is_active=False
    audit(db,admin,'supplier.archived',org_id,org_id,reason=payload.reason,preserved_contacts=True,preserved_capabilities=True)
    db.commit();return {'archived':True,'supplier_id':org.id}

def _admin_capability_values(org_id,payload,db):
    category=db.scalar(select(Category).where(Category.id==payload.category_id,Category.active.is_(True)))
    if not category:raise HTTPException(422,'Choose an active category')
    material_ids=sorted(set(payload.material_ids))
    materials=db.scalars(select(Material).where(Material.id.in_(material_ids),Material.active.is_(True))).all() if material_ids else []
    if len(materials)!=len(material_ids):raise HTTPException(422,'Every selected material must be active')
    if any(category.id not in {m.category_id,m.main_category_id,m.subcategory_id} for m in materials):
        raise HTTPException(422,'Selected materials must belong to the capability category')
    return category,material_ids,' '.join(payload.city.split())

@router.post('/admin/suppliers/{org_id}/capabilities',status_code=201)
def admin_add_supplier_capability(org_id:str,payload:SupplierCapabilityAdminInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);category,material_ids,city=_admin_capability_values(org_id,payload,db)
    cap=db.scalar(select(Capability).where(Capability.org_id==org_id,Capability.category_id==category.id,
        func.lower(func.trim(Capability.city))==city.casefold()).with_for_update())
    created=cap is None
    if cap is None:
        cap=Capability(org_id=org_id,category_id=category.id,city=city,material_ids=[],trade_keywords='',
            confirmed=True,accepts_rfqs=True,eligibility_status='enabled',mapping_source='admin')
        db.add(cap)
    cap.material_ids=sorted(set((cap.material_ids or [])+material_ids))
    existing=[part.strip() for part in (cap.trade_keywords or '').split(' | ') if part.strip()]
    for part in payload.trade_keywords.split('|'):
        value=part.strip()
        if value and value not in existing:existing.append(value)
    cap.trade_keywords=' | '.join(existing)[:5000]
    if cap.eligibility_status not in {'rejected','revoked','suspended'}:
        cap.confirmed=True;cap.accepts_rfqs=True;cap.eligibility_status='enabled'
    db.flush();audit(db,admin,'supplier.capability_admin_added',org_id,cap.id,category_id=category.id,created=created)
    db.commit();return {'id':cap.id,'category_id':cap.category_id,'city':cap.city,'trade_keywords':cap.trade_keywords,
        'material_ids':cap.material_ids,'confirmed':cap.confirmed,'eligibility_status':cap.eligibility_status,'created':created}

@router.patch('/admin/suppliers/{org_id}/capabilities/{capability_id}')
def admin_edit_supplier_capability(org_id:str,capability_id:str,payload:SupplierCapabilityAdminInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);cap=db.get(Capability,capability_id)
    if not cap or cap.org_id!=org_id:raise HTTPException(404,'Supplier capability not found')
    category,material_ids,city=_admin_capability_values(org_id,payload,db)
    duplicate=db.scalar(select(Capability.id).where(Capability.org_id==org_id,Capability.category_id==category.id,
        func.lower(func.trim(Capability.city))==city.casefold(),Capability.id!=cap.id))
    if duplicate:raise HTTPException(409,'This supplier capability already exists')
    before={'category_id':cap.category_id,'city':cap.city,'trade_keywords':cap.trade_keywords,'material_ids':cap.material_ids}
    cap.category_id=category.id;cap.city=city;cap.trade_keywords=payload.trade_keywords.strip()
    cap.material_ids=material_ids;cap.mapping_source='admin';cap.confirmed=True;cap.accepts_rfqs=True
    if cap.eligibility_status not in {'rejected','revoked','suspended'}:cap.eligibility_status='enabled'
    audit(db,admin,'supplier.capability_admin_updated',org_id,cap.id,before=before,after={'category_id':category.id,'city':city})
    db.commit();return {'id':cap.id,'category_id':cap.category_id,'city':cap.city,'trade_keywords':cap.trade_keywords,
        'material_ids':cap.material_ids,'confirmed':cap.confirmed,'eligibility_status':cap.eligibility_status}

@router.delete('/admin/suppliers/{org_id}/capabilities/{capability_id}',status_code=204)
def admin_delete_supplier_capability(org_id:str,capability_id:str,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);cap=db.get(Capability,capability_id)
    if not cap or cap.org_id!=org_id:raise HTTPException(404,'Supplier capability not found')
    audit(db,admin,'supplier.capability_admin_removed',org_id,cap.id,category_id=cap.category_id)
    db.delete(cap);db.commit()

def _supplier_review(org_id,admin,db,status,reason=None):
    org=_supplier_org(db,org_id)
    if status=='approved' and org.verified_status=='verified': raise HTTPException(409,'Supplier is already verified')
    if status=='approved' and db.scalar(select(Verification.id).where(Verification.org_id==org_id,Verification.document_key.is_not(None),Verification.status.in_(['pending','needs_information']))):
        raise HTTPException(409,'Uploaded verification evidence requires the existing document scanner review before approval')
    if status=='approved' and org.cr_number and db.scalar(select(Organization.id).where(Organization.id!=org.id,Organization.cr_number==org.cr_number,Organization.verified_status=='verified')):
        raise HTTPException(409,'A verified company already uses this CR')
    now=__import__('datetime').datetime.now(__import__('datetime').UTC)
    item=Verification(org_id=org.id,cr_number=org.cr_number,status=status,reviewer_id=admin.id,reviewer_notes=reason,reviewed_at=now)
    db.add(item);db.flush();org.verified_status={'approved':'verified','needs_information':'pending','rejected':'rejected'}[status]
    db.add(Outbox(event='company.verification_updated',payload={'org_id':org.id,'status':org.verified_status},dedupe_key=f'company-verification:{item.id}'))
    audit(db,admin,{'approved':'supplier.verified','needs_information':'supplier.information_requested','rejected':'supplier.rejected'}[status],org.id,item.id,reason=reason)
    db.commit();db.refresh(item);return {'id':item.id,'status':item.status,'verified_status':org.verified_status,'reviewed_at':item.reviewed_at}

@router.post('/admin/suppliers/{org_id}/verify')
def admin_verify_supplier(org_id:str,payload:SupplierReviewAction,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    return _supplier_review(org_id,admin,db,'approved',payload.reason)

@router.post('/admin/suppliers/{org_id}/request-information')
def admin_request_supplier_information(org_id:str,payload:SupplierReviewAction,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    if not payload.reason:raise HTTPException(422,'Request details are required')
    return _supplier_review(org_id,admin,db,'needs_information',payload.reason)

@router.post('/admin/suppliers/{org_id}/reject')
def admin_reject_supplier(org_id:str,payload:SupplierReviewAction,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    if not payload.reason:raise HTTPException(422,'A rejection reason is required')
    return _supplier_review(org_id,admin,db,'rejected',payload.reason)
# Publication preferences reuse the organization authorization boundary.
router.include_router(public_profile_router)

def business_records(db,organizations):
    """Keep pre-existing administrator/import contacts visible in the profile."""
    contacts={}
    ids=[org.id for org in organizations]
    if ids:
        for contact in db.scalars(select(Contact).where(Contact.org_id.in_(ids)).order_by(Contact.created_at,Contact.id)).all():
            contacts.setdefault(contact.org_id,contact)
    result=[]
    for org in organizations:
        data=record(org);contact=contacts.get(org.id)
        if contact:
            for field,source in [('contact_name','name'),('business_email','email'),('business_phone','phone')]:
                if data[field] is None:data[field]=getattr(contact,source)
        result.append(data)
    return result

@router.post('',status_code=201,response_model=OrganizationOut)
def create(payload:OrgCreate,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if missing_profile_columns(db):
        raise HTTPException(503,'Company creation requires the pending supplier profile database migration. Existing accounts can still sign in.')
    if payload.cr_number and db.scalar(select(Organization.id).where(Organization.cr_number==payload.cr_number,Organization.verified_status=='verified')):
        raise HTTPException(409,'A verified company already uses this CR')
    org=Organization(**payload.model_dump(),name_normalized=payload.name.strip().casefold(),source='registered')
    db.add(org);db.flush()
    db.add(Member(user_id=user.id,organization_id=org.id,role='owner'))
    audit(db,user,'organization.created',org.id,org.id)
    db.commit();db.refresh(org)
    return record(org)

@router.get('',response_model=list[OrganizationOut])
def mine(company_role:str|None=None,search:ListSearch=Depends(),user:User=Depends(current_user),db:Session=Depends(get_db)):
    statement=select(Organization)
    if not user.is_admin:statement=statement.join(Member,Member.organization_id==Organization.id).where(Member.user_id==user.id)
    if company_role is not None:
        if company_role not in {'buyer','supplier'}:raise HTTPException(422,'Invalid company role')
        statement=statement.where(Organization.is_supplier.is_(True) if company_role=='supplier' else Organization.is_buyer.is_(True))
    objs=db.scalars(search.apply(statement,Organization.name,Organization.city,Organization.cr_number,order=(Organization.name,Organization.id))).all()
    return business_records(db,objs)

@router.post('/admin/suppliers',status_code=201,response_model=OrganizationOut)
def create_supplier(payload:AdminSupplierCreate,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    normalized=payload.name.strip().casefold()
    if db.scalar(select(Organization.id).where(Organization.name_normalized==normalized,Organization.is_supplier.is_(True))):
        raise HTTPException(409,'A supplier with this company name already exists')
    org=Organization(name=payload.name.strip(),name_normalized=normalized,country=payload.country,address=payload.address,
                     description=payload.description,cr_number=payload.cr_number,vat_number=payload.vat_number,city=payload.city,website=payload.website,
                     is_buyer=False,is_supplier=True,source='admin_created',verified_status='verified',profile_status='incomplete')
    db.add(org);db.flush()
    contact_inputs=list(payload.contacts)
    if not contact_inputs and (payload.contact_name or payload.email or payload.phone):
        contact_inputs=[{'name':payload.contact_name,'email':payload.email,'phone':payload.phone}]
    seen_primary=False
    for index,contact_payload in enumerate(contact_inputs):
        item=contact_payload.model_dump() if hasattr(contact_payload,'model_dump') else dict(contact_payload)
        if not any(item.get(key) for key in ('name','email','phone')):continue
        _,_,contact_ids,_=import_contact_row(db,org.id,item.get('name'),item.get('email'),item.get('phone'),org.country or 'SA',f'admin-create:{admin.id}:{index}',return_ids=True)
        for contact_id in contact_ids:
            contact=db.get(Contact,contact_id)
            contact.role=(item.get('designation') or '').strip() or None
            contact.is_primary=bool(item.get('is_primary') and not seen_primary)
            seen_primary|=contact.is_primary
            category_ids=list(dict.fromkeys(item.get('category_ids') or []))
            requested_material_ids=sorted(set(item.get('material_ids') or []))
            requested_materials=db.scalars(select(Material).where(Material.id.in_(requested_material_ids),Material.active.is_(True))).all() if requested_material_ids else []
            if len(requested_materials)!=len(requested_material_ids) or (requested_materials and not category_ids):
                raise HTTPException(422,'Assigned materials must be active and have an assigned category')
            if any(not any(category_id in {material.category_id,material.main_category_id,material.subcategory_id} for category_id in category_ids) for material in requested_materials):
                raise HTTPException(422,'Every assigned material must belong to one of the contact categories')
            for category_id in category_ids:
                category=db.scalar(select(Category).where(Category.id==category_id,Category.active.is_(True)))
                if not category:raise HTTPException(422,'A contact category is not active or does not exist')
                capability=db.scalar(select(Capability).where(Capability.org_id==org.id,Capability.category_id==category.id,
                    func.lower(func.trim(Capability.city))==(org.city or '').casefold()).with_for_update())
                if capability is None:
                    capability=Capability(org_id=org.id,category_id=category.id,city=org.city or '',material_ids=[],trade_keywords='',
                        confirmed=True,accepts_rfqs=True,eligibility_status='enabled',mapping_source='admin')
                    db.add(capability);db.flush()
                materials=[m for m in requested_materials if category.id in {m.category_id,m.main_category_id,m.subcategory_id}]
                capability.material_ids=sorted(set([*(capability.material_ids or []),*[m.id for m in materials]]))
                relation=db.scalar(select(ContactCapability.id).where(ContactCapability.contact_id==contact.id,ContactCapability.capability_id==capability.id))
                if not relation:db.add(ContactCapability(contact_id=contact.id,capability_id=capability.id,is_primary=contact.is_primary))
    verification=Verification(org_id=org.id,cr_number=org.cr_number,status='approved',reviewer_id=admin.id,
        reviewer_notes='Automatically verified when created by an authenticated administrator.',reviewed_at=__import__('datetime').datetime.now(__import__('datetime').UTC))
    db.add(verification);db.flush()
    audit(db,admin,'supplier.created_by_admin',org.id,org.id)
    audit(db,admin,'supplier.automatically_verified',org.id,verification.id,source='admin_created')
    audit(db,admin,'supplier.contacts_and_categories_created',org.id,org.id,contacts=len(contact_inputs))
    db.commit();db.refresh(org)
    return record(org)

@router.get('/claimable/suppliers',response_model=list[OrganizationOut])
def claimable_suppliers(user:User=Depends(current_user),db:Session=Depends(get_db)):
    claimed=select(Member.id).where(Member.organization_id==Organization.id)
    query=select(Organization).where(
        Organization.is_supplier.is_(True),
        Organization.is_active.is_(True),
        Organization.source.in_(['admin_created','imported']),
        ~claimed.exists(),
    ).order_by(Organization.name).limit(200)
    return [record(org) for org in db.scalars(query).all()]

@router.get('/{org_id}',response_model=OrganizationOut)
def details(org_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id)
    obj=db.get(Organization,org_id)
    if not obj:raise HTTPException(404,'Organization not found')
    return business_records(db,[obj])[0]

@router.patch('/{org_id}',response_model=OrganizationOut)
def update(org_id:str,payload:OrgUpdate,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id,{'owner','manager'})
    org=db.get(Organization,org_id)
    if not org: raise HTTPException(404,'Organization not found')
    missing=missing_profile_columns(db)
    if any(value is not None and key in missing for key,value in payload.model_dump(exclude_unset=True).items()):
        raise HTTPException(503,'Saving extended company details requires the pending supplier profile database migration. No changes were saved.')
    for key,value in payload.model_dump(exclude_unset=True).items():
        if key in missing:continue
        if value is not None or key in {'trading_name','company_type','description','contact_name','designation','business_email','business_phone','country','region','address','vat_number','website'}: setattr(org,key,value)
    if payload.name:org.name_normalized=payload.name.strip().casefold()
    if org.source=='imported' and org.verified_status!='verified':
        org.profile_status='complete' if (not org.name.startswith('[Imported supplier placeholder:') and
            all((org.business_email,org.business_phone,org.city,org.address,org.contact_name))) else 'incomplete'
    audit(db,user,'organization.updated',org_id,org_id)
    db.commit();return record(org)

@router.post('/{org_id}/verification',response_model=VerificationOut)
def request_verification(org_id:str,payload:VerifyCreate,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id,{'owner','manager'})
    org=db.get(Organization,org_id)
    if not org:raise HTTPException(404,'Organization not found')
    if org.verified_status=='verified':raise HTTPException(409,'Already verified')
    if db.scalar(select(Verification.id).where(Verification.org_id==org_id,Verification.status=='pending')):
        raise HTTPException(409,'A verification request is already pending')
    if payload.document_key:
        if get_settings().r2_enabled:
            stored=db.scalar(select(StoredFile).where(StoredFile.id==payload.document_key,
                StoredFile.purpose=='verification_document',StoredFile.organization_id==org_id,
                StoredFile.owner_user_id==user.id,StoredFile.status=='available',StoredFile.deleted_at.is_(None)))
            if not stored:raise HTTPException(422,'Document must be uploaded for this organization')
        elif not payload.document_key.startswith(org_id+'/') or '/' in payload.document_key[len(org_id)+1:] or not os.path.isfile(os.path.join(get_settings().upload_dir,payload.document_key)):
            raise HTTPException(422,'Document must be uploaded for this organization')
    ver=Verification(org_id=org_id,cr_number=payload.cr_number,document_key=payload.document_key)
    org.verified_status='pending';db.add(ver);audit(db,user,'verification.requested',org_id)
    db.commit();return record(ver)

@router.get('/{org_id}/verification',response_model=list[VerificationOut])
def verification_history(org_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id)
    return [record(v) for v in db.scalars(select(Verification).where(Verification.org_id==org_id).order_by(Verification.created_at.desc())).all()]

@router.post('/{org_id}/documents')
def upload_document(org_id:str,file:UploadFile=File(...),user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id,{'owner','manager'})
    if file.content_type not in {'application/pdf','image/jpeg','image/png'}:
        raise HTTPException(415,'Unsupported document type')
    content=file.file.read(get_settings().max_upload_bytes+1)
    if len(content)>get_settings().max_upload_bytes:raise HTTPException(413,'Document too large')
    # Magic-byte check; production requires malware scan and configured private R2 storage.
    signatures={'application/pdf':content.startswith(b'%PDF'), 'image/jpeg':content.startswith(b'\xff\xd8\xff'), 'image/png':content.startswith(b'\x89PNG\r\n\x1a\n')}
    if not signatures[file.content_type]:raise HTTPException(422,'File content does not match declared type')
    settings=get_settings()
    if settings.r2_enabled:
        from hashlib import sha256
        from pathlib import PurePath
        from app.storage import StorageUnavailable,upload_file as r2_upload
        suffix=PurePath((file.filename or '').replace('\\','/')).suffix.lower()
        if suffix not in {'.pdf','.png','.jpg','.jpeg'}:raise HTTPException(415,'Unsupported document type')
        stored=StoredFile(bucket_type='private',object_key=f'private/verification/{org_id}/{uuid.uuid4()}',
            original_filename=PurePath((file.filename or 'document').replace('\\','/')).name[:200],
            safe_filename=PurePath((file.filename or 'document').replace('\\','/')).name[:200],
            content_type=file.content_type,file_extension=suffix,size_bytes=len(content),visibility='private',
            purpose='verification_document',status='available',owner_user_id=user.id,uploaded_by_user_id=user.id,
            organization_id=org_id,entity_type='organization',entity_id=org_id,checksum_sha256=sha256(content).hexdigest())
        db.add(stored)
        try:
            meta=r2_upload('private',stored.object_key,__import__('io').BytesIO(content),file.content_type,
                max_bytes=settings.r2_max_document_bytes)
            stored.size_bytes=meta['size_bytes'];stored.etag=meta['etag']
            audit(db,user,'verification.document_uploaded',org_id,file_id=stored.id)
            db.commit()
        except StorageUnavailable as exc:
            db.rollback();raise HTTPException(503,'File storage is temporarily unavailable') from exc
        return {'document_key':stored.id,'scan_status':'not_scanned'}
    if settings.is_production: raise HTTPException(503,'Production document storage is not configured')
    os.makedirs(get_settings().upload_dir,exist_ok=True)
    key=f'{org_id}/{uuid.uuid4().hex}'
    os.makedirs(os.path.join(get_settings().upload_dir,org_id),exist_ok=True)
    with open(os.path.join(get_settings().upload_dir,key),'xb') as f:f.write(content)
    audit(db,user,'verification.document_uploaded',org_id,document_key=key)
    db.commit()
    return {'document_key':key,'scan_status':'not_scanned_development_only'}

@router.get('/admin/review/verification/{verification_id}/document-url')
def verification_document_url(verification_id:str,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    item=db.get(Verification,verification_id)
    if not item or not item.document_key:raise HTTPException(404,'Verification document not found')
    settings=get_settings()
    if settings.r2_enabled:
        stored=db.scalar(select(StoredFile).where(StoredFile.id==item.document_key,
            StoredFile.purpose=='verification_document',StoredFile.organization_id==item.org_id,
            StoredFile.status=='available',StoredFile.deleted_at.is_(None)))
        if stored:
            from app.storage import StorageUnavailable,generate_download_url
            try:url=generate_download_url('private',stored.object_key,stored.safe_filename,stored.content_type)
            except StorageUnavailable as exc:raise HTTPException(503,'File storage is temporarily unavailable') from exc
            audit(db,admin,'verification.document_download_authorized',item.org_id,item.id)
            db.commit()
            return RedirectResponse(url,status_code=307,headers={'Cache-Control':'private, no-store'})
    # Existing local evidence remains available to admins while old records are retained.
    key=item.document_key
    if not key.startswith(item.org_id+'/') or '/' in key[len(item.org_id)+1:]:
        raise HTTPException(404,'Verification document not found')
    root=Path(settings.upload_dir).resolve()
    target=(root/key).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404,'Verification document not found')
    audit(db,admin,'verification.document_download_authorized',item.org_id,item.id)
    db.commit()
    return FileResponse(target,filename=target.name,media_type='application/octet-stream',
        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@router.post('/{org_id}/claims',response_model=ClaimOut)
def claim(org_id:str,payload:ClaimCreate,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org=db.get(Organization,org_id)
    if not org or org.source not in {'imported','admin_created'} or not org.is_supplier:raise HTTPException(404,'Claimable organization not found')
    if db.scalar(select(Member.id).where(Member.organization_id==org_id)):
        raise HTTPException(409,'This supplier already has an owner')
    existing=db.scalar(select(CompanyClaim).where(CompanyClaim.org_id==org_id,CompanyClaim.claimant_id==user.id,CompanyClaim.status=='pending'))
    if existing:raise HTTPException(409,'Claim already pending')
    c=CompanyClaim(org_id=org_id,claimant_id=user.id,evidence=payload.evidence)
    db.add(c);audit(db,user,'organization.claim_requested',org_id)
    db.commit();return record(c)

@router.get('/admin/review/queue',response_model=ReviewQueue)
def review_queue(admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    return {'verifications':[record(x) for x in db.scalars(select(Verification).where(Verification.status=='pending')).all()], 'claims':[record(x) for x in db.scalars(select(CompanyClaim).where(CompanyClaim.status=='pending')).all()]}

@router.post('/admin/review/verification/{verification_id}',response_model=VerificationOut)
def verification_decision(verification_id:str,payload:Decision,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    item=db.get(Verification,verification_id)
    if not item or item.status!='pending':raise HTTPException(404,'Pending verification not found')
    if payload.approve and item.document_key:
        raise HTTPException(409,'Document scanner integration required before approving uploaded evidence')
    if payload.approve and db.scalar(select(Organization.id).where(
        Organization.id!=item.org_id,
        Organization.cr_number==item.cr_number,
        Organization.verified_status=='verified',
    )):
        raise HTTPException(409,'A verified company already uses this CR')
    # Without documents, verification is still a manual attestation recorded by the admin.
    item.status='approved' if payload.approve else 'rejected';item.reviewer_id=admin.id;item.reviewer_notes=payload.notes;item.reviewed_at=__import__('datetime').datetime.now(__import__('datetime').UTC)
    org=db.get(Organization,item.org_id)
    org.verified_status='verified' if payload.approve else 'rejected'
    org.cr_number=item.cr_number
    db.add(Outbox(
        event='company.verification_updated',
        payload={'org_id':org.id,'status':org.verified_status},
        dedupe_key=f'company-verification:{item.id}',
    ))
    audit(db,admin,'verification.decision',org.id,item.id,approved=payload.approve)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409,'A verified company already uses this CR') from exc
    return record(item)

@router.post('/admin/review/claim/{claim_id}',response_model=ClaimOut)
def claim_decision(claim_id:str,payload:Decision,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    item=db.get(CompanyClaim,claim_id)
    if not item:raise HTTPException(404,'Claim not found')
    # Different pending claims share one ownership boundary. Locking only the
    # selected claim would not serialize approvals for the same organization.
    org=db.scalar(select(Organization).where(Organization.id==item.org_id).with_for_update())
    if not org:raise HTTPException(404,'Organization not found')
    db.refresh(item,with_for_update=True)
    if item.status!='pending':raise HTTPException(404,'Claim not found')
    item.status='approved' if payload.approve else 'rejected';item.reviewer_id=admin.id
    if payload.approve:
        if db.scalar(select(Member.id).where(Member.organization_id==item.org_id)):
            raise HTTPException(409,'This supplier already has an owner')
        db.add(Member(organization_id=item.org_id,user_id=item.claimant_id,role='owner'))
    audit(db,admin,'organization.claim_decision',item.org_id,item.id,approved=payload.approve)
    db.commit();return record(item)

from pydantic import BaseModel
class MemberAdd(BaseModel):
    phone:str
    phone_country:str|None=None
    role:str='viewer'

@router.get('/{org_id}/members',response_model=list[MemberOut])
def list_members(org_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id)
    return [record(m) for m in db.scalars(select(Member).where(Member.organization_id==org_id)).all()]

@router.post('/{org_id}/members',response_model=MemberOut)
def add_existing_verified_member(org_id:str,payload:MemberAdd,user:User=Depends(current_user),db:Session=Depends(get_db)):
    org_member(db,user,org_id,{'owner','manager'})
    if payload.role not in {'buyer','supplier','viewer'}:raise HTTPException(422,'Invalid invited role')
    from app.modules.auth.router import norm_phone
    canonical=norm_phone(payload.phone,payload.phone_country)
    candidate=db.scalar(select(User).where(User.phone_e164==canonical,User.phone_verified==True))
    if not candidate:raise HTTPException(404,'Verified user not found')
    if db.scalar(select(Member.id).where(Member.user_id==candidate.id,Member.organization_id==org_id)):
        raise HTTPException(409,'Already a member')
    # Direct add is a simplified pilot operation; require verified invitations/consent in production.
    if get_settings().is_production:raise HTTPException(503,'Member-invitation acceptance workflow required for production')
    m=Member(user_id=candidate.id,organization_id=org_id,role=payload.role)
    db.add(m);db.flush();audit(db,user,'organization.member_added',org_id,m.id);db.commit()
    return record(m)

@router.get('/admin/directory/unclaimed',response_model=list[OrganizationOut])
def unclaimed_companies(q:str='',admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    query=select(Organization).where(Organization.source=='imported',Organization.verified_status=='unverified')
    if q:query=query.where(Organization.name.ilike('%'+q[:100]+'%'))
    return [record(o) for o in db.scalars(query.limit(100)).all()]


class FavoriteSupplierOut(BaseModel):
    supplier_org_id:str
    name:str


class FavoriteStateOut(BaseModel):
    supplier_org_id:str
    favorite:bool


@router.get('/{org_id}/favorites',response_model=list[FavoriteSupplierOut])
def favorites(org_id:str,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.models import FavoriteSupplier
    org_member(db,user,org_id,{'owner','manager','buyer'})
    return [{'supplier_org_id':favorite.supplier_org_id,'name':name} for favorite,name in db.execute(
        select(FavoriteSupplier,Organization.name).join(Organization,Organization.id==FavoriteSupplier.supplier_org_id)
        .where(FavoriteSupplier.buyer_org_id==org_id).order_by(Organization.name,FavoriteSupplier.id).limit(limit).offset(offset)).all()]


@router.put('/{org_id}/favorites/{supplier_id}',response_model=FavoriteStateOut)
def favorite(org_id:str,supplier_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.models import FavoriteSupplier
    org_member(db,user,org_id,{'owner','manager','buyer'})
    buyer=db.scalar(select(Organization).where(Organization.id==org_id).with_for_update())
    company=db.get(Organization,supplier_id)
    if not buyer or not buyer.is_buyer:raise HTTPException(422,'Select a buyer organization')
    if not company or not company.is_supplier or not company.is_active or company.verified_status!='verified' or company.id==org_id:
        raise HTTPException(422,'Select an active verified supplier')
    if not db.scalar(select(FavoriteSupplier.id).where(FavoriteSupplier.buyer_org_id==org_id,FavoriteSupplier.supplier_org_id==supplier_id)):
        db.add(FavoriteSupplier(buyer_org_id=org_id,supplier_org_id=supplier_id));audit(db,user,'supplier.favorited',org_id,supplier_id)
    db.commit();return {'supplier_org_id':supplier_id,'favorite':True}


@router.delete('/{org_id}/favorites/{supplier_id}',response_model=FavoriteStateOut)
def unfavorite(org_id:str,supplier_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.models import FavoriteSupplier
    org_member(db,user,org_id,{'owner','manager','buyer'})
    db.scalar(select(Organization.id).where(Organization.id==org_id).with_for_update())
    favorite=db.scalar(select(FavoriteSupplier).where(FavoriteSupplier.buyer_org_id==org_id,FavoriteSupplier.supplier_org_id==supplier_id))
    if favorite:db.delete(favorite);audit(db,user,'supplier.unfavorited',org_id,supplier_id)
    db.commit();return {'supplier_org_id':supplier_id,'favorite':False}
