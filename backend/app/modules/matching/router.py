from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RFQ, Invitation, Member, Organization, Outbox, PriceBookItem, Quotation, RFQItem, RFQMatch, Capability, Category, Material, User
from app.modules.matching.service import INTERNAL_REASON_PREFIXES, MATCHED_STATUSES, match_and_invite, match_level
from app.response_contracts import InvitationOut, InvitationSummary, MatchResults, MatchingDiagnosticOut, MatchingRerunOut
from app.search import ListSearch
from app.security import as_utc, audit, current_user, org_member
from app.serializers import record

router=APIRouter(prefix='/matching',tags=['Supplier Matching'])
from app.modules.matching.guest import router as guest_router
router.include_router(guest_router)

@router.get('/admin/rfqs/{rfq_id}/diagnostics',response_model=MatchingDiagnosticOut)
def admin_diagnostics(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Explain classification, Material Master resolution, evidence levels, exclusions, jobs and delivery to admins.

    Read-only: classification is recomputed deterministically (no AI call) and nothing is persisted.
    """
    if not user.is_admin:raise HTTPException(403,'Admin required')
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    from app.modules.matching.service import active_categories, resolve_item_scopes, taxonomy_label
    from app.modules.rfqs.material_matching import classify_material_description
    items=db.execute(select(RFQItem,Material).outerjoin(Material,Material.id==RFQItem.material_id).where(RFQItem.rfq_id==rfq.id,RFQItem.is_active.is_(True)).order_by(RFQItem.created_at,RFQItem.id)).all()
    categories=active_categories(db)
    scopes,material_info=resolve_item_scopes(db,items,categories)
    names={cid:c.name_en for cid,c in categories.items()}

    statuses={status:int(count) for status,count in db.execute(select(RFQMatch.status,func.count()).where(RFQMatch.rfq_id==rfq.id).group_by(RFQMatch.status)).all()}
    inv_map={}
    inv_statuses:dict[str,int]={}
    quote_statuses:dict[str,int]={}
    for inv,quote_status,source in db.execute(select(Invitation,Quotation.status,Quotation.submission_source).outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(Invitation.rfq_id==rfq.id)).all():
        inv_map[inv.supplier_org_id]=(inv.status,quote_status)
        inv_statuses[inv.status]=inv_statuses.get(inv.status,0)+1
        if quote_status:
            key=f'{source or "manual"}:{quote_status}'
            quote_statuses[key]=quote_statuses.get(key,0)+1

    reasons=[];eligible_suppliers=[];excluded_suppliers=[]
    level_totals={'exact material':0,'product':0,'subcategory':0,'main category':0,'ai':0}
    item_levels:dict[str,dict[str,int]]={scope['id']:{} for scope in scopes}
    for match,org in db.execute(select(RFQMatch,Organization).join(Organization,Organization.id==RFQMatch.supplier_org_id).where(RFQMatch.rfq_id==rfq.id).order_by(RFQMatch.score.desc(),Organization.name).limit(300)).all():
        raw=match.reasons or []
        readable=[reason for reason in raw if not reason.startswith(INTERNAL_REASON_PREFIXES)]
        reasons.append({'supplier_org_id':org.id,'supplier_name':org.name,'status':match.status,'reasons':readable})
        if match.status not in {'eligible','partial'}:
            excluded_suppliers.append({'supplier_org_id':org.id,'supplier_name':org.name,'status':match.status,'reasons':readable})
            continue
        levels=match_level(raw)
        for scope in scopes:
            for level in match_level(raw,scope['id']):
                item_levels[scope['id']][level]=item_levels[scope['id']].get(level,0)+1
        if 'exact material' in levels:level_totals['exact material']+=1
        elif {'product alias','product family'}&set(levels):level_totals['product']+=1
        elif 'subcategory' in levels:level_totals['subcategory']+=1
        elif 'main category' in levels:level_totals['main category']+=1
        if 'AI-assisted semantic match' in levels:level_totals['ai']+=1
        inv_status,quote_status=inv_map.get(org.id,(None,None))
        eligible_suppliers.append({'supplier_org_id':org.id,'supplier_name':org.name,'status':match.status,'score':match.score,
            'match_levels':levels,'coverage':next((r[9:] for r in raw if r.startswith('coverage:')),'Not recorded'),
            'invitation_status':inv_status,'quotation_status':quote_status,'ai_assisted':'AI-assisted semantic match' in levels,'reasons':readable})

    item_diagnostics=[]
    for scope in scopes:
        item=scope['item']
        result=classify_material_description(db,item.description,specifications=item.specifications or '',unit=item.unit,ai_enabled=False,categories=categories)
        note=None
        if scope['conflict']:
            note=(f'Material Master places this product family in {scope["family_label"]}; the RFQ line is filed under '
                  f'{taxonomy_label(categories,scope["main"],scope["sub"]) or "no category"}. Matching uses product evidence; '
                  'buyer confirmation is required before changing the line.')
        elif not scope['family'] and not scope['main']:
            note='No Material Master product family or category was resolved; buyer category confirmation is required.'
        item_diagnostics.append({
            'item_id':item.id,'description':item.description,'material_id':item.material_id,
            'main_category_id':scope['main'],'main_category_name':names.get(scope['main']),
            'subcategory_id':scope['sub'],'subcategory_name':names.get(scope['sub']),
            'classified':bool(scope['main']),'matched_supplier_count':sum(item_levels[scope['id']].values()),
            'specifications_excerpt':(item.specifications or '')[:300],
            'material_master_main_category_id':scope['family_main'],'material_master_main_category_name':names.get(scope['family_main']),
            'material_master_subcategory_id':scope['family_sub'],'material_master_subcategory_name':names.get(scope['family_sub']),
            'classification_confidence':result.get('confidence'),'classification_source':result.get('match_source'),
            'classification_needs_confirmation':bool(result.get('needs_confirmation')),
            'classification_conflict':scope['conflict'],'classification_note':note,
            'product_candidates':[material_info[mid][0] for mid,_ in sorted(scope['family'].items(),key=lambda kv:(-kv[1],material_info[kv[0]][0]))[:10]],
            'level_counts':item_levels[scope['id']],
        })

    from types import SimpleNamespace
    from app.modules.notifications.router import display_delivery_status
    delivery_summary:dict[str,int]={}
    deliveries=select(Outbox.status.label('status'),Outbox.delivery_status.label('provider')).where(
        Outbox.event=='notification.delivery',Outbox.payload['context']['rfq_id'].as_string()==rfq.id).subquery()
    for status,provider,count in db.execute(select(deliveries.c.status,deliveries.c.provider,func.count()).group_by(deliveries.c.status,deliveries.c.provider)).all():
        label=display_delivery_status(SimpleNamespace(status=status,delivery_status=provider))
        delivery_summary[label]=delivery_summary.get(label,0)+int(count)
    job_rows=db.scalars(select(Outbox).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id).order_by(Outbox.created_at.desc())).all()
    jobs=[{**record(job)} for job in job_rows]
    duration=next((job.payload.get('matching_duration_ms') for job in job_rows if job.payload.get('matching_duration_ms') is not None),None)
    evaluated=sum(statuses.values())
    # Candidate funnel, counted in SQL over this RFQ's evaluated candidates.
    candidates=select(RFQMatch.supplier_org_id).where(RFQMatch.rfq_id==rfq.id)
    mains={s['main'] for s in scopes if s['main']};subs={s['sub'] for s in scopes if s['sub']}
    discipline=lambda cid:(names.get(cid) or '').split(' - ',1)[0].strip().casefold()
    disciplines={discipline(m) for m in mains}
    discipline_mains={cid for cid,c in categories.items() if not c.parent_id and discipline(cid) in disciplines}
    def holders(category_ids):
        if not category_ids:return 0
        return int(db.scalar(select(func.count(func.distinct(Capability.org_id))).where(
            Capability.org_id.in_(candidates),Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),
            Capability.eligibility_status=='enabled',Capability.category_id.in_(category_ids))) or 0)
    children=lambda parents:{cid for cid,c in categories.items() if c.parent_id in parents}
    matched_total=statuses.get('eligible',0)+statuses.get('partial',0)
    totals=match_level_totals(db,rfq.id)
    funnel={'initial_candidates':evaluated,
            'discipline_qualified':holders(discipline_mains|children(discipline_mains)),
            'main_category_qualified':holders(mains|children(mains)),
            'subcategory_qualified':holders(subs),
            'exact_product_matches':totals['exact_material_matches'],'fuzzy_product_matches':totals['product_description_matches'],
            'category_capability_fallback_matches':totals['subcategory_matches']+totals['main_category_matches'],
            'insufficient_product_evidence':statuses.get('insufficient_evidence',0),
            'location_exclusions':statuses.get('location_mismatch',0)+statuses.get('location_unconfirmed',0),
            'brand_exclusions':statuses.get('brand_mismatch',0),
            'inactive_or_rejected':sum(statuses.get(k,0) for k in ('inactive','rejected','suspended','pending_approval','ineligible','unverified')),
            'final_eligible_suppliers':matched_total,
            'invitations_active':sum(v for k,v in inv_statuses.items() if k!='cancelled'),
            'invitations_cancelled':inv_statuses.get('cancelled',0),
            'automatic_quotations':sum(v for k,v in quote_statuses.items() if k.startswith('automatic')),
            'quotations_submitted':sum(v for k,v in quote_statuses.items() if not k.endswith(':draft'))}
    channel_delivery:dict[str,dict[str,int]]={}
    channel_rows=select(Outbox.payload['channel'].as_string().label('channel'),Outbox.status.label('status'),Outbox.delivery_status.label('provider')).where(
        Outbox.event=='notification.delivery',Outbox.payload['context']['rfq_id'].as_string()==rfq.id).subquery()
    for channel,status,provider,count in db.execute(select(channel_rows.c.channel,channel_rows.c.status,channel_rows.c.provider,func.count())
                                                    .group_by(channel_rows.c.channel,channel_rows.c.status,channel_rows.c.provider)).all():
        label=display_delivery_status(SimpleNamespace(status=status,delivery_status=provider))
        bucket=channel_delivery.setdefault(channel or 'unknown',{})
        bucket[label]=bucket.get(label,0)+int(count)
    reminder_jobs=db.scalars(select(Outbox).where(Outbox.event=='rfq.deadline.reminder',Outbox.payload['rfq_id'].as_string()==rfq.id)
                             .order_by(Outbox.created_at.desc()).limit(20)).all()
    reminders={'requests':int(db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event=='rfq.deadline.reminder',
                   Outbox.payload['rfq_id'].as_string()==rfq.id)) or 0),
               'suppliers_reminded':int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq.id,Invitation.reminder_count>0)) or 0),
               'recent':[{'created_at':job.created_at.isoformat() if job.created_at else None,'status':job.status,
                          'suppliers':len(job.payload.get('invitation_ids') or []) or None} for job in reminder_jobs]}
    failed_reasons=[f"{job.status}: {job.last_error}" for job in job_rows if job.last_error]
    return {
        'rfq_id':rfq.id,'rfq_items':len(items),'classified_items':sum(1 for s in scopes if s['main']),'unclassified_items':sum(1 for s in scopes if not s['main']),
        'active_suppliers_checked':int(db.scalar(select(func.count()).select_from(Organization).where(Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.id!=rfq.buyer_org_id)) or 0),
        'category_candidate_organizations':evaluated,
        'approved_matching_capabilities':statuses.get('eligible',0)+statuses.get('partial',0),
        'exact_matches':level_totals['exact material'],
        'category_matches':level_totals['subcategory']+level_totals['main category'],
        'ai_assisted_candidates':level_totals['ai'],
        'product_matches':level_totals['product'],'subcategory_matches':level_totals['subcategory'],'main_category_matches':level_totals['main category'],
        'matching_duration_ms':duration,
        'rejected_by_location':statuses.get('location_mismatch',0)+statuses.get('coverage_unknown',0),
        'rejected_by_verification_or_eligibility':sum(statuses.get(k,0) for k in ('inactive','unverified','pending_approval','ineligible','rejected','suspended')),
        'matches_created':statuses.get('eligible',0)+statuses.get('partial',0),
        'invitations_created':int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq.id)) or 0),
        'delivery_summary':delivery_summary,'invitation_statuses':inv_statuses,'quotation_statuses':quote_statuses,
        'item_diagnostics':item_diagnostics,'eligible_suppliers':eligible_suppliers,'excluded_suppliers':excluded_suppliers,
        'job_status':jobs,'supplier_reasons':reasons,
        'funnel':funnel,'channel_delivery':channel_delivery,'reminders':reminders,'failed_job_reasons':failed_reasons,
    }

@router.post('/admin/rfqs/{rfq_id}/rerun',response_model=MatchingRerunOut)
def admin_rerun(rfq_id:str,run_now:bool=Query(True,description='Evaluate immediately within the publish time budget; any remainder stays queued.'),
                user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Re-run matching for a published RFQ without recreating it.

    Existing matches are re-evaluated in place, invitations stay unique per supplier
    (uq_invitation), and quotations and supplier responses are never modified.
    """
    if not user.is_admin:raise HTTPException(403,'Admin required')
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    if rfq.status not in {'published','collecting_quotes'}:raise HTTPException(409,'RFQ is not open for matching')
    pending=db.scalar(select(Outbox).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id,Outbox.status.in_({'pending','retry','processing'})).limit(1))
    deduplicated=bool(pending)
    job=pending or Outbox(event='rfq.published',payload={'rfq_id':rfq.id,'requested_by':user.id},dedupe_key=f'rfq-admin-rerun:{rfq.id}:{uuid4().hex}')
    if not pending:db.add(job)
    audit(db,user,'rfq.match_admin_rerun_queued',rfq.buyer_org_id,rfq.id,run_now=run_now,deduplicated=deduplicated)
    db.commit()
    status=job.status
    if run_now:
        from app.config import get_settings
        from app.modules.matching.service import run_matching_job
        try:
            run_matching_job(db.get_bind(),job.id,time_budget=get_settings().matching_publish_time_budget_seconds,restart=True)
        except Exception:
            import logging
            logging.getLogger('uvicorn.error').exception('Admin matching rerun failed rfq_id=%s',rfq.id)
        db.expire_all()
        status=db.scalar(select(Outbox.status).where(Outbox.id==job.id)) or status
    return {'rfq_id':rfq.id,'job_id':job.id,'job_status':status,'deduplicated':deduplicated}

