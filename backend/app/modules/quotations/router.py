from app.search import ListSearch
from app.response_contracts import QuotationSummary, Comparison, QuoteDetail
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from fastapi import APIRouter,Depends,File,HTTPException,Query,Request,UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import func,select
from app.database import get_db
from app.models import Invitation,Quotation,QuoteLine,RFQ,RFQItem,PriceBookItem,StoredFile,User,Member,Notification,Outbox,Organization
from app.schemas import QuoteIn
from app.security import current_user,org_member,audit
from app.serializers import record

router=APIRouter(prefix='/quotations',tags=['Supplier Quotations & Buyer Comparison'])

SUBMITTED_STATUSES = {'submitted', 'shortlisted', 'awarded', 'rejected'}

def snapshot(db,quote,lines=None):
    if lines is None:
        lines=db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()
    attachments=[{'id':f.id,'filename':f.safe_filename,'content_type':f.content_type,'size':f.size_bytes}
        for f in db.scalars(select(StoredFile).where(StoredFile.entity_type=='quotation',StoredFile.entity_id==quote.id,
            StoredFile.purpose=='quotation_attachment',StoredFile.status=='available',StoredFile.deleted_at.is_(None)).order_by(StoredFile.created_at)).all()]
    from app.modules.quotations.document import quotation_document
    return {'quotation':record(quote),'lines':[record(l) for l in lines],'attachments':attachments,
        'document':quotation_document(db,quote,lines)}

def invoice(db,quote,lines=None):
    """Backend-calculated Decimal totals, including line discounts (see procurement.money)."""
    from app.modules.procurement.money import Line,totals,d
    if lines is None:
        lines=db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()
    terms=quote.commercial_terms or {}
    try:
        result=totals([Line(d(l.quantity),d(l.unit_price),d(getattr(l,'discount_percent',None)),d(l.vat_rate if l.vat_rate is not None else quote.vat_rate)) for l in lines],
            header_discount=terms.get('discount_amount') or '0',delivery_charge=terms.get('delivery_charge') or '0',delivery_vat_rate=terms.get('delivery_vat_rate') or '0')
    except ValueError as exc:
        raise HTTPException(422,'Invalid quotation discount or delivery charge') from exc
    output={'subtotal':str(result['subtotal']),'vat':str(result['vat']),'total':str(result['total']),'currency':quote.currency}
    if result['discount'] or result['delivery_charge']:
        output.update(discount=str(result['discount']),delivery_charge=str(result['delivery_charge']))
    return output

@router.get('',response_model=list[QuotationSummary])
def list_quotes(search:ListSearch=Depends(),org_id:str|None=None,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from sqlalchemy import or_
    supplier_orgs=select(Member.organization_id).join(Organization,Organization.id==Member.organization_id).where(Organization.is_active.is_(True),Member.user_id==user.id,Member.role.in_({'owner','manager','supplier'}))
    buyer_orgs=select(Member.organization_id).join(Organization,Organization.id==Member.organization_id).where(Organization.is_active.is_(True),Member.user_id==user.id,Member.role.in_({'owner','manager','buyer'}))
    query=select(Quotation).join(RFQ,RFQ.id==Quotation.rfq_id)
    if not user.is_admin:
        query=query.where(or_(Quotation.supplier_org_id.in_(supplier_orgs),(Quotation.status.in_(SUBMITTED_STATUSES)) & RFQ.buyer_org_id.in_(buyer_orgs)))
    if org_id:
        org_member(db,user,org_id,{'owner','manager','buyer','supplier'})
        query=query.where(or_(Quotation.supplier_org_id==org_id,(Quotation.status.in_(SUBMITTED_STATUSES)) & (RFQ.buyer_org_id==org_id)))
    quotes=db.scalars(search.apply(query,RFQ.reference,RFQ.title,Quotation.status,Quotation.id,order=(Quotation.created_at.desc(),Quotation.id))).all()
    ids=[q.id for q in quotes]
    lines_by_quote={key:[] for key in ids}
    for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id.in_(ids))).all():lines_by_quote[line.quotation_id].append(line)
    suppliers=dict(db.execute(select(Organization.id,Organization.name).where(Organization.id.in_({q.supplier_org_id for q in quotes}))).all())
    rfqs={r.id:r for r in db.scalars(select(RFQ).where(RFQ.id.in_({q.rfq_id for q in quotes}))).all()}
    return [{**record(q),'supplier_name':suppliers[q.supplier_org_id],'rfq_reference':rfqs[q.rfq_id].reference,
             'rfq_title':rfqs[q.rfq_id].title,'totals':invoice(db,q,lines_by_quote[q.id])} for q in quotes]

