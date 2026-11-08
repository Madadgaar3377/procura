from app.search import ListSearch
from app.response_contracts import QuotationSummary, Comparison, QuoteDetail
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.database import get_db
from app.models import Invitation,Quotation,QuoteLine,RFQ,RFQItem,PriceBookItem,User,Member,Notification,Outbox,Organization
from app.schemas import QuoteIn
from app.security import current_user,org_member,audit
from app.serializers import record

router=APIRouter(prefix='/quotations',tags=['Supplier Quotations & Buyer Comparison'])

SUBMITTED_STATUSES = {'submitted', 'shortlisted', 'awarded', 'rejected'}

def snapshot(db,quote,lines=None):
    if lines is None:
        lines=db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()
    return {'quotation':record(quote),'lines':[record(l) for l in lines]}

def invoice(db,quote,lines=None):
    if lines is None:
        lines=db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()
    subtotal=sum((Decimal(l.quantity)*Decimal(l.unit_price) for l in lines),Decimal('0')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    vat=(subtotal*Decimal(quote.vat_rate)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    return {'subtotal':str(subtotal),'vat':str(vat),'total':str(subtotal+vat),'currency':quote.currency}

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
    for current in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all():db.delete(current)
    db.flush()
    for line in payload.lines:db.add(QuoteLine(quotation_id=quote.id,**line.model_dump()))
    if payload.price_basis not in {'supply','supply_install'}:raise HTTPException(422,'Invalid price basis')
    quote.price_basis=payload.price_basis;quote.vat_rate=payload.vat_rate
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
def comparison(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
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
    return {'rfq_id':rfq_id,'quotations':output}


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
    if decision=='awarded' and db.scalar(select(Quotation.id).where(Quotation.rfq_id==rfq.id,Quotation.status=='awarded',Quotation.id!=quote.id)):
        raise HTTPException(409,'An award already exists for this RFQ')
    before=quote.status;quote.status=decision
    audit(db,user,'quotation.'+decision,rfq.buyer_org_id,quote.id,before=before,after=decision)
    template='quotation_'+decision
    enqueue_org_notice(db,org_id=quote.supplier_org_id,template=template,
        context={'reference':rfq.reference,'title':rfq.title,'org_id':quote.supplier_org_id,
                 'url':get_settings().frontend_url.rstrip('/')+'/app/supplier/quote/'+quote.id},
        source_key=f'quotation-decision:{quote.id}:{decision}')
    recipients=db.scalars(select(Member.user_id).join(User,User.id==Member.user_id).where(Member.organization_id==quote.supplier_org_id,User.is_active.is_(True))).all()
    db.add_all(Notification(recipient_user_id=recipient,event=template,message=f'Procumaterial: {rfq.reference} quotation {decision}',status='delivered') for recipient in recipients)
    db.commit()
    return snapshot(db,quote)


@router.get('/{quotation_id}/history')
def decision_history(quotation_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    from app.models import AuditEvent
    get_quote(quotation_id,user,db)
    return [{'action':event.action,'created_at':event.created_at,'details':event.metadata_json}
            for event in db.scalars(select(AuditEvent).where(AuditEvent.entity_id==quotation_id,
            AuditEvent.action.in_({'quotation.shortlisted','quotation.awarded','quotation.rejected'})).order_by(AuditEvent.created_at,AuditEvent.id)).all()]