@router.post('/rfqs/{rfq_id}/dispatch')
def dispatch(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.scalar(select(RFQ).where(RFQ.id==rfq_id).with_for_update())
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    if rfq.status not in {'published','collecting_quotes'}:raise HTTPException(409,'RFQ not published')
    # Matching runs once per published RFQ. After its job completed, this returns the
    # stored result; only an administrator rerun evaluates the suppliers again.
    jobs=db.scalars(select(Outbox.status).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id)).all()
    if 'processed' in jobs and not {'pending','retry','processing'}&set(jobs):
        matched=int(db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id==rfq.id,RFQMatch.status.in_(MATCHED_STATUSES))) or 0)
        db.commit()
        return {'eligible_suppliers':matched,'new_invitations':0,'invitations':[],'already_completed':True,
                'reason':None if matched else 'No supplier has product evidence for the requested items and meets the delivery and brand requirements'}
    results=match_and_invite(db,rfq,actor=user,bounded=True)
    audit(db,user,'rfq.match_dispatched',rfq.buyer_org_id,rfq.id,count=results['new_invitations'])
    db.commit()
    return {**results,'invitations':[record(i) for i in results['invitations']], 'reason':'No eligible supplier with the required category capability and service city' if not results['eligible_suppliers'] else None}

def matching_stalled(job):
    """True when a queued/continuing job has not advanced within the configured window."""
    from app.config import get_settings
    if job.status=='processing' and job.lock_until and as_utc(job.lock_until)>datetime.now(UTC):return False
    raw=(job.payload or {}).get('progressed_at')
    try:last=datetime.fromisoformat(raw) if raw else job.created_at
    except ValueError:last=job.created_at
    if job.next_attempt_at and job.status=='retry':last=max(as_utc(last),as_utc(job.next_attempt_at))
    return bool(last) and (datetime.now(UTC)-as_utc(last)).total_seconds()>get_settings().matching_stalled_after_seconds