@router.post('/invitations/{invitation_id}/auto-draft',response_model=QuoteDetail)
def draft(invitation_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    inv=db.scalar(select(Invitation).where(Invitation.id==invitation_id).with_for_update())
    if not inv:raise HTTPException(404,'Invitation not found')
    org_member(db,user,inv.supplier_org_id,{'owner','manager','supplier'})
    if inv.status!='accepted':raise HTTPException(409,'Invitation must first be accepted')
    rfq=db.get(RFQ,inv.rfq_id)
    deadline=rfq.response_deadline.replace(tzinfo=timezone.utc) if rfq.response_deadline.tzinfo is None else rfq.response_deadline.astimezone(timezone.utc)
    if rfq.status in {'closed','cancelled'} or deadline<datetime.now(timezone.utc):raise HTTPException(409,'RFQ closed')
    from app.modules.quotations.automation import build_draft
    quote=build_draft(db,inv)
    audit(db,user,'quotation.draft_generated',inv.supplier_org_id,quote.id)
    db.commit();return snapshot(db,quote)

@router.put('/{quotation_id}/lines',response_model=QuoteDetail)
def edit(quotation_id:str,payload:QuoteIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    quote=db.get(Quotation,quotation_id)
    if not quote:raise HTTPException(404,'Quotation not found')
    org_member(db,user,quote.supplier_org_id,{'owner','manager','supplier'})
    # RFQ before quotation is also the decision/publication lock order.
    rfq=db.scalar(select(RFQ).where(RFQ.id==quote.rfq_id).with_for_update())
    db.refresh(quote,with_for_update=True)
    if quote.status!='draft':raise HTTPException(409,'Submitted quotations are immutable')
    quote.submission_source='manual';quote.review_required=True
    quote.review_reasons=['Review quotation before submitting']
    deadline=rfq.response_deadline.replace(tzinfo=timezone.utc) if rfq.response_deadline.tzinfo is None else rfq.response_deadline
    if rfq.status not in {'published','collecting_quotes'} or deadline<=datetime.now(timezone.utc):raise HTTPException(409,'RFQ is closed')
    valid={i.id for i in db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id)).all()}
    used=set()
    for line in payload.lines:
        if line.rfq_item_id not in valid or line.rfq_item_id in used:raise HTTPException(422,'Invalid or duplicate quoted item')
        if line.compliance not in {'compliant','partial','noncompliant','unconfirmed'}:raise HTTPException(422,'Invalid compliance')
        used.add(line.rfq_item_id)
    originals={line.rfq_item_id:line for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id))}
    for current in originals.values():db.delete(current)
    db.flush()
    for line in payload.lines:
        original=originals.get(line.rfq_item_id)
        provenance={key:getattr(original,key) for key in ('source_rate_id','source_rate_version','source_rate_snapshot')} if original else {}
        db.add(QuoteLine(quotation_id=quote.id,**line.model_dump(),**provenance))
    if payload.price_basis not in {'supply','supply_install'}:raise HTTPException(422,'Invalid price basis')
    quote.price_basis=payload.price_basis;quote.vat_rate=payload.vat_rate
    quote.currency=payload.currency
    quote.commercial_terms=payload.commercial_terms.model_dump(mode='json')
    audit(db,user,'quotation.draft_updated',quote.supplier_org_id,quote.id);db.commit()
    return snapshot(db,quote)

