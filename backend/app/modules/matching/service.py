"""Set-based matching for verified suppliers under the configured capability policy."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
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
from app.security import as_utc

NATIONWIDE_KEYWORDS = {
    '', 'all', 'all cities', 'nationwide', 'kingdom-wide', 'ksa', 'saudi arabia',
    'المملكة', 'جميع المدن', 'كل المدن', 'كافة المدن'
}


def match_and_invite(
    db: Session,
    rfq: RFQ,
    actor: User | None = None,
    limit: int | None = None,
    after_supplier_id: str | None = None,
    bounded: bool = False,
) -> dict:
    """Filter candidates in PostgreSQL, evaluate eligibility, and write matches/invitations.

    Verification, active status, supplier role, configured capability eligibility, and
    service-city coverage are evaluated systematically. Ineligible suppliers who matched
    the category are recorded with diagnostic reasons so administrators have complete visibility.
    """
    if rfq.status not in {'published', 'collecting_quotes'} or as_utc(rfq.response_deadline) <= datetime.now(timezone.utc):
        return {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [], 'evaluated_suppliers': 0}

    # Snapshot IDs take precedence; fallback supports pre-migration legacy items.
    requested = db.execute(
        select(RFQItem, Material)
        .outerjoin(Material, Material.id == RFQItem.material_id)
        .where(RFQItem.rfq_id == rfq.id)
    ).all()

    item_scope = {}
    for item, material in requested:
        main = item.main_category_id or (material.main_category_id if material else None)
        sub = item.subcategory_id or (material.subcategory_id if material and not item.main_category_id else None)
        if not main and material:
            leaf = db.get(Category, material.category_id)
            if leaf:
                main = leaf.parent_id or leaf.id
                sub = leaf.id if leaf.parent_id else None
        if main:
            item_scope[item.id] = (main, sub)

    if not item_scope:
        return {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [], 'evaluated_suppliers': 0}

    category_scopes = {category_id for main, sub in item_scope.values() for category_id in (main, sub) if category_id}
    category_parents = dict(
        db.execute(select(Category.id, Category.parent_id).where(Category.id.in_(category_scopes))).all()
    )
    exact_material_ids = {item.material_id for item, _ in requested if item.material_id}

    batch_size = max(1, min(limit if limit is not None else get_settings().matching_batch_size, 100))

    # Phase 3 & 8: Scan candidate supplier organizations offering the required category scope.
    candidate_query = (
        select(Capability.org_id)
        .join(Organization, Organization.id == Capability.org_id)
        .where(
            Capability.category_id.in_(category_scopes),
            Organization.id != rfq.buyer_org_id,
        )
    )
    if after_supplier_id:
        candidate_query = candidate_query.where(Capability.org_id > after_supplier_id)
    candidate_query = candidate_query.distinct().order_by(Capability.org_id)
    if bounded:
        candidate_query = candidate_query.limit(batch_size + 1)
    candidate_ids = list(db.scalars(candidate_query).all())
    has_more = bounded and len(candidate_ids) > batch_size
    if bounded:
        candidate_ids = candidate_ids[:batch_size]

    if not candidate_ids:
        return {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [], 'evaluated_suppliers': 0}

    # Fetch supplier capabilities and organizations
    rows = db.execute(
        select(Capability, Organization)
        .join(Organization, Organization.id == Capability.org_id)
        .where(
            Capability.org_id.in_(candidate_ids),
            Capability.category_id.in_(category_scopes),
        )
    ).all()

    if has_more:
        cursor = candidate_ids[-1]
        key = f'rfq-matching:{rfq.id}:after:{cursor}'
        if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
            db.add(Outbox(event='rfq.published', payload={'rfq_id': rfq.id, 'after_supplier_id': cursor}, dedupe_key=key))

    suppliers_caps: dict[str, list[Capability]] = {}
    suppliers_org: dict[str, Organization] = {}
    for cap, org in rows:
        suppliers_caps.setdefault(org.id, []).append(cap)
        suppliers_org[org.id] = org

    rfq_city_norm = (rfq.city or '').strip().casefold()
    required = {value.strip().casefold() for value in (rfq.required_qualifications or []) if value.strip()}
    # RFQ invitations require an explicitly approved capability, regardless of
    # the softer discovery-directory setting.
    require_approval = True

    eligible_candidates: dict[str, dict] = {}
    ineligible_diagnostics: dict[str, dict] = {}

    for org_id in candidate_ids:
        org = suppliers_org.get(org_id)
        if not org:
            continue
        caps = suppliers_caps.get(org_id, [])

        # Check A: Active supplier organization
        if not org.is_active or not org.is_supplier:
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'inactive',
                'reasons': ['Supplier organization is inactive or not marked as supplier.'],
                'covered': set(),
            }
            continue

        # Check B: Verification status
        if org.verified_status != 'verified':
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'unverified',
                'reasons': [f'Company verification required (current status: {org.verified_status or "unverified"}).'],
                'covered': set(),
            }
            continue

        # Check C: Capability approval
        approved_caps = [
            c for c in caps
            if (c.confirmed or not require_approval) and c.eligibility_status == 'enabled' and c.accepts_rfqs
        ]
        if not approved_caps:
            reasons = []
            if any(c.eligibility_status == 'rejected' for c in caps):
                reasons.append('Capability was rejected by administration.')
            else:
                reasons.append('Capability pending administrative review/approval.')
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'pending_approval',
                'reasons': reasons,
                'covered': set(),
            }
            continue

        # Check D: Delivery location coverage
        covered_cities = {c.city.strip().casefold() for c in approved_caps if c.city}
        if org.city:
            covered_cities.add(org.city.strip().casefold())

        has_nationwide = any(
            (c.city or '').strip().casefold() in NATIONWIDE_KEYWORDS
            for c in approved_caps
        )
        location_matches = (
            not rfq_city_norm
            or has_nationwide
            or (rfq_city_norm in covered_cities)
        )
        if not location_matches:
            cities_disp = ', '.join(sorted({c.city for c in approved_caps if c.city})) or org.city or 'None'
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'location_mismatch',
                'reasons': [
                    f"Delivery location not covered: supplier serves '{cities_disp}', RFQ requires '{rfq.city}'."
                ],
                'covered': set(),
            }
            continue

        # Check E: Required qualifications
        supplier_quals = set()
        for c in approved_caps:
            for q in (c.qualifications or []):
                supplier_quals.add(q.strip().casefold())
        if required and not required.issubset(supplier_quals):
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'ineligible',
                'reasons': ['Missing required project qualifications.'],
                'covered': set(),
            }
            continue

        # Item coverage
        covered = set()
        exact_subcategory_items = set()
        main_category_items = set()
        for item_id, (main, sub) in item_scope.items():
            exact_subcategory = sub and any(c.category_id == sub and category_parents.get(c.category_id) == main for c in approved_caps)
            main_category = any(c.category_id == main for c in approved_caps)
            if exact_subcategory:
                covered.add(item_id)
                exact_subcategory_items.add(item_id)
            elif main_category:
                covered.add(item_id)
                main_category_items.add(item_id)

        if not covered:
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'ineligible',
                'reasons': ['Capability category hierarchy does not match requested items.'],
                'covered': set(),
            }
            continue

        exact_materials = set()
        if exact_material_ids:
            for c in approved_caps:
                if c.material_ids:
                    exact_materials.update(set(c.material_ids) & exact_material_ids)

        eligible_candidates[org_id] = {
            'org': org,
            'covered': covered,
            'exact_subcategory_items': exact_subcategory_items,
            'main_category_items': main_category_items,
            'exact_materials': exact_materials,
            'has_exact_city': rfq_city_norm in covered_cities,
            'approved_caps': approved_caps,
        }

    # Rank eligible candidates
    ranked = sorted(
        eligible_candidates.values(),
        key=lambda cand: (
            -len(cand.get('exact_materials', set())),
            -len(cand.get('exact_subcategory_items', set())),
            -len(cand['covered']),
            0 if cand.get('has_exact_city') else 1,
            cand['org'].name.casefold(),
            cand['org'].id,
        )
    )

    # Persist or update RFQMatch for eligible candidates
    existing_matches = {
        m.supplier_org_id: m
        for m in db.scalars(select(RFQMatch).where(RFQMatch.rfq_id == rfq.id)).all()
    }
    for obj in db.new:
        if isinstance(obj, RFQMatch) and obj.rfq_id == rfq.id:
            existing_matches[obj.supplier_org_id] = obj

    for candidate in ranked:
        exact_count = len(candidate.get('exact_materials', set()))
        score = min(100, 50 + 10 * len(candidate['covered']) + 10 * len(candidate.get('exact_subcategory_items', set())) + (10 if exact_count > 0 else 0) + (10 if candidate.get('has_exact_city') else 0))
        reasons = [
            'verified company',
            ('approved exact subcategory capability' if candidate.get('exact_subcategory_items') else 'approved main-category capability'),
            f'delivery city match: {rfq.city}' if candidate.get('has_exact_city') else ('service coverage matches delivery requirement' if rfq_city_norm else 'delivery location not specified; city coverage not used'),
            f"coverage:{len(candidate['covered'])}/{len(item_scope)}",
        ]
        if exact_material_ids:
            reasons.append(f"exact_materials:{exact_count}/{len(exact_material_ids)}")
        reasons.extend([f'item:{item_id}' for item_id in sorted(candidate['covered'])])
        reasons.extend([f'exact_subcategory_item:{item_id}' for item_id in sorted(candidate.get('exact_subcategory_items', set()))])
        reasons.extend([f'main_category_item:{item_id}' for item_id in sorted(candidate.get('main_category_items', set()))])
        reasons.append('Category eligibility does not confirm technical compliance')

        status = 'eligible' if len(candidate['covered']) == len(item_scope) else 'partial'
        m = existing_matches.get(candidate['org'].id)
        if m:
            m.score = score
            m.reasons = reasons
            m.status = status
        else:
            db.add(RFQMatch(
                rfq_id=rfq.id,
                supplier_org_id=candidate['org'].id,
                score=score,
                reasons=reasons,
                status=status,
            ))

    # Persist non-match diagnostics (Phase 9)
    for org_id, inelig in ineligible_diagnostics.items():
        m = existing_matches.get(org_id)
        diag_reasons = inelig['reasons'] + ['Category eligibility does not confirm technical compliance']
        if m:
            if m.status not in {'eligible', 'partial'}:
                m.score = 0
                m.reasons = diag_reasons
                m.status = inelig['status']
        else:
            db.add(RFQMatch(
                rfq_id=rfq.id,
                supplier_org_id=org_id,
                score=0,
                reasons=diag_reasons,
                status=inelig['status'],
            ))

    # Create invitations for eligible candidates
    eligible_org_ids = [cand['org'].id for cand in ranked]
    invited = set(db.scalars(select(Invitation.supplier_org_id).where(
        Invitation.rfq_id == rfq.id, Invitation.supplier_org_id.in_(eligible_org_ids),
    )).all())
    for obj in db.new:
        if isinstance(obj, Invitation) and obj.rfq_id == rfq.id:
            invited.add(obj.supplier_org_id)

    invitation_candidates = [cand for cand in ranked if cand['org'].id not in invited][:batch_size]
    invitations = [
        Invitation(rfq_id=rfq.id, supplier_org_id=cand['org'].id, status='invited')
        for cand in invitation_candidates
    ]
    db.add_all(invitations)

    if invitations:
        db.flush()
        member_rows = db.execute(
            select(Member.organization_id, Member.user_id)
            .join(User, User.id == Member.user_id)
            .where(
                User.is_active.is_(True),
                Member.organization_id.in_([inv.supplier_org_id for inv in invitations]),
            )
        ).all()
        members_by_org: dict[str, list[str]] = {}
        for org_id, user_id in member_rows:
            members_by_org.setdefault(org_id, []).append(user_id)

        for invitation in invitations:
            db.add(Outbox(
                event='rfq.invitation',
                payload={
                    'rfq_id': rfq.id,
                    'supplier_org_id': invitation.supplier_org_id,
                    'invitation_id': invitation.id,
                },
                dedupe_key=f'rfq-invitation:{invitation.id}',
            ))

        recipients = [
            user_id for invitation in invitations
            for user_id in members_by_org.get(invitation.supplier_org_id, [])
        ]
        db.add_all(Notification(
            recipient_user_id=user_id,
            event='rfq.invitation',
            message=f'New RFQ: {rfq.reference}',
            status='delivered',
        ) for user_id in recipients)

        rfq.status = 'collecting_quotes'
    elif invited:
        rfq.status = 'collecting_quotes'

    # Preserve legacy worker continuation if unbounded
    if not bounded and len(ranked) - len(invited) > len(invitations):
        key = f'rfq-matching:{rfq.id}:after:{len(invited) + len(invitations)}'
        if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
            db.add(Outbox(event='rfq.published', payload={'rfq_id': rfq.id}, dedupe_key=key))

    return {
        'eligible_suppliers': len(ranked),
        'new_invitations': len(invitations),
        'invitations': invitations,
        'evaluated_suppliers': len(candidate_ids),
        'ineligible_suppliers': len(ineligible_diagnostics),
    }