def match_level_totals(db,rfq_id):
    """Strongest evidence level per matched supplier, counted in SQL (no row transfer)."""
    text_reasons=cast(RFQMatch.reasons,String)
    def has(*markers):return or_(*[text_reasons.like(f'%"{m}%') for m in markers])
    level=case((has('exact_material_item:'),'exact'),(has('alias_item:','family_item:','ai_semantic_item:'),'product'),
               (has('exact_subcategory_item:'),'subcategory'),(has('main_category_item:'),'main'),else_='other')
    rows=dict(db.execute(select(level,func.count()).where(RFQMatch.rfq_id==rfq_id,RFQMatch.status.in_(('eligible','partial'))).group_by(level)).all())
    return {'exact_material_matches':rows.get('exact',0),'product_description_matches':rows.get('product',0),
            'subcategory_matches':rows.get('subcategory',0),'main_category_matches':rows.get('main',0)}


def _like(value:str)->str:
    return '%'+value.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'


def _strength_sql():
    """Same evidence markers as match_level/match_level_totals; display only, not eligibility."""
    text_reasons=cast(RFQMatch.reasons,String)
    def has(*markers):return or_(*[text_reasons.like(f'%"{m}%') for m in markers])
    return case((has('exact_material_item:'),'exact'),(has('alias_item:','family_item:','ai_semantic_item:'),'product'),
                (text_reasons.like('%"broad_only"%'),'broad'),else_='capability')


