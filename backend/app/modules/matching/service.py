"""Set-based matching for verified suppliers under the configured capability policy."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.capabilities.policy import eligibility_predicates
from app.models import (
    RFQ,
    Category,
    Capability,
    Invitation,
    Material,
    Member,
    Notification,
    Organization,
    Outbox,
    RFQItem,
    RFQMatch,
    User,
)


def match_and_invite(db: Session, rfq: RFQ, actor: User | None = None, limit: int | None = None, after_supplier_id: str | None = None, bounded: bool = False):
    """Filter candidates in PostgreSQL, then write matches/invitations in bounded sets.

    Verification, active status, supplier role, configured capability eligibility, and exact
    service-city coverage are mandatory SQL predicates. Nothing in search ranking
    can override them.
    """
    from datetime import datetime, timezone
    from app.security import as_utc
    if rfq.status not in {'published','collecting_quotes'} or as_utc(rfq.response_deadline)<=datetime.now(timezone.utc):
        return {'eligible_suppliers':0,'new_invitations':0,'invitations':[]}
    # Snapshot IDs take precedence; fallback supports pre-migration legacy items.
    requested=db.execute(select(RFQItem,Material).outerjoin(Material,Material.id==RFQItem.material_id).where(RFQItem.rfq_id==rfq.id)).all()
    item_scope={}
    for item,material in requested:
        main=item.main_category_id or (material.main_category_id if material else None)
        sub=item.subcategory_id or (material.subcategory_id if material and not item.main_category_id else None)
        if not main and material:
            leaf=db.get(Category,material.category_id)
            main=leaf.parent_id or leaf.id
            sub=leaf.id if leaf.parent_id else None
        if main:item_scope[item.id]=(main,sub)
    if not item_scope:return {'eligible_suppliers':0,'new_invitations':0,'invitations':[]}
    category_scopes={sub or main for main,sub in item_scope.values()}
    category_parents=dict(db.execute(select(Category.id,Category.parent_id).where(Category.id.in_(category_scopes))).all())
    exact_material_ids = {item.material_id for item, _ in requested if item.material_id}

    batch_size = max(1, min(limit if limit is not None else get_settings().matching_batch_size, 100))
    # Scan a bounded keyset, including ineligible qualification candidates, so
    # a large supplier directory never has to be loaded into worker memory.
    candidate_query = (
        select(Capability.org_id)
        .join(Organization, Organization.id == Capability.org_id)
        .where(
            Capability.category_id.in_(category_scopes),
            *eligibility_predicates(),
            func.lower(func.trim(Capability.city)) == rfq.city.strip().casefold(),
            Organization.is_active.is_(True),
            Organization.is_supplier.is_(True),
            Organization.verified_status == 'verified',
            Organization.id != rfq.buyer_org_id,
        )
    )
    if after_supplier_id:candidate_query=candidate_query.where(Capability.org_id>after_supplier_id)
    candidate_query=candidate_query.distinct().order_by(Capability.org_id)
    if bounded:candidate_query=candidate_query.limit(batch_size+1)
    candidate_ids=list(db.scalars(candidate_query).all())
    has_more=bounded and len(candidate_ids)>batch_size
    if bounded:candidate_ids=candidate_ids[:batch_size]
    rows = db.execute(select(Capability.org_id,Capability.category_id,Capability.qualifications,Capability.material_ids,Organization)
        .join(Organization,Organization.id==Capability.org_id).where(Capability.org_id.in_(candidate_ids),
        Capability.category_id.in_(category_scopes),*eligibility_predicates(),
        func.lower(func.trim(Capability.city))==rfq.city.strip().casefold())).all()
    if has_more:
        cursor=candidate_ids[-1]
        key=f'rfq-matching:{rfq.id}:after:{cursor}'
        if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key==key)):
            db.add(Outbox(event='rfq.published',payload={'rfq_id':rfq.id,'after_supplier_id':cursor},dedupe_key=key))

    selected: dict[str, dict] = {}
    required={value.strip().casefold() for value in (rfq.required_qualifications or []) if value.strip()}
    for org_id, category_id, qualifications, supplier_mat_ids, company in rows:
        if not required.issubset({value.strip().casefold() for value in (qualifications or [])}):continue
        candidate=selected.setdefault(org_id,{'org':company,'covered':set(),'exact_materials':set()})
        for item_id,(main,sub) in item_scope.items():
            # A main-category capability never grants all of its subcategories.
            if category_id==(sub or main) and (not sub or category_parents.get(category_id)==main):
                candidate['covered'].add(item_id)
        if supplier_mat_ids and exact_material_ids:
            matched_mats = set(supplier_mat_ids) & exact_material_ids
            candidate['exact_materials'].update(matched_mats)
    ranked = sorted((candidate for candidate in selected.values() if candidate['covered']), key=lambda candidate: (
        -len(candidate.get('exact_materials', set())), -len(candidate['covered']), candidate['org'].name.casefold(), candidate['org'].id,
    ))
    if not ranked:
        return {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': []}

    org_ids = [candidate['org'].id for candidate in ranked]
    matches = set(db.scalars(select(RFQMatch.supplier_org_id).where(
        RFQMatch.rfq_id == rfq.id, RFQMatch.supplier_org_id.in_(org_ids),
    )).all())
    invited = set(db.scalars(select(Invitation.supplier_org_id).where(
        Invitation.rfq_id == rfq.id, Invitation.supplier_org_id.in_(org_ids),
    )).all())
    member_rows = db.execute(select(Member.organization_id, Member.user_id).join(User,User.id==Member.user_id).where(
        User.is_active.is_(True),
        Member.organization_id.in_(org_ids),
    )).all()
    members_by_org: dict[str, list[str]] = {}
    for org_id, user_id in member_rows:
        members_by_org.setdefault(org_id, []).append(user_id)

    batch_size = max(1, min(limit if limit is not None else get_settings().matching_batch_size, 100))
    invitation_candidates = [candidate for candidate in ranked if candidate['org'].id not in invited][:batch_size]
    new_matches = []
    for candidate in ranked:
        if candidate['org'].id in matches:
            continue
        exact_count = len(candidate.get('exact_materials', set()))
        score = min(100, 60 + 10 * len(candidate['covered']) + (10 if exact_count > 0 else 0))
        reasons = [
            'verified company',
            ('approved exact main category + subcategory capability' if get_settings().require_capability_approval else 'self-declared exact main category + subcategory capability'),
            'delivery city match',
            f"coverage:{len(candidate['covered'])}/{len(item_scope)}",
        ]
        if exact_material_ids:
            reasons.append(f"exact_materials:{exact_count}/{len(exact_material_ids)}")
        reasons.extend([f'item:{item_id}' for item_id in sorted(candidate['covered'])])
        reasons.append('Category eligibility does not confirm technical compliance')
        new_matches.append(RFQMatch(
            rfq_id=rfq.id,
            supplier_org_id=candidate['org'].id,
            score=score,
            reasons=reasons,
            status='eligible' if len(candidate['covered']) == len(item_scope) else 'partial',
        ))
    db.add_all(new_matches)

    invitations = [Invitation(rfq_id=rfq.id, supplier_org_id=candidate['org'].id)
                   for candidate in invitation_candidates]
    db.add_all(invitations)
    if invitations:
        db.flush()
        for invitation in invitations:
            db.add(Outbox(
                event='rfq.invitation',
                payload={'rfq_id': rfq.id, 'supplier_org_id': invitation.supplier_org_id,
                         'invitation_id': invitation.id},
                dedupe_key=f'rfq-invitation:{invitation.id}',
            ))
        recipients = [user_id for invitation in invitations
                      for user_id in members_by_org.get(invitation.supplier_org_id, [])]
        db.add_all(Notification(
            recipient_user_id=user_id,
            event='rfq.invitation',
            message=f'New RFQ: {rfq.reference}',
            status='delivered',
        ) for user_id in recipients)
        rfq.status = 'collecting_quotes'

    # Preserve the legacy worker entry point while the active worker uses keysets.
    if not bounded and len(ranked)-len(invited)>len(invitations):
        key=f'rfq-matching:{rfq.id}:after:{len(invited)+len(invitations)}'
        if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key==key)):
            db.add(Outbox(event='rfq.published',payload={'rfq_id':rfq.id},dedupe_key=key))

    return {
        'eligible_suppliers': len(ranked),
        'new_invitations': len(invitations),
        'invitations': invitations,
    }
