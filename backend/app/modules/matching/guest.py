"""Expiring bearer access to exactly one invitation, reauthorized on every use."""
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Invitation, Organization, Quotation, QuoteLine, RFQ, RFQItem
from app.schemas import Model, QuoteIn
from app.response_contracts import RFQItemOut, ResponseModel
from app.security import as_utc, audit
from app.rate_limit import enforce_rate_limit
from app.modules.notifications.recipients import current_recipient

router=APIRouter(prefix='/guest-invitations',tags=['Supplier Matching'])


def invitation_token(invitation, rfq, payload):
    settings=get_settings();now=datetime.now(UTC)
    return jwt.encode({'sub':invitation.id,'org':invitation.supplier_org_id,'rfq':rfq.id,
        'destination':payload['destination'],'channel':payload['channel'],
        'template':payload['template'],'typ':'rfq-invitation',
        'iat':now,'exp':min(as_utc(rfq.response_deadline),now+timedelta(days=7)),
        'iss':settings.jwt_issuer,'aud':'procumaterial-rfq-invitation'},settings.jwt_secret,algorithm='HS256')


class GuestAccess(Model):
    token:str=Field(min_length=20,max_length=4096)


class GuestQuote(GuestAccess):
    quotation:QuoteIn
    confirm_submission:bool=False


class GuestLine(ResponseModel):
    rfq_item_id:str
    unit_price:str
    quantity:str
    brand:str
    model:str
    offered_specs:str
    lead_days:int
    compliance:str


class GuestInvitationOut(ResponseModel):
    currency:str
    invitation_id:str
    supplier_name:str
    reference:str
    title:str
    city:str
    response_deadline:datetime
    items:list[RFQItemOut]
    quotation_id:str|None
    quotation_status:str|None
    lines:list[GuestLine]


class GuestQuoteOut(ResponseModel):
    quotation_id:str
    status:str


def authorize(db, token, *, lock=False):
    settings=get_settings()
    try:
        claims=jwt.decode(token,settings.jwt_secret,algorithms=['HS256'],issuer=settings.jwt_issuer,
            audience='procumaterial-rfq-invitation',options={'require':['sub','org','rfq','destination','channel','template','typ','iat','exp']})
        if claims['typ']!='rfq-invitation' or claims['channel'] not in {'email','sms'}:raise ValueError()
    except (jwt.PyJWTError,ValueError,KeyError):raise HTTPException(401,'Invalid or expired invitation') from None
    # Match the RFQ -> invitation lock order used by quotation submission.
    query=select(RFQ).where(RFQ.id==claims['rfq'])
    rfq=db.scalar(query.with_for_update() if lock else query)
    query=select(Invitation).where(Invitation.id==claims['sub'],Invitation.rfq_id==claims['rfq'],Invitation.supplier_org_id==claims['org'])
    inv=db.scalar(query.with_for_update() if lock else query)
    if not inv or not rfq or inv.status=='declined' or rfq.status not in {'published','collecting_quotes'} or as_utc(rfq.response_deadline)<=datetime.now(UTC):
        raise HTTPException(403,'Invitation is no longer available')
    # Persisted final matching result + active, non-suspended supplier (no re-matching).
    from app.modules.notifications.procurement import eligible_orgs
    if inv.supplier_org_id not in eligible_orgs(db,rfq,[inv.supplier_org_id]):
        raise HTTPException(403,'Invitation is no longer available')
    context={'org_id':inv.supplier_org_id,'rfq_id':rfq.id}
    if not current_recipient(db,{'context':context,'template':claims['template'],'channel':claims['channel'],'destination':claims['destination']}):
        raise HTTPException(403,'Invitation authorization changed')
    return inv,rfq


def limit(request):
    enforce_rate_limit(request,scope='guest-invitation',identity='guest',limit=60,window_seconds=60)


@router.post('/open',response_model=GuestInvitationOut)
def open_invitation(payload:GuestAccess,request:Request,response:Response,db:Session=Depends(get_db)):
    limit(request);response.headers['Cache-Control']='no-store'
    inv,rfq=authorize(db,payload.token)
    from app.serializers import record
    quote=db.scalar(select(Quotation).where(Quotation.invitation_id==inv.id))
    return {'invitation_id':inv.id,'supplier_name':db.get(Organization,inv.supplier_org_id).name,
        'currency':quote.currency if quote else 'SAR',
        'reference':rfq.reference,'title':rfq.title,'city':rfq.city,'response_deadline':rfq.response_deadline,
        'items':[record(i) for i in db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id))],
        'quotation_id':quote.id if quote else None,'quotation_status':quote.status if quote else None,
        'lines':[{'rfq_item_id':l.rfq_item_id,'unit_price':str(l.unit_price),'quantity':str(l.quantity),
            'brand':l.brand,'model':l.model,'offered_specs':l.offered_specs,'lead_days':l.lead_days,
            'compliance':l.compliance} for l in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id))] if quote else []}


@router.post('/quotation',response_model=GuestQuoteOut)
def save_quotation(payload:GuestQuote,request:Request,response:Response,db:Session=Depends(get_db)):
    limit(request);response.headers['Cache-Control']='no-store'
    inv,rfq=authorize(db,payload.token,lock=True)
    quote=db.scalar(select(Quotation).where(Quotation.invitation_id==inv.id).with_for_update())
    if quote and quote.status!='draft':
        raise HTTPException(409,'Quotation already submitted')
    valid=set(db.scalars(select(RFQItem.id).where(RFQItem.rfq_id==rfq.id)));used=set()
    for line in payload.quotation.lines:
        if line.rfq_item_id not in valid or line.rfq_item_id in used:raise HTTPException(422,'Invalid or duplicate quoted item')
        used.add(line.rfq_item_id)
    if payload.quotation.price_basis not in {'supply','supply_install'}:raise HTTPException(422,'Invalid price basis')
    if not quote:
        quote=Quotation(invitation_id=inv.id,rfq_id=rfq.id,supplier_org_id=inv.supplier_org_id)
        db.add(quote);db.flush()
    originals={line.rfq_item_id:line for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id))}
    for line in originals.values():db.delete(line)
    db.flush()
    for line in payload.quotation.lines:
        original=originals.get(line.rfq_item_id)
        provenance={key:getattr(original,key) for key in ('source_rate_id','source_rate_version','source_rate_snapshot')} if original else {}
        db.add(QuoteLine(quotation_id=quote.id,**line.model_dump(),**provenance))
    quote.price_basis=payload.quotation.price_basis;quote.vat_rate=payload.quotation.vat_rate
    quote.currency=payload.quotation.currency
    quote.commercial_terms=payload.quotation.commercial_terms.model_dump(mode='json')
    quote.submission_source='manual';quote.review_required=True;inv.status='accepted'
    audit(db,None,'quotation.guest_draft_updated',inv.supplier_org_id,quote.id,invitation_id=inv.id)
    if payload.confirm_submission:
        from app.modules.quotations.automation import submit_record
        submit_record(db,quote,None)
    db.commit()
    return {'quotation_id':quote.id,'status':quote.status}