def _supplier_filters(db,rfq,rfq_items,*,q,match_type,discipline,main_category_id,subcategory_id,location,brand,quotation):
    """Server-side display filters over persisted RFQMatch rows. Matching itself is untouched."""
    from sqlalchemy import and_, exists
    from app.models import ImportedSupplierTrade
    conditions=[]
    supplier=RFQMatch.supplier_org_id
    if q and q.strip():
        conditions.append(Organization.name.ilike(_like(q),escape='\\'))
    if match_type:
        conditions.append(and_(RFQMatch.status.in_(MATCHED_STATUSES),_strength_sql()==match_type))
    text_reasons=cast(RFQMatch.reasons,String)
    def taxonomy(main_ids:set[str],sub_ids:set[str]):
        # A line counts when this supplier covers it ("item:<id>") and the line, or the
        # product-family taxonomy recorded for it, belongs to the requested category.
        parts=[text_reasons.like(f'%={mid}/%') for mid in main_ids]+[text_reasons.like(f'%/{sid}"%') for sid in sub_ids]
        parts+=[text_reasons.like(f'%"item:{item.id}"%') for item in rfq_items
                if (main_ids and item.main_category_id in main_ids) or (sub_ids and item.subcategory_id in sub_ids)]
        return or_(*parts) if parts else RFQMatch.id.is_(None)
    if discipline and discipline.strip():
        key=discipline.strip().casefold()
        mains={cid for cid,name in db.execute(select(Category.id,Category.name_en).where(Category.parent_id.is_(None))).all()
               if (name or '').split(' - ',1)[0].strip().casefold()==key}
        conditions.append(taxonomy(mains,set()))
    if main_category_id:conditions.append(taxonomy({main_category_id},set()))
    if subcategory_id:conditions.append(taxonomy(set(),{subcategory_id}))
    if location and location.strip():
        pattern=_like(location)
        conditions.append(or_(Organization.city.ilike(pattern,escape='\\'),exists().where(
            Capability.org_id==supplier,Capability.confirmed.is_(True),Capability.eligibility_status=='enabled',
            Capability.city.ilike(pattern,escape='\\'))))
    if brand and brand.strip():
        pattern=_like(brand)
        conditions.append(or_(
            exists().where(Capability.org_id==supplier,cast(Capability.brands,String).ilike(pattern,escape='\\')),
            exists().where(PriceBookItem.org_id==supplier,PriceBookItem.active.is_(True),PriceBookItem.brand.ilike(pattern,escape='\\')),
            exists().where(ImportedSupplierTrade.org_id==supplier,ImportedSupplierTrade.raw_brands.ilike(pattern,escape='\\'))))
    if quotation=='received':
        conditions.append(exists().where(Quotation.rfq_id==rfq.id,Quotation.supplier_org_id==supplier,
                                         Quotation.status.in_(('submitted','shortlisted','awarded','rejected'))))
    elif quotation in {'saved_price','manual'}:
        materials={item.material_id for item in rfq_items if item.material_id}
        subs={item.subcategory_id for item in rfq_items if item.subcategory_id}
        scope=[PriceBookItem.material_id.in_(materials)] if materials else []
        if subs:scope.append(PriceBookItem.subcategory_id.in_(subs))
        priced=exists().where(PriceBookItem.org_id==supplier,PriceBookItem.active.is_(True),or_(*scope)) if scope else None
        if quotation=='saved_price':conditions.append(priced if priced is not None else RFQMatch.id.is_(None))
        elif priced is not None:conditions.append(~priced)
    return conditions


def _filter_options(rfq_items,taxonomy_names)->dict:
    """Taxonomy choices for the supplier toolbar, taken from this RFQ's own lines."""
    mains={item.main_category_id for item in rfq_items if item.main_category_id}
    subs={(item.subcategory_id,item.main_category_id) for item in rfq_items if item.subcategory_id}
    name=lambda cid:taxonomy_names.get(cid) or cid  # noqa: E731
    disciplines=sorted({name(mid).split(' - ',1)[0].strip() for mid in mains if taxonomy_names.get(mid)})
    return {'disciplines':disciplines,
            'main_categories':sorted(({'id':mid,'name':name(mid)} for mid in mains),key=lambda row:row['name']),
            'subcategories':sorted(({'id':sid,'name':name(sid),'parent_id':parent} for sid,parent in subs),key=lambda row:row['name'])}


