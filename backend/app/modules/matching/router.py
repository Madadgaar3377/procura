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
from app.modules.matching.guest import router as guest_router
router.include_router(guest_router)

@router.get('/admin/rfqs/{rfq_id}/diagnostics',response_model=MatchingDiagnosticOut)
def admin_diagnostics(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Explain persisted match outcomes, item classification, AI candidates, exclusions, and delivery state to admins."""
    if not user.is_admin:raise HTTPException(403,'Admin required')
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    items=db.execute(select(RFQItem,Material).outerjoin(Material,Material.id==RFQItem.material_id).where(RFQItem.rfq_id==rfq.id).order_by(RFQItem.created_at,RFQItem.id)).all()
    all_cat_ids={cid for item,mat in items for cid in (item.main_category_id,item.subcategory_id,mat.main_category_id if mat else None,mat.subcategory_id if mat else None,mat.category_id if mat else None) if cid}
    cats={c.id:c for c in db.scalars(select(Category).where(Category.id.in_(all_cat_ids))).all()} if all_cat_ids else {}
    scopes=set()
    main_ids=set()
    sub_ids=set()
    classified_count=0
    unclassified_count=0
    item_meta=[]
    for item,material in items:
        main=item.main_category_id or (material.main_category_id if material else None)
        sub=item.subcategory_id or (material.subcategory_id if material else None)
        if not main and material:
            leaf=cats.get(material.category_id) or db.get(Category,material.category_id)
            if leaf:
                main=leaf.parent_id or leaf.id
                sub=leaf.id if leaf.parent_id else None
        is_classified=bool(main)
        if is_classified:
            classified_count+=1
            main_ids.add(main)
            if sub:sub_ids.add(sub)
            scopes.update(x for x in (main,sub) if x)
        else:
            unclassified_count+=1
        item_meta.append((item,main,sub,is_classified))

    active_checked=int(db.scalar(select(func.count()).select_from(Organization).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.id!=rfq.buyer_org_id)) or 0)
    from app.modules.rfqs.material_matching import extract_keywords
    all_kw={kw for item,_ in items for kw in extract_keywords(f'{item.description} {item.specifications or ""}')}
    relevant=capability_scope(db,{item.material_id for item,_ in items if item.material_id},sub_ids,main_ids=main_ids,keywords=all_kw)
    candidate_query=select(Capability.org_id).join(Organization,Organization.id==Capability.org_id).where(
        relevant,Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.id!=rfq.buyer_org_id).distinct()
    candidate_count=int(db.scalar(select(func.count()).select_from(candidate_query.subquery())) or 0)
    capabilities=int(db.scalar(select(func.count()).select_from(Capability).where(
        Capability.org_id.in_(candidate_query),relevant,Capability.confirmed.is_(True),
        Capability.accepts_rfqs.is_(True),Capability.eligibility_status=='enabled')) or 0)
    statuses={status:int(count) for status,count in db.execute(select(RFQMatch.status,func.count()).where(RFQMatch.rfq_id==rfq.id).group_by(RFQMatch.status)).all()}

    inv_map={}
    inv_statuses:dict[str,int]={}
    for inv,quote_status in db.execute(select(Invitation,Quotation.status).outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(Invitation.rfq_id==rfq.id)).all():
        inv_map[inv.supplier_org_id]=(inv.status,quote_status)
        inv_statuses[inv.status]=inv_statuses.get(inv.status,0)+1

    internal=('item:','coverage:','exact_materials:','exact_subcategory_item:','main_category_item:','keyword_item:','ai_semantic_item:')
    reasons=[]
    eligible_suppliers=[]
    excluded_suppliers=[]
    recorded=set()
    exact_matches=0
    category_matches=0
    ai_assisted_candidates=0
    item_match_counts:dict[str,int]={item.id:0 for item,_ in items}

    for match,org in db.execute(select(RFQMatch,Organization).join(Organization,Organization.id==RFQMatch.supplier_org_id).where(RFQMatch.rfq_id==rfq.id).order_by(RFQMatch.score.desc(),Organization.name).limit(200)).all():
        raw_reasons=match.reasons or []
        readable=[reason for reason in raw_reasons if not reason.startswith(internal)]
        reasons.append({'supplier_org_id':org.id,'supplier_name':org.name,'status':match.status,'reasons':readable})
        recorded.add(org.id)
        if match.status in {'eligible','partial'}:
            covered_ids=[r[5:] for r in raw_reasons if r.startswith('item:')]
            for iid in covered_ids:
                if iid in item_match_counts:item_match_counts[iid]+=1
            has_exact=any('exact material' in r.lower() for r in readable if r.startswith('Approved '))
            has_ai=any(r.startswith('ai_semantic_item:') for r in raw_reasons)
            has_cat=any(r.startswith(('exact_subcategory_item:','main_category_item:','keyword_item:')) for r in raw_reasons) or (not has_exact and not has_ai)
            if has_exact:exact_matches+=1
            if has_cat:category_matches+=1
            if has_ai:ai_assisted_candidates+=1
            levels=[]
            if has_exact:levels.append('exact material')
            if any(r.startswith('exact_subcategory_item:') for r in raw_reasons):levels.append('exact subcategory')
            if any(r.startswith('keyword_item:') for r in raw_reasons):levels.append('keyword/catalog match')
            if any(r.startswith('main_category_item:') for r in raw_reasons):levels.append('main category')
            if has_ai:levels.append('AI-assisted semantic match')
            inv_status,quote_status=inv_map.get(org.id,(None,None))
            eligible_suppliers.append({
                'supplier_org_id':org.id,'supplier_name':org.name,'status':match.status,'score':match.score,
                'match_levels':levels,'coverage':next((r[9:] for r in raw_reasons if r.startswith('coverage:')),'Not recorded'),
                'invitation_status':inv_status,'quotation_status':quote_status,'ai_assisted':has_ai,'reasons':readable,
            })
        else:
            excluded_suppliers.append({'supplier_org_id':org.id,'supplier_name':org.name,'status':match.status,'reasons':readable})

    for org in db.scalars(select(Organization).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.id!=rfq.buyer_org_id,Organization.id.not_in(candidate_query),Organization.id.not_in(recorded)).order_by(Organization.name).limit(100)).all():
        if org.id not in recorded:
            entry={'supplier_org_id':org.id,'supplier_name':org.name,'status':'no_capability','reasons':['No supplier capability, keyword, or catalog match found for the RFQ items.']}
            reasons.append(entry)
            excluded_suppliers.append(entry)

    item_diagnostics=[{
        'item_id':item.id,'description':item.description,'material_id':item.material_id,
        'main_category_id':main,'main_category_name':cats[main].name_en if main in cats else None,
        'subcategory_id':sub,'subcategory_name':cats[sub].name_en if sub in cats else None,
        'classified':is_classified,'matched_supplier_count':item_match_counts.get(item.id,0),
    } for item,main,sub,is_classified in item_meta]

    from types import SimpleNamespace
    from app.modules.notifications.router import display_delivery_status
    delivery_summary:dict[str,int]={}
    for status,provider,count in db.execute(select(Outbox.status,Outbox.delivery_status,func.count()).where(Outbox.event=='notification.delivery',Outbox.payload['context']['rfq_id'].as_string()==rfq.id).group_by(Outbox.status,Outbox.delivery_status)).all():
        label=display_delivery_status(SimpleNamespace(status=status,delivery_status=provider))
        delivery_summary[label]=delivery_summary.get(label,0)+int(count)

    jobs=[{**record(job)} for job in db.scalars(select(Outbox).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id).order_by(Outbox.created_at.desc())).all()]
    return {
        'rfq_id':rfq.id,'rfq_items':len(items),'classified_items':classified_count,'unclassified_items':unclassified_count,
        'active_suppliers_checked':active_checked,'category_candidate_organizations':candidate_count,
        'approved_matching_capabilities':capabilities,'exact_matches':exact_matches,'category_matches':category_matches,
        'ai_assisted_candidates':ai_assisted_candidates,
        'rejected_by_location':statuses.get('location_mismatch',0)+statuses.get('coverage_unknown',0),
        'rejected_by_verification_or_eligibility':sum(statuses.get(k,0) for k in ('inactive','unverified','pending_approval','ineligible','rejected','suspended')),
        'matches_created':statuses.get('eligible',0)+statuses.get('partial',0),
        'invitations_created':int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq.id)) or 0),
        'delivery_summary':delivery_summary,'invitation_statuses':inv_statuses,
        'item_diagnostics':item_diagnostics,'eligible_suppliers':eligible_suppliers,'excluded_suppliers':excluded_suppliers,
        'job_status':jobs,'supplier_reasons':reasons,
    }

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
    return {**results,'invitations':[record(i) for i in results['invitations']], 'reason':'No eligible supplier with the required category capability and service city' if not results['eligible_suppliers'] else None}

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
        internal=('item:','coverage:','exact_materials:','exact_subcategory_item:','main_category_item:','keyword_item:','ai_semantic_item:')
        display_reasons=[reason for reason in (match.reasons or []) if not reason.startswith(internal)]
        approved_labels=[reason for reason in display_reasons if reason.startswith('Approved ')]
        levels=sorted({
            'exact material' if 'exact material' in label.lower()
            else ('exact subcategory' if 'exact subcategory' in label.lower()
                  else ('keyword/catalog match' if 'keyword/catalog' in label.lower()
                        else ('AI-assisted semantic match' if 'ai-assisted' in label.lower() else 'main category')))
            for label in approved_labels
        })
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
    latest_job=db.scalar(select(Outbox).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id).order_by(Outbox.created_at.desc(),Outbox.id.desc()).limit(1))
    job_status=latest_job.status if latest_job else None
    matched=int(db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id==rfq.id,RFQMatch.status.in_({'eligible','partial'}))) or 0)
    evaluated=int(db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id==rfq.id)) or 0)
    status_counts={status:count for status,count in db.execute(select(RFQMatch.status,func.count()).where(RFQMatch.rfq_id==rfq.id).group_by(RFQMatch.status)).all()}
    invited=int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq.id)) or 0)
    if rfq.status=='draft':progress='draft'
    elif rfq.status not in {'published','collecting_quotes'} or as_utc(rfq.response_deadline)<=datetime.now(UTC):progress='closed'
    elif job_status in {'pending','retry','processing'}:
        progress='matching_in_progress' if matched or job_status=='processing' else 'matching_queued'
    elif job_status in {'failed','uncertain'}:progress='matching_failed'
    elif invited:progress='invitations_available'
    elif job_status=='processed':progress='no_eligible_suppliers'
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
    if progress=='matching_failed':
        explanation='Supplier matching failed. Existing matches and invitations are retained; an administrator can retry the job.'
    elif matched:
        explanation=f'{matched} supplier(s) qualify based on approved category capability and configured eligibility; technical compliance remains subject to quotation review.'
    elif status_counts.get('location_mismatch'):
        explanation=f"{status_counts['location_mismatch']} supplier(s) match the requested category but do not cover delivery city {rfq.city}."
    elif status_counts.get('pending_approval'):
        explanation=f"{status_counts['pending_approval']} supplier(s) have a matching category pending approval."
    elif status_counts.get('inactive'):
        explanation=f"{status_counts['inactive']} supplier(s) have matching capabilities but are inactive or not marked as suppliers."
    elif status_counts.get('rejected') or status_counts.get('suspended'):
        excluded_count = status_counts.get('rejected', 0) + status_counts.get('suspended', 0)
        explanation=f"{excluded_count} supplier(s) have matching capabilities but are rejected or suspended."
    elif status_counts.get('unverified'):
        explanation=f"{status_counts['unverified']} historical supplier result(s) require matching refresh; company verification is not required for RFQ invitations."
    elif status_counts.get('ineligible'):
        explanation=f"{status_counts['ineligible']} supplier(s) have matching capabilities but do not meet other RFQ eligibility requirements."
    elif progress in {'matching_queued','matching_in_progress'}:
        explanation='Supplier matching is queued or processing. Results will refresh automatically.'
    elif evaluated:
        explanation='No evaluated supplier currently satisfies the RFQ eligibility requirements.'
    elif progress=='no_eligible_suppliers':
        explanation='No active supplier has an approved capability for the selected category and subcategory.'
    matching_status=('failed' if progress=='matching_failed' else 'completed' if progress in {'invitations_available','no_eligible_suppliers','closed'} else 'processing' if progress=='matching_in_progress' else 'queued')
    return {'matches':matches, 'invitations':[{**record(i),'supplier_name':name,'quotation_status':quote,'reminder_eligible':i.supplier_org_id in pending} for i,name,quote in invitation_rows], 'progress':progress,'matching_status':matching_status,
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
    db.add(Outbox(
        event='rfq.invitation.responded',
        payload={'invitation_id':inv.id,'rfq_id':inv.rfq_id,'supplier_org_id':inv.supplier_org_id,'action':action},
        dedupe_key=f'rfq-invitation-responded:{inv.id}:{action}',
    ))
    audit(db,user,'rfq.invitation_'+action,inv.supplier_org_id,inv.id);db.commit()
    return record(inv)
