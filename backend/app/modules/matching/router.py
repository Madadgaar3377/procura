from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RFQ, Invitation, Member, Organization, Outbox, Quotation, RFQItem, RFQMatch, User
from app.modules.matching.service import match_and_invite
from app.response_contracts import InvitationOut, InvitationSummary, MatchResults
from app.search import ListSearch
from app.security import as_utc, audit, current_user, org_member
from app.serializers import record

router=APIRouter(prefix='/matching',tags=['Supplier Matching'])

@router.post('/rfqs/{rfq_id}/dispatch')
def dispatch(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status not in {'published','collecting_quotes'}:raise HTTPException(409,'RFQ not published')
    results=match_and_invite(db,rfq,actor=user,bounded=True)
    audit(db,user,'rfq.match_dispatched',rfq.buyer_org_id,rfq.id,count=results['new_invitations'])
    db.commit()
    return {**results,'invitations':[record(i) for i in results['invitations']], 'reason':'No eligible verified supplier with the required category capability and service city' if not results['eligible_suppliers'] else None}

@router.get('/rfqs/{rfq_id}/results',response_model=MatchResults)
def results(rfq_id:str,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    items={item.id:item.description for item in db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id)).all()}
    matches=[]
    for match,name in db.execute(select(RFQMatch,Organization.name).join(Organization,Organization.id==RFQMatch.supplier_org_id).where(RFQMatch.rfq_id==rfq.id).order_by(RFQMatch.id).limit(limit).offset(offset)).all():
        covered=[reason[5:] for reason in match.reasons if reason.startswith('item:')]
        matches.append({**record(match),'supplier_name':name,'covered_items':[items[item_id] for item_id in covered if item_id in items],
                        'coverage':next((reason[9:] for reason in match.reasons if reason.startswith('coverage:')),'Not recorded')})
    invitation_rows=db.execute(select(Invitation,Organization.name,Quotation.status).join(Organization,Organization.id==Invitation.supplier_org_id).outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(Invitation.rfq_id==rfq.id).order_by(Invitation.id).limit(limit).offset(offset)).all()
    jobs={status:count for status,count in db.execute(select(Outbox.status,func.count()).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id).group_by(Outbox.status)).all()}
    matched=int(db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id==rfq.id)) or 0)
    invited=int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq.id)) or 0)
    if rfq.status=='draft':progress='draft'
    elif rfq.status not in {'published','collecting_quotes'} or as_utc(rfq.response_deadline)<=datetime.now(UTC):progress='closed'
    elif jobs.keys() & {'pending','retry','processing'}:
        progress='matching_in_progress' if matched or jobs.get('processing') else 'matching_queued'
    elif jobs.keys() & {'failed','uncertain'}:progress='matching_failed'
    elif invited:progress='invitations_available'
    elif jobs and set(jobs)=={'processed'}:progress='no_eligible_suppliers'
    else:progress='matching_queued'
    from datetime import timedelta

    from app.modules.notifications.procurement import pending_orgs
    from app.modules.notifications.router import display_delivery_status
    pending=set(pending_orgs(db,rfq)) if progress not in {'draft','closed'} else set()
    deliveries:dict[str,int]={}
    # Aggregate only safe status fields; recipient addresses and provider errors stay private.
    delivery_rows=db.execute(select(Outbox.status,Outbox.delivery_status,func.count()).where(Outbox.event=='notification.delivery',Outbox.payload['context']['rfq_id'].as_string()==rfq.id).group_by(Outbox.status,Outbox.delivery_status)).all()
    from types import SimpleNamespace
    for status,provider,count in delivery_rows:
        label=display_delivery_status(SimpleNamespace(status=status,delivery_status=provider))
        deliveries[label]=deliveries.get(label,0)+count
    recent=db.scalar(select(func.max(Outbox.created_at)).where(Outbox.event=='rfq.deadline.reminder',Outbox.payload['rfq_id'].as_string()==rfq.id))
    available=(recent.replace(tzinfo=UTC) if recent and recent.tzinfo is None else recent)+timedelta(hours=24) if recent else None
    return {'matches':matches, 'invitations':[{**record(i),'supplier_name':name,'quotation_status':quote,'reminder_eligible':i.supplier_org_id in pending} for i,name,quote in invitation_rows], 'progress':progress,
            'pending_invitation_ids':[i.id for i,_,_ in invitation_rows if i.supplier_org_id in pending],
            'matched_suppliers':matched,'invited_suppliers':invited,'pending_suppliers':len(pending),
            'deliveries':deliveries,'reminder_available_at':available.isoformat() if available else None,'limit':limit,'offset':offset}

@router.get('/invitations',response_model=list[InvitationSummary])
def my_invitations(search:ListSearch=Depends(),user:User=Depends(current_user),db:Session=Depends(get_db)):
    q=select(Invitation).join(Member,Invitation.supplier_org_id==Member.organization_id).join(Organization,Organization.id==Member.organization_id).where(Member.user_id==user.id,Organization.is_active.is_(True))
    if search.org_id:
        org_member(db,user,search.org_id)
        q=q.where(Invitation.supplier_org_id==search.org_id)
    q=q.join(RFQ,RFQ.id==Invitation.rfq_id)
    invitations=db.scalars(search.apply(q,RFQ.reference,RFQ.title,Invitation.status,order=(Invitation.created_at.desc(),Invitation.id))).all()
    rfq_ids={i.rfq_id for i in invitations}
    rfqs={r.id:r for r in db.scalars(select(RFQ).where(RFQ.id.in_(rfq_ids))).all()}
    descriptions:dict[str,list[str]]={}
    for rfq_id,description in db.execute(select(RFQItem.rfq_id,RFQItem.description).where(RFQItem.rfq_id.in_(rfq_ids))).all():descriptions.setdefault(rfq_id,[]).append(description)
    return [{**record(i),'rfq_reference':rfqs[i.rfq_id].reference,'title':rfqs[i.rfq_id].title,'item_descriptions':descriptions.get(i.rfq_id,[])} for i in invitations]

@router.post('/invitations/{invitation_id}/{action}',response_model=InvitationOut)
def respond(invitation_id:str,action:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if action not in {'accept','decline'}:raise HTTPException(422,'Action must be accept or decline')
    inv=db.scalar(select(Invitation).where(Invitation.id==invitation_id).with_for_update())
    if not inv:raise HTTPException(404,'Invitation not found')
    org_member(db,user,inv.supplier_org_id,{'owner','manager','supplier'})
    if inv.status!='invited':raise HTTPException(409,'Invitation already processed')
    if action=='accept':
        rfq=db.get(RFQ,inv.rfq_id)
        if not rfq:raise HTTPException(404,'RFQ not found')
        deadline=rfq.response_deadline.replace(tzinfo=UTC) if rfq.response_deadline.tzinfo is None else rfq.response_deadline.astimezone(UTC)
        if rfq.status not in {'published','collecting_quotes'} or deadline<=datetime.now(UTC):
            raise HTTPException(409,'RFQ is closed or past its response deadline')
    inv.status='accepted' if action=='accept' else 'declined'
    audit(db,user,'rfq.invitation_'+action,inv.supplier_org_id,inv.id);db.commit()
    return record(inv)