@router.get('/rfqs/{rfq_id}/results',response_model=MatchResults)
def results(rfq_id:str,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),view:str=Query('filtered',pattern='^(filtered|total)$'),
            q:str|None=Query(None,max_length=120),match_type:str|None=Query(None,pattern='^(exact|product|capability|broad)$'),
            discipline:str|None=Query(None,max_length=120),main_category_id:str|None=Query(None,max_length=64),
            subcategory_id:str|None=Query(None,max_length=64),location:str|None=Query(None,max_length=100),
            brand:str|None=Query(None,max_length=100),quotation:str|None=Query(None,pattern='^(received|saved_price|manual)$'),
            sort:str=Query('relevance',pattern='^(relevance|name|name_desc|score)$'),
            invitation_limit:int|None=Query(None,ge=1,le=100),invitation_offset:int|None=Query(None,ge=0),
            user:User=Depends(current_user),db:Session=Depends(get_db)):
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    rfq_items=db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id,RFQItem.is_active.is_(True))).all()
    items={item.id:item.description for item in rfq_items}
    # Filtered view shows final eligible supplier organizations; Total view includes
    # broad category, location, and brand candidates evaluated for this RFQ.
    candidate_statuses=(*MATCHED_STATUSES,'insufficient_evidence','location_mismatch','location_unconfirmed','coverage_unknown','brand_mismatch')
    org_filter=(Organization.is_supplier.is_(True),Organization.is_active.is_(True),Organization.verified_status.not_in({'rejected','suspended'}))
    visible=RFQMatch.status.in_(candidate_statuses if view=='total' else MATCHED_STATUSES)
    display_filters=_supplier_filters(db,rfq,rfq_items,q=q,match_type=match_type,discipline=discipline,
        main_category_id=main_category_id,subcategory_id=subcategory_id,location=location,brand=brand,quotation=quotation)
    ordering={
        'name':(Organization.name,Organization.id),
        'name_desc':(Organization.name.desc(),Organization.id),
        'score':(RFQMatch.score.desc(),Organization.name,Organization.id),
    }.get(sort,(case((RFQMatch.status=='eligible',0),(RFQMatch.status=='partial',1),else_=2),RFQMatch.score.desc(),Organization.name,Organization.id))
    page_where=(RFQMatch.rfq_id==rfq.id,visible,*org_filter,*display_filters)
    match_rows=db.execute(select(RFQMatch,Organization).join(Organization,Organization.id==RFQMatch.supplier_org_id)
        .where(*page_where).order_by(*ordering).limit(limit).offset(offset)).all()
    # Rows matching the current view and filters across every page (pagination text).
    page_total=int(db.scalar(select(func.count()).select_from(RFQMatch).join(Organization,Organization.id==RFQMatch.supplier_org_id)
        .where(*page_where)) or 0)
    inv_limit=invitation_limit or limit
    inv_offset=offset if invitation_offset is None else invitation_offset
    matches=[]
    supplier_ids=[org.id for match,org in match_rows]
    orgs_by_id={org.id:org for _,org in match_rows}
    service_cities={org_id:set() for org_id in supplier_ids}
    if supplier_ids:
        for org_id,city in db.execute(select(Capability.org_id,Capability.city).where(Capability.org_id.in_(supplier_ids),Capability.city!='',Capability.confirmed.is_(True),Capability.accepts_rfqs.is_(True),Capability.eligibility_status=='enabled')).all():
            service_cities[org_id].add(city)
    invitation_by_supplier={}
    if supplier_ids:
        for invitation,quote_status in db.execute(select(Invitation,Quotation.status)
            .outerjoin(Quotation,Quotation.invitation_id==Invitation.id)
            .where(Invitation.rfq_id==rfq.id,Invitation.supplier_org_id.in_(supplier_ids))).all():
            invitation_by_supplier[invitation.supplier_org_id]=(invitation.status,quote_status)
    from app.modules.capabilities.public_details import published_details_batch
    visible_ids=[org.id for _,org in match_rows if org.is_supplier and org.is_active and org.verified_status=='verified']
    public_by_org=published_details_batch(db,visible_ids,orgs_by_id)
    # Matched taxonomy may differ from the buyer's line (product family evidence), so
    # names are resolved for every category recorded on this page's markers too.
    page_taxonomy={}
    for match,_ in match_rows:
        for reason in match.reasons or []:
            if reason.startswith('taxonomy:') and '=' in reason:
                item_id,pair=reason[9:].split('=',1)
                page_taxonomy[(match.id,item_id)]=tuple(value or None for value in pair.split('/',1))
    category_ids={value for item in rfq_items for value in (item.main_category_id,item.subcategory_id) if value}
    category_ids|={value for pair in page_taxonomy.values() for value in pair if value}
    taxonomy_names=dict(db.execute(select(Category.id,Category.name_en).where(Category.id.in_(category_ids))).all()) if category_ids else {}
    material_ids={item.material_id for item in rfq_items if item.material_id}
    priced=set()
    if supplier_ids:
        # Saved private rates exist for a requested material or the matched subcategory;
        # prices themselves stay private to the supplier until a quotation is submitted.
        subcategories={pair[1] for pair in page_taxonomy.values() if pair[1]}|{item.subcategory_id for item in rfq_items if item.subcategory_id}
        conditions=[PriceBookItem.material_id.in_(material_ids)] if material_ids else []
        if subcategories:conditions.append(PriceBookItem.subcategory_id.in_(subcategories))
        if conditions:
            priced=set(db.scalars(select(PriceBookItem.org_id).where(PriceBookItem.org_id.in_(supplier_ids),
                PriceBookItem.active.is_(True),or_(*conditions)).distinct()).all())
    items_by_id={item.id:item for item in rfq_items}
    invitation_rows=db.execute(select(Invitation,Organization.name,Quotation.status).join(Organization,Organization.id==Invitation.supplier_org_id).outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(Invitation.rfq_id==rfq.id,Invitation.status!='cancelled').order_by(Invitation.created_at.desc(),Invitation.id).limit(inv_limit).offset(inv_offset)).all()
    channel_status=invitation_channel_status(db,rfq.id,set(supplier_ids)|{i.supplier_org_id for i,_,_ in invitation_rows})
    for match,org in match_rows:
        covered=[reason[5:] for reason in (match.reasons or []) if reason.startswith('item:')]
        directory_visible=bool(org.is_supplier and org.is_active and org.verified_status=='verified')
        public=public_by_org.get(org.id,{}) if directory_visible else {}
        invitation_status,quotation_status=invitation_by_supplier.get(org.id,(None,None))
        # Internal markers remain persisted for legacy consumers, never display them.
        display_reasons=[reason for reason in (match.reasons or []) if not reason.startswith(INTERNAL_REASON_PREFIXES)]
        levels=match_level(match.reasons) if match.status in {'eligible','partial'} else []
        broad='broad_only' in (match.reasons or [])
        strength=None
        if levels:
            strength=('exact' if 'exact material' in levels else 'product' if {'product alias','product family'}&set(levels)
                      else 'broad' if broad else 'capability')
        matched_items=[]
        for item_id in covered:
            item=items_by_id.get(item_id)
            if not item:continue
            main,sub=page_taxonomy.get((match.id,item_id),(item.main_category_id,item.subcategory_id))
            main_name=taxonomy_names.get(main)
            matched_items.append({'item_id':item_id,'description':item.description,
                'match_level':next(iter(match_level(match.reasons,item_id)),None),
                'discipline':main_name.split(' - ',1)[0] if main_name else None,
                'main_category':main_name,'subcategory':taxonomy_names.get(sub)})
        historical_review=bool(invitation_status and (match.status not in {'eligible','partial'} or 'broad_only' in (match.reasons or [])))
        if historical_review:
            display_reasons.append('Historical invitation retained; current eligibility requires administrative review. No invitation was revoked.')
        matches.append({**record(match),'reasons':display_reasons,'supplier_name':org.name,'covered_items':[items[item_id] for item_id in covered if item_id in items],
            'coverage':next((reason[9:] for reason in (match.reasons or []) if reason.startswith('coverage:')),'Not recorded'),
            'verification_status':org.verified_status,'description':public.get('description'),'website':public.get('website'),
            'business_email':public.get('business_email'),'business_phone':public.get('business_phone'),
            'service_locations':sorted(service_cities.get(org.id,set())),
            'matched_categories':sorted({' / '.join(filter(None,(entry['main_category'],entry['subcategory']))) for entry in matched_items}-{''}),
            'match_strength':strength,'matched_item_count':len(matched_items),'matched_items':matched_items,
            'saved_price_available':org.id in priced,
            'match_levels':levels,'brand_requirement_status':('saved product data — quotation confirmation required'
                if any(reason.startswith('Brand requirement matches approved saved product data:') for reason in display_reasons)
                else 'unverified — confirm requested brands in quotation'),
            'service_coverage_status':('unknown' if 'delivery coverage unconfirmed' in (match.reasons or []) else 'covered') if match.status in {'eligible','partial'} else ('not covered' if match.status=='location_mismatch' else 'unknown'),
            'invitation_review_required':historical_review,
            'invitation_status':invitation_status,'quotation_status':quotation_status,
            # Actual delivery outcome per channel; a recorded invitation is not a delivery.
            'email_status':channel_status.get((org.id,'email'),{}).get('status') if invitation_status else None,
            'sms_status':channel_status.get((org.id,'sms'),{}).get('status') if invitation_status else None,
            'profile_url':f'/suppliers/{org.id}' if directory_visible else None})
    latest_job=db.scalar(select(Outbox).where(Outbox.event=='rfq.published',Outbox.payload['rfq_id'].as_string()==rfq.id).order_by(Outbox.created_at.desc(),Outbox.id.desc()).limit(1))
    job_status=latest_job.status if latest_job else None
    status_counts={status:int(count) for status,count in db.execute(select(RFQMatch.status,func.count()).where(RFQMatch.rfq_id==rfq.id).group_by(RFQMatch.status)).all()}
    matched=status_counts.get('eligible',0)+status_counts.get('partial',0)
    evaluated=sum(status_counts.values())
    total_candidates=int(db.scalar(select(func.count()).select_from(RFQMatch).join(Organization,Organization.id==RFQMatch.supplier_org_id).where(RFQMatch.rfq_id==rfq.id,RFQMatch.status.in_(candidate_statuses),*org_filter)) or 0)
    invited=int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id==rfq.id,Invitation.status!='cancelled')) or 0)
    level_counts=match_level_totals(db,rfq.id)
    if rfq.status=='draft':progress='draft'
    elif rfq.status not in {'published','collecting_quotes'} or as_utc(rfq.response_deadline)<=datetime.now(UTC):progress='closed'
    elif job_status in {'pending','retry','processing'} and matching_stalled(latest_job):progress='matching_failed'
    elif job_status in {'pending','retry','processing'}:
        progress='matching_in_progress' if matched or job_status=='processing' else 'matching_queued'
    elif job_status in {'failed','uncertain'}:progress='matching_failed'
    elif invited:progress='invitations_available'
    elif matched and job_status in {'processed','skipped'}:progress='matches_available'
    elif job_status in {'processed','skipped'}:progress='no_eligible_suppliers'
    else:progress='matching_failed'  # An open RFQ without a durable job cannot make progress.
    from datetime import timedelta
    from app.modules.notifications.router import display_delivery_status
    # Fast indexed SQL pending check using persisted RFQMatch + Invitation + Quotation
    # (never re-runs _match_batch on every results poll).
    submitted_statuses = {'submitted', 'shortlisted', 'awarded', 'rejected'}
    quotations_received = int(db.scalar(
        select(func.count()).select_from(Quotation).where(
            Quotation.rfq_id == rfq.id,
            Quotation.status.in_(submitted_statuses),
        )
    ) or 0)
    # The same persisted query the reminder endpoint enforces (no matching re-run).
    from app.config import get_settings
    from app.modules.notifications.procurement import pending_invitations
    page_ids=[invitation.id for invitation,_,_ in invitation_rows]
    pending_rows=pending_invitations(db,rfq,page_ids) if progress not in {'draft','closed'} and page_ids else []
    pending={invitation.supplier_org_id for invitation in pending_rows}
    if progress not in {'draft','closed'} and invited:
        responded_sub=select(Quotation.supplier_org_id).where(Quotation.rfq_id==rfq.id,Quotation.status!='draft')
        matched_sub=select(RFQMatch.supplier_org_id).where(RFQMatch.rfq_id==rfq.id,RFQMatch.status.in_(MATCHED_STATUSES))
        pending_total=int(db.scalar(select(func.count(Invitation.id)).join(Organization,Organization.id==Invitation.supplier_org_id).where(
            Invitation.rfq_id==rfq.id,Invitation.status.in_({'invited','accepted'}),
            Invitation.supplier_org_id.not_in(responded_sub),Invitation.supplier_org_id.in_(matched_sub),*org_filter)) or 0)
    else:
        pending_total=0
    cooldown=timedelta(hours=get_settings().rfq_reminder_cooldown_hours)
    def next_reminder(invitation):
        last=invitation.last_reminded_at
        if not last:return None
        due=(last.replace(tzinfo=UTC) if last.tzinfo is None else last)+cooldown
        return due if due>datetime.now(UTC) else None
    deliveries:dict[str,int]={}
    # Aggregate only safe status fields; recipient addresses and provider errors stay private.
    delivery_rows=db.execute(select(Outbox.status,Outbox.delivery_status,func.count()).where(Outbox.event=='notification.delivery',Outbox.payload['context']['rfq_id'].as_string()==rfq.id).group_by(Outbox.status,Outbox.delivery_status)).all()
    from types import SimpleNamespace
    for status,provider,count in delivery_rows:
        label=display_delivery_status(SimpleNamespace(status=status,delivery_status=provider))
        deliveries[label]=deliveries.get(label,0)+count
    # Cooldowns are per supplier invitation (next_reminder_at); no RFQ-wide lock.
    available=None
    explanation=None
    if progress=='matching_failed' and latest_job is None:
        explanation='Supplier matching was not queued for this RFQ. An administrator can safely recover its matching job.'
    elif progress=='matching_failed' and job_status in {'pending','retry','processing'}:
        explanation=('Supplier matching has not progressed recently; the background worker may be stopped. '
                     f'{matched} supplier(s) matched so far are retained and matching resumes automatically when the worker runs.')
    elif progress=='matching_failed':
        failure_detail=f' ({latest_job.last_error[:160]})' if latest_job and latest_job.last_error else ''
        explanation=f'Supplier matching failed{failure_detail}. Existing matches and invitations are retained; an administrator can retry the job.'
    elif matched:
        explanation=f'{matched} supplier(s) have product evidence for at least one RFQ item and meet the delivery and brand requirements; technical compliance is confirmed in their quotations.'
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
    if not matched and progress not in {'draft','matching_queued','matching_in_progress'}:
        # Zero results state the real cause and how each line is classified.
        lines='; '.join(f"{item.description} — {' / '.join(filter(None,(taxonomy_names.get(item.main_category_id),taxonomy_names.get(item.subcategory_id)))) or 'unclassified'}"
                        for item in rfq_items[:5])
        common=Counter(reason for reasons in db.scalars(select(RFQMatch.reasons).where(RFQMatch.rfq_id==rfq.id,
                        RFQMatch.status.not_in({'eligible','partial'})).limit(200)) for reason in (reasons or [])
                       if not reason.startswith(INTERNAL_REASON_PREFIXES) and reason!='Category eligibility does not confirm technical compliance').most_common(1)
        explanation=' '.join(filter(None,(explanation,f'RFQ lines: {lines}.' if lines else None,
                                          f'Most common exclusion: {common[0][0]}' if common else None)))
    matching_status=('failed' if progress=='matching_failed' else 'completed' if progress in {'invitations_available','matches_available','no_eligible_suppliers','closed'} else 'processing' if progress=='matching_in_progress' else 'queued')
    # Structured cause for clients; the text is the buyer explanation chosen above.
    failure_code=(None if progress!='matching_failed' else 'no_matching_job' if latest_job is None
                  else 'matching_stalled' if job_status in {'pending','retry','processing'} else 'matching_error')
    failure_reason=explanation if failure_code else None
    rows=[]
    for i,name,quote in invitation_rows:
        due=next_reminder(i)
        email=channel_status.get((i.supplier_org_id,'email'),{});sms=channel_status.get((i.supplier_org_id,'sms'),{})
        rows.append({**record(i),'supplier_name':name,'quotation_status':quote,
                     'invitation_recorded':True,'email_status':email.get('status') or 'not_queued',
                     'email_reason':email.get('reason'),'sms_status':sms.get('status') or 'not_queued',
                     'sms_reason':sms.get('reason'),'supplier_response':i.status,
                     'reminder_eligible':i.supplier_org_id in pending and due is None,
                     'next_reminder_at':due.isoformat() if due else None,'reminder_count':i.reminder_count or 0})
    from app.modules.notifications.procurement import rfq_solicitation_state
    reminders_open,reminder_code,reminder_message=rfq_solicitation_state(rfq)
    return {'matches':matches, 'invitations':rows, 'progress':progress,'matching_status':matching_status,
            'matching_failure_code':failure_code,'matching_failure_reason':failure_reason,
            'page_total':page_total,'invitation_total':invited,'invitation_limit':inv_limit,'invitation_offset':inv_offset,
            'sort':sort,'filter_options':_filter_options(rfq_items,taxonomy_names),
            # Reminder actions are disabled for closed, cancelled and expired RFQs.
            'reminders_enabled':reminders_open,'reminders_disabled_code':reminder_code,
            'reminders_disabled_reason':reminder_message,
            'pending_invitation_ids':[row['id'] for row in rows if row['reminder_eligible']],
            'matched_suppliers':matched,'total_candidates':total_candidates,'invited_suppliers':invited,'pending_suppliers':pending_total,
            'suppliers_evaluated':(int((latest_job.payload or {}).get('evaluated_suppliers') or 0)
                                   if latest_job and job_status in {'pending','retry','processing'} else evaluated),
            'quotations_received':quotations_received,**level_counts,
            # Candidate and exclusion counts are admin diagnostics, not buyer results.
            'excluded_suppliers':0,'excluded_by_status':{},
            'explanation':explanation,'deliveries':deliveries,'reminder_available_at':available.isoformat() if available else None,'limit':limit,'offset':offset}