@router.get('/{quotation_id}',response_model=QuoteDetail)
def get_quote(quotation_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    quote=db.get(Quotation,quotation_id)
    if not quote:raise HTTPException(404,'Quotation not found')
    rfq=db.get(RFQ,quote.rfq_id)
    supplier_role=db.scalar(select(Member.role).join(Organization,Organization.id==Member.organization_id).where(Organization.is_active.is_(True),Member.user_id==user.id,Member.organization_id==quote.supplier_org_id))
    buyer_role=db.scalar(select(Member.role).join(Organization,Organization.id==Member.organization_id).where(Organization.is_active.is_(True),Member.user_id==user.id,Member.organization_id==rfq.buyer_org_id))
    supplier_access=supplier_role in {'owner','manager','supplier'}
    buyer_access=quote.status in SUBMITTED_STATUSES and buyer_role in {'owner','manager','buyer'}
    if not (user.is_admin or supplier_access or buyer_access):
        raise HTTPException(404,'Quotation not found')
    result=snapshot(db,quote);result['totals']=invoice(db,quote)
    result['supplier_name']=db.get(Organization,quote.supplier_org_id).name
    result['rfq_reference']=rfq.reference
    return result


@router.get('/{quotation_id}/export.pdf')
def export_quote_pdf(
    quotation_id: str,
    lang: str = 'en',
    template_key: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Download a configurable bilingual (EN/AR + RTL) quotation PDF independently from matching."""
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    from app.modules.quotations.document import build_quotation_pdf, quotation_document

    quote = db.get(Quotation, quotation_id)
    if not quote:
        raise HTTPException(404, 'Quotation not found')
    rfq = db.get(RFQ, quote.rfq_id)
    supplier_role = db.scalar(select(Member.role).join(Organization, Organization.id == Member.organization_id).where(Organization.is_active.is_(True), Member.user_id == user.id, Member.organization_id == quote.supplier_org_id))
    buyer_role = db.scalar(select(Member.role).join(Organization, Organization.id == Member.organization_id).where(Organization.is_active.is_(True), Member.user_id == user.id, Member.organization_id == rfq.buyer_org_id))
    supplier_access = supplier_role in {'owner', 'manager', 'supplier'}
    buyer_access = quote.status in SUBMITTED_STATUSES and buyer_role in {'owner', 'manager', 'buyer'}
    if not (user.is_admin or supplier_access or buyer_access):
        raise HTTPException(404, 'Quotation not found')
    lines = db.scalars(select(QuoteLine).where(QuoteLine.quotation_id == quote.id)).all()
    doc = quotation_document(db, quote, lines)
    pdf_bytes = build_quotation_pdf(doc, lang=lang, template_key=template_key)
    safe_ref = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '_' for ch in str(doc.get('quotation_reference') or quote.id))
    filename = f'Quotation-{safe_ref}-{lang}.pdf'
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type='application/pdf',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'private, no-store',
        },
    )

@router.post('/{quotation_id}/submit')
def submit(quotation_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    quote=db.get(Quotation,quotation_id)
    if not quote:raise HTTPException(404,'Quotation not found')
    org_member(db,user,quote.supplier_org_id,{'owner','manager','supplier'})
    rfq=db.scalar(select(RFQ).where(RFQ.id==quote.rfq_id).with_for_update())
    db.refresh(quote,with_for_update=True)
    deadline=rfq.response_deadline.replace(tzinfo=timezone.utc) if rfq.response_deadline.tzinfo is None else rfq.response_deadline.astimezone(timezone.utc)
    if quote.status!='draft' or rfq.status not in {'published','collecting_quotes'} or deadline<=datetime.now(timezone.utc):
        raise HTTPException(409,'Quotation cannot be submitted')
    if not db.scalar(select(QuoteLine.id).where(QuoteLine.quotation_id==quote.id)):
        raise HTTPException(422,'Add quote lines first')
    from app.modules.quotations.automation import submit_record
    submit_record(db,quote,user)
    db.commit()
    return {'quotation':record(quote),'totals':invoice(db,quote)}

@router.get('/rfqs/{rfq_id}/comparison',response_model=Comparison)
def comparison(rfq_id:str,price_weight:int|None=Query(None,ge=0,le=100),delivery_weight:int|None=Query(None,ge=0,le=100),terms_weight:int|None=Query(None,ge=0,le=100),user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Side-by-side quotations plus the client's weighted ranking (see procurement.scoring)."""
    from app.modules.procurement.scoring import compare,normalize_weights
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    output=[]
    quotes=db.scalars(select(Quotation).where(Quotation.rfq_id==rfq_id,Quotation.status.in_(SUBMITTED_STATUSES))).all()
    from app.models import FavoriteSupplier
    suppliers=dict(db.execute(select(Organization.id,Organization.name).where(Organization.id.in_({q.supplier_org_id for q in quotes}))).all())
    favorites=set(db.scalars(select(FavoriteSupplier.supplier_org_id).where(FavoriteSupplier.buyer_org_id==rfq.buyer_org_id)).all())
    quote_ids=[quote.id for quote in quotes]
    lines=db.scalars(select(QuoteLine).where(QuoteLine.quotation_id.in_(quote_ids))).all() if quote_ids else []
    lines_by_quote={quote_id:[] for quote_id in quote_ids}
    for line in lines:lines_by_quote[line.quotation_id].append(line)
    for quote in quotes:
        quote_lines=lines_by_quote[quote.id]
        result=snapshot(db,quote,quote_lines);result['totals']=invoice(db,quote,quote_lines)
        result.update(supplier_name=suppliers[quote.supplier_org_id],rfq_reference=rfq.reference,favorite=quote.supplier_org_id in favorites)
        output.append(result)
    requested=None if price_weight is None and delivery_weight is None and terms_weight is None else {'price':price_weight or 0,'delivery':delivery_weight or 0,'warranty':terms_weight or 0}
    ranked=compare(db,rfq,normalize_weights(requested) if requested else None)
    return {'rfq_id':rfq_id,'quotations':output,'weights':ranked['weights'],'ranking':ranked['ranking'],'method':ranked['method'],'currency':ranked['currency']}


@router.post('/{quotation_id}/decision/{decision}',response_model=QuoteDetail)
def decide(quotation_id:str,decision:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.modules.notifications.service import enqueue_org_notice
    from app.config import get_settings
    if decision not in {'shortlisted','awarded','rejected'}:raise HTTPException(422,'Choose shortlisted, awarded or rejected')
    quote=db.get(Quotation,quotation_id)
    if not quote:raise HTTPException(404,'Quotation not found')
    rfq=db.scalar(select(RFQ).where(RFQ.id==quote.rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    db.refresh(quote)
    if quote.status==decision:return snapshot(db,quote)
    if quote.status not in {'submitted','shortlisted'} or (quote.status=='shortlisted' and decision=='shortlisted'):
        raise HTTPException(409,'Quotation cannot receive this decision')
    if decision=='awarded':
        # Split awards are allowed for partial quotations, but never for the same RFQ line twice.
        mine=set(db.scalars(select(QuoteLine.rfq_item_id).where(QuoteLine.quotation_id==quote.id)))
        taken=set(db.scalars(select(QuoteLine.rfq_item_id).join(Quotation,Quotation.id==QuoteLine.quotation_id).where(
            Quotation.rfq_id==rfq.id,Quotation.status=='awarded',Quotation.id!=quote.id)))
        if mine & taken:raise HTTPException(409,'An award already exists for this RFQ')
    before=quote.status;quote.status=decision
    audit(db,user,'quotation.'+decision,rfq.buyer_org_id,quote.id,before=before,after=decision)
    template='quotation_'+decision
    enqueue_org_notice(db,org_id=quote.supplier_org_id,template=template,
        context={'reference':rfq.reference,'title':rfq.title,'org_id':quote.supplier_org_id,
                 'url':get_settings().frontend_url.rstrip('/')+'/app/supplier/quote/'+quote.id},
        source_key=f'quotation-decision:{quote.id}:{decision}')
    recipients=db.scalars(select(Member.user_id).join(User,User.id==Member.user_id).where(Member.organization_id==quote.supplier_org_id,User.is_active.is_(True))).all()
    db.add_all(Notification(recipient_user_id=recipient,event=template,message=f'Procumaterial: {rfq.reference} quotation {decision}',status='delivered') for recipient in recipients)
    if decision=='awarded':
        # Client guide: an approved quotation automatically becomes a draft purchase order.
        from app.modules.procurement.orders import prepare_purchase_order
        prepare_purchase_order(db,quote,user)
    db.commit()
    return snapshot(db,quote)


@router.get('/{quotation_id}/history')
def decision_history(quotation_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.models import AuditEvent
    get_quote(quotation_id,user,db)
    return [{'action':event.action,'created_at':event.created_at,'details':event.metadata_json}
            for event in db.scalars(select(AuditEvent).where(AuditEvent.entity_id==quotation_id,
            AuditEvent.action.in_({'quotation.shortlisted','quotation.awarded','quotation.rejected'})).order_by(AuditEvent.created_at,AuditEvent.id)).all()]


@router.post('/{quotation_id}/attachments')
def upload_quote_attachment(quotation_id:str,request:Request,file:UploadFile=File(...),user:User=Depends(current_user),db:Session=Depends(get_db)):
    import io
    from hashlib import sha256
    from pathlib import Path
    from uuid import uuid4
    from app.config import get_settings
    from app.rate_limit import enforce_rate_limit
    from app.storage import (ALLOWED_DOCUMENT_TYPES,StorageUnavailable,StorageValidationError,
        build_object_key,cleanup_object_best_effort,safe_filename,upload_file as r2_upload,validate_file_content)

    quote=db.get(Quotation,quotation_id)
    if not quote:raise HTTPException(404,'Quotation not found')
    org_member(db,user,quote.supplier_org_id,{'owner','manager','supplier'})
    if quote.status!='draft':raise HTTPException(409,'Only draft quotation attachments may be changed')
    settings=get_settings()
    if settings.is_production and not settings.r2_enabled:raise HTTPException(503,'Private attachment storage is not configured')
    count=db.scalar(select(func.count(StoredFile.id)).where(StoredFile.entity_type=='quotation',
        StoredFile.entity_id==quote.id,StoredFile.purpose=='quotation_attachment',
        StoredFile.status.in_(['pending_upload','available']),StoredFile.deleted_at.is_(None))) or 0
    if count>=10:raise HTTPException(422,'At most 10 quotation attachments are allowed')
    max_bytes=min(settings.max_upload_bytes,settings.r2_max_document_bytes)
    content=file.file.read(max_bytes+1)
    try:
        suffix,ct=validate_file_content(file.filename,file.content_type,content,ALLOWED_DOCUMENT_TYPES,max_bytes)
    except StorageValidationError as exc:
        raise HTTPException(exc.status_code,exc.message) from exc
    enforce_rate_limit(request,scope='api-r2-upload',identity=user.id,limit=10,window_seconds=60)
    filename=safe_filename(file.filename,suffix)
    key=build_object_key('private/quotation',quote.id,suffix)
    supplier_org_id=quote.supplier_org_id
    # Release read transaction before network/disk I/O so no DB lock is held during upload.
    db.rollback()
    stored=StoredFile(bucket_type='private',object_key=key,original_filename=filename,safe_filename=filename,
        content_type=ct,file_extension=suffix,size_bytes=len(content),visibility='private',
        purpose='quotation_attachment',status='pending_upload',owner_user_id=user.id,
        uploaded_by_user_id=user.id,organization_id=supplier_org_id,entity_type='quotation',entity_id=quotation_id)
    uploaded_to_r2=False
    local_target:Path|None=None
    if settings.r2_enabled:
        try:
            res=r2_upload('private',key,io.BytesIO(content),ct,max_bytes=max_bytes)
            stored.size_bytes=res['size_bytes'];stored.etag=res['etag'];stored.checksum_sha256=res['checksum_sha256']
            uploaded_to_r2=True
        except StorageUnavailable as exc:
            raise HTTPException(503,'File storage is temporarily unavailable') from exc
    else:
        root=(Path(settings.upload_dir).resolve()/'quotation-attachments').resolve()
        local_target=(root/f'{quotation_id}/{uuid4().hex}{suffix}').resolve()
        local_target.parent.mkdir(parents=True,exist_ok=True)
        local_target.write_bytes(content)
        stored.object_key=str(local_target.relative_to(root).as_posix())
        stored.checksum_sha256=sha256(content).hexdigest()
    try:
        locked_quote=db.scalar(select(Quotation).where(Quotation.id==quotation_id).with_for_update())
        if not locked_quote or locked_quote.status!='draft':
            raise HTTPException(409,'Only draft quotation attachments may be changed')
        stored.status='available'
        db.add(stored)
        db.flush()
        audit(db,user,'quotation.attachment_uploaded',supplier_org_id,locked_quote.id,file_id=stored.id)
        db.commit()
    except Exception:
        db.rollback()
        if uploaded_to_r2:cleanup_object_best_effort('private',key)
        if local_target and local_target.exists():local_target.unlink(missing_ok=True)
        raise
    return {'id':stored.id,'filename':stored.safe_filename,'content_type':stored.content_type,'size':stored.size_bytes}


@router.get('/{quotation_id}/attachments/{attachment_id}')
def download_quote_attachment(quotation_id:str,attachment_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from pathlib import Path
    from fastapi.responses import FileResponse,RedirectResponse
    from app.config import get_settings
    from app.storage import StorageUnavailable,generate_download_url

    get_quote(quotation_id,user,db)
    stored=db.scalar(select(StoredFile).where(StoredFile.id==attachment_id,StoredFile.entity_type=='quotation',
        StoredFile.entity_id==quotation_id,StoredFile.purpose=='quotation_attachment',
        StoredFile.status=='available',StoredFile.deleted_at.is_(None)))
    if not stored:raise HTTPException(404,'Attachment not found')
    settings=get_settings()
    if settings.r2_enabled:
        try:url=generate_download_url('private',stored.object_key,stored.safe_filename,stored.content_type)
        except StorageUnavailable as exc:raise HTTPException(503,'File storage is temporarily unavailable') from exc
        audit(db,user,'file.download_authorized',stored.organization_id,stored.id);db.commit()
        return RedirectResponse(url,status_code=307,headers={'Cache-Control':'private, no-store','X-Robots-Tag':'noindex, nofollow'})
    if settings.is_production:raise HTTPException(503,'File storage is not configured')
    root=(Path(settings.upload_dir).resolve()/'quotation-attachments').resolve()
    target=(root/stored.object_key).resolve()
    if not target.is_relative_to(root) or not target.is_file():raise HTTPException(404,'Attachment not found')
    return FileResponse(target,media_type=stored.content_type,filename=stored.safe_filename,
        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','X-Robots-Tag':'noindex, nofollow'})


@router.delete('/{quotation_id}/attachments/{attachment_id}')
def remove_quote_attachment(quotation_id:str,attachment_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from datetime import UTC,datetime
    from pathlib import Path
    from app.config import get_settings
    from app.storage import cleanup_object_best_effort

    quote=db.scalar(select(Quotation).where(Quotation.id==quotation_id).with_for_update())
    if not quote:raise HTTPException(404,'Quotation not found')
    org_member(db,user,quote.supplier_org_id,{'owner','manager','supplier'})
    if quote.status!='draft':raise HTTPException(409,'Only draft quotation attachments may be changed')
    stored=db.scalar(select(StoredFile).where(StoredFile.id==attachment_id,StoredFile.entity_type=='quotation',
        StoredFile.entity_id==quotation_id,StoredFile.purpose=='quotation_attachment'))
    if not stored or stored.deleted_at is not None:raise HTTPException(404,'Attachment not found')
    stored.status='deleted';stored.deleted_at=datetime.now(UTC)
    audit(db,user,'quotation.attachment_removed',quote.supplier_org_id,quote.id,attachment_id=stored.id)
    db.commit()
    if get_settings().r2_enabled:cleanup_object_best_effort('private',stored.object_key)
    else:
        root=(Path(get_settings().upload_dir).resolve()/'quotation-attachments').resolve()
        target=(root/stored.object_key).resolve()
        if target.is_relative_to(root) and target.is_file():target.unlink(missing_ok=True)
    return {'removed':True}
