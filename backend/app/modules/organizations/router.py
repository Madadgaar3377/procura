from app.search import ListSearch
from app.response_contracts import ClaimOut, MemberOut, OrganizationOut, ReviewQueue, VerificationOut
import os, uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, func, literal
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models import Organization,User,Member,Verification,CompanyClaim,Outbox,Contact,StoredFile,Capability,Category,Material,ImportedSupplierTrade,AuditEvent
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

class SupplierReviewAction(BaseModel):
    reason: str | None = Field(default=None, min_length=4, max_length=2000)

def _supplier_org(db, org_id):
    org=db.get(Organization,org_id)
    if not org or not org.is_supplier: raise HTTPException(404,'Supplier not found')
    return org

def _refresh_supplier_profile_status(org,db):
    contacts=db.scalars(select(Contact).where(Contact.org_id==org.id)).all()
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

def _contact_data(contact):
    return {key:getattr(contact,key) for key in ('id','name','role','email','raw_email','phone','raw_phone','phone_normalized','fax','needs_review','created_at')}

@router.get('/admin/suppliers')
def admin_supplier_list(q:str='', status_filter:str='all', limit:int=Query(26,ge=1,le=100), offset:int=Query(0,ge=0), admin:User=Depends(admin_user), db:Session=Depends(get_db)):
    if status_filter not in {'all','verified','unverified','incomplete','imported','pending_verification'}: raise HTTPException(422,'Invalid supplier filter')
    missing=missing_profile_columns(db)
    stmt=select(Organization).where(Organization.is_supplier.is_(True))
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
    contacts=db.scalars(select(Contact).where(Contact.org_id==org_id).order_by(Contact.created_at,Contact.id)).all()
    verifications=db.scalars(select(Verification).where(Verification.org_id==org_id).order_by(Verification.created_at.desc())).all()
    latest=verifications[0] if verifications else None
    caps=db.scalars(select(Capability).where(Capability.org_id==org_id).order_by(Capability.created_at,Capability.id)).all()
    cap_items=[]
    for cap in caps:
        category=db.get(Category,cap.category_id)
        parent=db.get(Category,category.parent_id) if category and category.parent_id else None
        materials=db.scalars(select(Material).where(Material.id.in_(cap.material_ids or []))).all() if cap.material_ids else []
        cap_items.append({'id':cap.id,'main_category':parent.name_en if parent else (category.name_en if category else None),'subcategory':category.name_en if parent else None,'category_id':cap.category_id,'materials':[{'id':m.id,'name':m.name_en,'unit':m.unit} for m in materials],'units':sorted({m.unit for m in materials if m.unit}),'trade_keywords':cap.trade_keywords,'confirmed':cap.confirmed,'eligibility_status':cap.eligibility_status,'city':cap.city})
    trades=db.scalars(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id==org_id).order_by(ImportedSupplierTrade.created_at)).all()
    files=db.scalars(select(StoredFile).where(StoredFile.organization_id==org_id,StoredFile.status=='available',StoredFile.deleted_at.is_(None))).all()
    docs=[{'id':v.id,'name':v.document_key.rsplit('/',1)[-1] if v.document_key else 'Verification document','status':v.status,'download_url':f'/organizations/admin/review/verification/{v.id}/document-url'} for v in verifications if v.document_key]
    for f in files:
        if not any(x['id']==f.id for x in docs):
            docs.append({'id':f.id,'name':f.safe_filename,'purpose':f.purpose,'status':f.status,'download_url':f'/files/{f.id}/download-url'})
    last_audit=db.scalar(select(func.max(AuditEvent.created_at)).where(AuditEvent.org_id==org_id))
    return {'company':{key:getattr(org,key) for key in ('id','name','country','city','address','cr_number','vat_number','website','description','source','created_at','contact_name','designation','business_email','business_phone')},
        'contacts':[_contact_data(c) for c in contacts],
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
    if contact is None: contact=Contact(org_id=org_id,permission_to_notify=False)
    if 'name' in values: contact.name=(values['name'] or '').strip() or None
    if 'role' in values: contact.role=(values['role'] or '').strip() or None
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
    contact.needs_review=review
    is_new=contact.id is None
    db.add(contact);db.flush();_refresh_supplier_profile_status(db.get(Organization,org_id),db);audit(db,admin,'supplier.contact_added' if is_new else 'supplier.contact_updated',org_id,contact.id)
    db.commit();db.refresh(contact);return _contact_data(contact)

@router.post('/admin/suppliers/{org_id}/contacts',status_code=201)
def admin_add_supplier_contact(org_id:str,payload:SupplierContactAdminInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);return _save_admin_contact(org_id,payload,admin,db)

@router.patch('/admin/suppliers/{org_id}/contacts/{contact_id}')
def admin_edit_supplier_contact(org_id:str,contact_id:str,payload:SupplierContactAdminInput,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    _supplier_org(db,org_id);contact=db.get(Contact,contact_id)
    if not contact or contact.org_id!=org_id:raise HTTPException(404,'Supplier contact not found')
    return _save_admin_contact(org_id,payload,admin,db,contact)

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
    try:
        phone=normalize_business_phone(payload.phone) if payload.phone else None
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    email=norm_email(payload.email) if payload.email else None
    normalized=payload.name.strip().casefold()
    if db.scalar(select(Organization.id).where(Organization.name_normalized==normalized,Organization.is_supplier.is_(True))):
        raise HTTPException(409,'A supplier with this company name already exists')
    org=Organization(name=payload.name.strip(),name_normalized=normalized,
                     cr_number=payload.cr_number,city=payload.city,website=payload.website,
                     is_buyer=False,is_supplier=True,source='admin_created')
    db.add(org);db.flush()
    if payload.contact_name or phone or email:
        db.add(Contact(org_id=org.id,name=payload.contact_name,phone=phone,email=email,permission_to_notify=False))
    audit(db,admin,'supplier.created_by_admin',org.id,org.id)
    db.commit();db.refresh(org)
    return record(org)

@router.get('/claimable/suppliers',response_model=list[OrganizationOut])
def claimable_suppliers(user:User=Depends(current_user),db:Session=Depends(get_db)):
    claimed=select(Member.id).where(Member.organization_id==Organization.id)
    query=select(Organization).where(
        Organization.is_supplier.is_(True),
        Organization.is_active.is_(True),
        Organization.verified_status=='unverified',
        Organization.source=='admin_created',
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