# Best outcome first: a delivered message outranks a failed duplicate for the same channel.
CHANNEL_RANK=('delivered','accepted','sent','simulated','uncertain','sending','queued','retry_scheduled',
              'soft_bounced','hard_bounced','failed','suppressed','skipped')
SAFE_SKIP_REASONS={'no_contact','invalid_contact','opted_out','suppressed','invalid_recipient','duplicate_suppressed',
                   'invitation_no_longer_eligible','authorization_changed','eligibility_review_required',
                   'rfq_closed_or_expired','superseded_not_eligible','whatsapp_not_configured'}


def invitation_channel_status(db,rfq_id,org_ids):
    """{(org_id, 'email'|'sms'): {'status', 'reason'}} for RFQ invitation deliveries.

    Buyer-safe: statuses and reason codes only, never addresses or provider text.
    WhatsApp is reported with SMS (same mobile destinations).
    """
    if not org_ids:return {}
    from app.modules.notifications.admin_deliveries import delivery_label
    rows=db.execute(select(Outbox.payload['context']['org_id'].as_string(),Outbox.payload['channel'].as_string(),
        Outbox.status,Outbox.delivery_status).where(Outbox.event=='notification.delivery',
        Outbox.payload['context']['rfq_id'].as_string()==rfq_id,
        Outbox.payload['template'].as_string()=='rfq_invitation',
        Outbox.payload['context']['org_id'].as_string().in_(list(org_ids)))).all()
    best={}
    for org_id,channel,status,provider in rows:
        channel='sms' if channel=='whatsapp' else channel
        label=delivery_label(status,provider)
        current=best.get((org_id,channel))
        rank=CHANNEL_RANK.index(label) if label in CHANNEL_RANK else len(CHANNEL_RANK)
        if current is None or rank<current['rank']:
            best[(org_id,channel)]={'status':label,'rank':rank,
                'reason':provider if label in {'skipped','suppressed'} and provider in SAFE_SKIP_REASONS else None}
    return best


