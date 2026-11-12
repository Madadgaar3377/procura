from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RFQ, Invitation, Member, Organization, Outbox, Quotation, RFQItem, RFQMatch, Capability, Category, Material, User
from app.modules.matching.service import match_and_invite, capability_scope
from app.response_contracts import InvitationOut, InvitationSummary, MatchResults, MatchingDiagnosticOut, MatchingRerunOut
from app.search import ListSearch
from app.security import as_utc, audit, current_user, org_member
from app.serializers import record

router=APIRouter(prefix='/matching',tags=['Supplier Matching'])

@router.get('/admin/rfqs/{rfq_id}/diagnostics',response_model=MatchingDiagnosticOut)
def admin_diagnostics(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Explain persisted match outcomes and queue state to authorized administrators."""
    if not user.is_admin:raise HTTPException(403,'Admin required')
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    items=db.execute(select(RFQItem,Material).outerjoin(Material,Material.id==RFQItem.material_id).where(RFQItem.rfq_id==rfq.id)).all()
    scopes=set()
    for item,material in items:
        main=item.main_category_id or (material.main_category_id if material else None)
        sub=item.subcategory_id or (material.subcategory_id if material else None)
        if not main and material:
            leaf=db.get(Category,material.category_id)
            if leaf:main=leaf.parent_id or leaf.id;sub=leaf.id if leaf.parent_id else None
        if main:scopes.update(x for x in (main,sub) if x)
    active_checked=int(db.scalar(select(func.count()).select_from(Organization).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.id!=rfq.buyer_org_id)) or 0)
    sub_ids={item.subcategory_id or (material.subcategory_id if material else None) for item,material in items}
    sub_ids.discard(None)
    relevant=capability_scope(db,{item.material_id for item,_ in items if item.material_id},sub_ids)
    candidate_query=select(Capability.org_id).join(Organization,Organization.id==Capability.org_id).where(
        relevant,Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.id!=rfq.buyer_org_id).distinct()
    candidate_count=int(db.scalar(select(func.count()).select_from(candidate_query.subquery())) or 0)
    capabilities=int(db.scalar(select(func.count()).select_from(Capability).where(
        Capability.org_id.in_(candidate_query),relevant,Capability.confirmed.is_(True),
        Capability.accepts_rfqs.is_(True),Capability.eligibility_status=='enabled')) or 0)
    statuses={status:int(count) for status,count in db.execute(select(RFQMatch.status,func.count()).where(RFQMatch.rfq_id==rfq.id).group_by(RFQMatch.status)).all()}
    reasons=[]
    recorded=set()
    for match,org in db.execute(select(RFQMatch,Organization).join(Organization,Organization.id==RFQMatch.supplier_org_id).where(RFQMatch.rfq_id==rfq.id).order_by(Organization.name).limit(100)).all():
        readable=[reason for reason in (match.reasons or []) if not reason.startswith(
            ('item:','coverage:','exact_materials:','exact_subcategory_item:','main_category_item:'))]
        reasons.append({'supplier_org_id':org.id,'supplier_name':org.name,'status':match.status,'reasons':readable})
        recorded.add(org.id)
    for org in db.scalars(select(Organization).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.id!=rfq.buyer_org_id,Organization.id.not_in(candidate_query),Organization.id.not_in(recorded)).order_by(Organization.name).limit(100)).all():
        if org.id not in recorded:
            reasons.append({'supplier_org_id':org.id,'supplier_name':org.name,'status':'no_capability','reasons':['No supplier capability found for the RFQ category or subcategory.']})
    jobs=[{**record(job)} for job in db.scalars(select(Outbox).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id).order_by(Outbox.created_at.desc())).all()]
    return {'rfq_id':rfq.id,'rfq_items':len(items),'active_suppliers_checked':active_checked,'category_candidate_organizations':candidate_count,'approved_matching_capabilities':capabilities,'rejected_by_location':statuses.get('location_mismatch',0),'rejected_by_verification_or_eligibility':sum(statuses.get(k,0) for k in ('inactive','unverified','pending_approval','ineligible')),'matches_created':statuses.get('eligible',0)+statuses.get('partial',0),'invitations_created':int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq.id)) or 0),'job_status':jobs,'supplier_reasons':reasons}

@router.post('/admin/rfqs/{rfq_id}/rerun',response_model=MatchingRerunOut)
def admin_rerun(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if not user.is_admin:raise HTTPException(403,'Admin required')
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    if rfq.status not in {'published','collecting_quotes'}:raise HTTPException(409,'RFQ is not open for matching')
    pending=db.scalar(select(Outbox).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id,Outbox.status.in_({'pending','retry','processing'})).limit(1))
    if pending:
        return {'rfq_id':rfq.id,'job_id':pending.id,'job_status':pending.status,'deduplicated':True}
    job=Outbox(event='rfq.published',payload={'rfq_id':rfq.id,'requested_by':user.id},dedupe_key=f'rfq-admin-rerun:{rfq.id}:{uuid4().hex}')
    db.add(job)
    audit(db,user,'rfq.match_admin_rerun_queued',rfq.buyer_org_id,rfq.id)
    db.commit()
    return {'rfq_id':rfq.id,'job_id':job.id,'job_status':'pending','deduplicated':False}

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
    rfq_items=db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id)).all()
    items={item.id:item.description for item in rfq_items}
    match_rows=db.execute(select(RFQMatch,Organization).join(Organization,Organization.id==RFQMatch.supplier_org_id)
        .where(RFQMatch.rfq_id==rfq.id).order_by(RFQMatch.score.desc(),Organization.name,Organization.id)
        .limit(limit).offset(offset)).all()
    matches=[]
    supplier_ids=[org.id for match,org in match_rows]
    service_cities={org_id:set() for org_id in supplier_ids}
    if supplier_ids:
        for org_id,city in db.execute(select(Capability.org_id,Capability.city).where(Capability.org_id.in_(supplier_ids),Capability.city!='',Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),Capability.eligibility_status=='enabled')).all():
            service_cities[org_id].add(city)
    invitation_by_supplier={}
    for invitation,org_name,quote_status in db.execute(select(Invitation,Organization.name,Quotation.status)
        .outerjoin(Organization,Organization.id==Invitation.supplier_org_id)
        .outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(Invitation.rfq_id==rfq.id)).all():
        invitation_by_supplier[invitation.supplier_org_id]=(invitation.status,quote_status)
    from app.modules.capabilities.public_details import published_details
    taxonomy_names=dict(db.execute(select(Category.id,Category.name_en).where(Category.id.in_(
        {value for item in rfq_items for value in (item.main_category_id,item.subcategory_id) if value}))).all())
    for match,org in match_rows:
        covered=[reason[5:] for reason in (match.reasons or []) if reason.startswith('item:')]
        public=published_details(db,org.id)
        invitation_status,quotation_status=invitation_by_supplier.get(org.id,(None,None))
        # Internal markers remain persisted for legacy consumers, never display them.
        internal=('item:','coverage:','exact_materials:','exact_subcategory_item:','main_category_item:')
        display_reasons=[reason for reason in (match.reasons or []) if not reason.startswith(internal)]
        approved_labels=[reason for reason in display_reasons if reason.startswith('Approved exact')]
        levels=sorted({'exact material' if 'exact material' in label.lower() else 'exact subcategory' for label in approved_labels})
        historical_review=bool(invitation_status and match.status not in {'eligible','partial'})
        if historical_review:
            display_reasons.append('Historical invitation retained; current eligibility requires administrative review. No invitation was revoked.')
        matches.append({**record(match),'reasons':display_reasons,'supplier_name':org.name,'covered_items':[items[item_id] for item_id in covered if item_id in items],
            'coverage':next((reason[9:] for reason in (match.reasons or []) if reason.startswith('coverage:')),'Not recorded'),
            'verification_status':org.verified_status,'description':public.get('description'),'website':public.get('website'),
            'business_email':public.get('business_email'),'business_phone':public.get('business_phone'),
            'service_locations':sorted(service_cities.get(org.id,set())),
            'matched_categories':sorted({taxonomy_names.get(item.subcategory_id) or taxonomy_names.get(item.main_category_id,'') for item in rfq_items if item.id in covered}),
            'match_levels':levels,'brand_requirement_status':('saved product data — quotation confirmation required'
                if any(reason.startswith('Brand requirement matches approved saved product data:') for reason in display_reasons)
                else 'unverified — confirm requested brands in quotation'),
            'service_coverage_status':'covered' if match.status in {'eligible','partial'} else ('not covered' if match.status=='location_mismatch' else 'unknown'),
            'invitation_review_required':historical_review,
            'invitation_status':invitation_status,'quotation_status':quotation_status,'profile_url':f'/suppliers/{org.id}'})
    invitation_rows=db.execute(select(Invitation,Organization.name,Quotation.status).join(Organization,Organization.id==Invitation.supplier_org_id).outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(Invitation.rfq_id==rfq.id).order_by(Invitation.id).limit(limit).offset(offset)).all()
    jobs={status:count for status,count in db.execute(select(Outbox.status,func.count()).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id).group_by(Outbox.status)).all()}
    matched=int(db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id==rfq.id,RFQMatch.status.in_({'eligible','partial'}))) or 0)
    evaluated=int(db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id==rfq.id)) or 0)
    status_counts={status:count for status,count in db.execute(select(RFQMatch.status,func.count()).where(RFQMatch.rfq_id==rfq.id).group_by(RFQMatch.status)).all()}
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
    explanation=None
    if matched:
        explanation=f'{matched} supplier(s) qualify based on approved category capability and configured eligibility; technical compliance remains subject to quotation review.'
    elif status_counts.get('location_mismatch'):
        explanation=f"{status_counts['location_mismatch']} supplier(s) match the requested category but do not cover delivery city {rfq.city}."
    elif status_counts.get('pending_approval'):
        explanation=f"{status_counts['pending_approval']} supplier(s) have a matching category pending approval."
    elif status_counts.get('inactive'):
        explanation=f"{status_counts['inactive']} supplier(s) have matching capabilities but are inactive or not marked as suppliers."
    elif status_counts.get('unverified'):
        explanation=f"{status_counts['unverified']} supplier(s) have matching capabilities but require company verification."
    elif status_counts.get('ineligible'):
        explanation=f"{status_counts['ineligible']} supplier(s) have matching capabilities but do not meet other RFQ eligibility requirements."
    elif progress in {'matching_queued','matching_in_progress'}:
        explanation='Supplier matching is queued or processing. Results will refresh automatically.'
    elif evaluated:
        explanation='No evaluated supplier currently satisfies the RFQ eligibility requirements.'
    elif progress=='no_eligible_suppliers':
        explanation='No active supplier has an approved capability for the selected category and subcategory.'
    return {'matches':matches, 'invitations':[{**record(i),'supplier_name':name,'quotation_status':quote,'reminder_eligible':i.supplier_org_id in pending} for i,name,quote in invitation_rows], 'progress':progress,
            'pending_invitation_ids':[i.id for i,_,_ in invitation_rows if i.supplier_org_id in pending],
            'matched_suppliers':matched,'invited_suppliers':invited,'pending_suppliers':len(pending),'suppliers_evaluated':evaluated,
            'explanation':explanation,'deliveries':deliveries,'reminder_available_at':available.isoformat() if available else None,'limit':limit,'offset':offset}

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