@router.get('/rfqs/{rfq_id}/quotations-summary')
def rfq_quotations_summary(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Independent buyer-safe summary of automatic vs manual quotations and preparation progress.

    Never exposes unapproved supplier draft prices or line items to the buyer.
    """
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    submitted_statuses = {'submitted', 'shortlisted', 'awarded', 'rejected'}
    from app.modules.quotations.router import invoice
    from app.models import QuoteLine
    all_quotes = db.execute(
        select(Quotation, Organization.name)
        .join(Organization, Organization.id == Quotation.supplier_org_id)
        .where(Quotation.rfq_id == rfq.id)
        .order_by(Quotation.submitted_at.desc().nullslast(), Quotation.created_at.desc())
    ).all()
    total_items = int(db.scalar(select(func.count()).select_from(RFQItem).where(RFQItem.rfq_id == rfq.id,RFQItem.is_active.is_(True))) or 0)
    submitted_ids = [q.id for q, _ in all_quotes if q.status in submitted_statuses]
    lines_by_quote: dict[str, list] = {qid: [] for qid in submitted_ids}
    if submitted_ids:
        for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id.in_(submitted_ids))).all():
            lines_by_quote[line.quotation_id].append(line)
    automatic_submitted = []
    manual_submitted = []
    preparing_auto_count = 0
    preparing_manual_count = 0
    for q, supplier_name in all_quotes:
        if q.status not in submitted_statuses:
            if q.submission_source in {'automatic', 'automatic_draft'}:
                preparing_auto_count += 1
            else:
                preparing_manual_count += 1
            continue
        q_lines = lines_by_quote.get(q.id, [])
        totals = invoice(db, q, q_lines)
        entry = {
            'id': q.id,
            # Same reference the quotation PDF prints.
            'reference': (q.commercial_terms or {}).get('quotation_reference') or f'QT-{q.id[:8].upper()}',
            'supplier_org_id': q.supplier_org_id,
            'supplier_name': supplier_name,
            'status': q.status,
            'submission_source': q.submission_source,
            'submitted_at': q.submitted_at.isoformat() if q.submitted_at else None,
            'currency': q.currency,
            'price_basis': q.price_basis,
            'covered_items': len(q_lines),
            'total_items': total_items,
            'totals': totals,
        }
        if q.submission_source == 'automatic':
            automatic_submitted.append(entry)
        else:
            manual_submitted.append(entry)
    active_invitations_count = int(db.scalar(
        select(func.count()).select_from(Invitation).where(
            Invitation.rfq_id == rfq.id,
            Invitation.status.in_({'invited', 'accepted'}),
        )
    ) or 0)
    quoted_invitation_ids = {q.invitation_id for q, _ in all_quotes}
    manual_pricing_required_count = max(0, active_invitations_count - len(quoted_invitation_ids))
    return {
        'rfq_id': rfq.id,
        'automatic_quotations': automatic_submitted,
        'manual_quotations': manual_submitted,
        'preparing_automatic_count': preparing_auto_count,
        'preparing_manual_count': preparing_manual_count,
        'manual_pricing_required_count': manual_pricing_required_count,
        'total_submitted': len(automatic_submitted) + len(manual_submitted),
    }


@router.get('/rfqs/{rfq_id}/notifications-status')
def rfq_notifications_status(rfq_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Independent buyer-safe summary of email and SMS notification delivery statuses."""
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    from types import SimpleNamespace
    from app.modules.notifications.router import display_delivery_status
    # The JSON key is rendered once in a subquery. Grouping by the same JSON
    # expression in the outer query bound the key as separate parameters, which
    # PostgreSQL rejects with GroupingError (a 500 in production).
    deliveries = select(
        Outbox.payload['channel'].as_string().label('channel'),
        Outbox.status.label('status'),
        Outbox.delivery_status.label('delivery_status'),
    ).where(
        Outbox.event == 'notification.delivery',
        Outbox.payload['context']['rfq_id'].as_string() == rfq.id,
    ).subquery()
    rows = db.execute(
        select(deliveries.c.channel, deliveries.c.status, deliveries.c.delivery_status, func.count())
        .group_by(deliveries.c.channel, deliveries.c.status, deliveries.c.delivery_status)
    ).all()
    by_status: dict[str, int] = {}
    by_channel: dict[str, dict[str, int]] = {'email': {}, 'sms': {}}
    total = 0
    for channel, status, provider, count in rows:
        label = display_delivery_status(SimpleNamespace(status=status, delivery_status=provider))
        c = int(count or 0)
        by_status[label] = by_status.get(label, 0) + c
        ch = channel if channel in by_channel else 'email'
        by_channel[ch][label] = by_channel[ch].get(label, 0) + c
        total += c
    return {
        'rfq_id': rfq.id,
        'total': total,
        'by_status': by_status,
        'by_channel': by_channel,
    }


# Buyer-facing delivery groups. An invitation record is never shown as delivery.
DELIVERY_GROUPS={'queued':{'queued','sending','retry_scheduled'},'accepted':{'accepted','sent','simulated'},
                 'delivered':{'delivered'},'failed':{'failed','uncertain'},'bounced':{'hard_bounced','soft_bounced'},
                 'skipped':{'skipped','suppressed'}}


def _mask_destination(channel:str,value:str|None)->str|None:
    if not value:return None
    if '@' in value:
        local,_,domain=value.partition('@')
        return f'{local[:1]}***@{domain}'
    digits=value.strip()
    return f'{digits[:4]}•••{digits[-3:]}' if len(digits)>7 else '•••'


@router.get('/rfqs/{rfq_id}/notifications')
def rfq_notification_records(rfq_id:str,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),
                             channel:str|None=Query(None,pattern='^(email|sms)$'),
                             group:str|None=Query(None,pattern='^(queued|accepted|delivered|failed|bounced|skipped)$'),
                             user:User=Depends(current_user),db:Session=Depends(get_db)):
    """Paginated email/SMS delivery records for this RFQ's supplier notices (read-only).

    Destinations are masked and only reason codes are returned; provider responses and
    secrets stay in the admin delivery console.
    """
    rfq=db.get(RFQ,rfq_id)
    if not rfq:raise HTTPException(404,'RFQ not found')
    org_member(db,user,rfq.buyer_org_id,{'owner','manager','buyer'})
    from types import SimpleNamespace
    from app.modules.notifications.router import display_delivery_status
    context=Outbox.payload['context']
    org_ref=context['org_id'].as_string()
    rows=db.execute(select(
        Outbox.id,Outbox.created_at,Outbox.submitted_at,Outbox.status,Outbox.delivery_status,
        Outbox.payload['channel'].as_string(),Outbox.payload['template'].as_string(),
        Outbox.payload['contact_name'].as_string(),Outbox.payload['destination'].as_string(),
        org_ref,context['invitation_id'].as_string(),
    ).where(Outbox.event=='notification.delivery',context['rfq_id'].as_string()==rfq.id,
            Outbox.payload['template'].as_string().in_(('rfq_invitation','deadline_reminder','rfq_revision')))
     .order_by(Outbox.created_at.desc(),Outbox.id)).all()
    org_ids={row[9] for row in rows if row[9]}
    names=dict(db.execute(select(Organization.id,Organization.name).where(Organization.id.in_(org_ids))).all()) if org_ids else {}
    invitation_by_org=dict(db.execute(select(Invitation.supplier_org_id,Invitation.id).where(
        Invitation.rfq_id==rfq.id,Invitation.supplier_org_id.in_(org_ids))).all()) if org_ids else {}
    records=[];counts={key:0 for key in DELIVERY_GROUPS}
    for event_id,created,submitted,status,provider,ch,template,contact,destination,org_id,invitation_id in rows:
        ch='sms' if ch=='whatsapp' else (ch or 'email')
        label=display_delivery_status(SimpleNamespace(status=status,delivery_status=provider))
        bucket=next((key for key,labels in DELIVERY_GROUPS.items() if label in labels),'queued')
        if channel and ch!=channel:continue
        counts[bucket]+=1
        if group and bucket!=group:continue
        reason=(provider if provider in SAFE_SKIP_REASONS or label in DELIVERY_GROUPS['bounced'] else
                'provider_error' if bucket=='failed' else None)
        records.append({'id':event_id,'supplier_org_id':org_id,'supplier_name':names.get(org_id) or '',
            'contact_name':contact or None,'destination':_mask_destination(ch,destination),'channel':ch,
            'template':template,'status':label,'status_group':bucket,'failure_reason':reason,
            'created_at':as_utc(created).isoformat() if created else None,
            'sent_at':as_utc(submitted).isoformat() if submitted else None,
            'invitation_id':invitation_id or invitation_by_org.get(org_id),
            'retryable':bucket in {'failed','bounced'} or label=='uncertain'})
    return {'rfq_id':rfq.id,'total':len(records),'limit':limit,'offset':offset,'counts':counts,
            'records':records[offset:offset+limit]}

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
    for rfq_id,description in db.execute(select(RFQItem.rfq_id,RFQItem.description).where(RFQItem.rfq_id.in_(rfq_ids),RFQItem.is_active.is_(True))).all():descriptions.setdefault(rfq_id,[]).append(description)
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
