"""Set-based matching for active suppliers under the configured capability policy."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from sqlalchemy import func, select, or_, cast, literal
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

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
    'all', 'all cities', 'nationwide', 'kingdom-wide', 'ksa', 'saudi arabia',
    'المملكة', 'جميع المدن', 'كل المدن', 'كافة المدن'
}


def material_link(db, material_id):
    """JSON membership is evaluated by SQL, never by a supplier-wide Python scan."""
    if db.bind.dialect.name == 'postgresql':
        return cast(Capability.material_ids, JSONB).contains(func.jsonb_build_array(material_id))
    values = func.json_each(Capability.material_ids).table_valued('value')
    return select(literal(1)).select_from(values).where(values.c.value == material_id).exists()


def capability_scope(db, material_ids, sub_ids):
    linked_material = select(Material.id).where(
        Material.active.is_(True),
        or_(Material.id.in_(material_ids), Material.subcategory_id.in_(sub_ids)),
        material_link(db, Material.id),
    ).exists()
    return or_(Capability.category_id.in_(sub_ids), linked_material)


def match_and_invite(db: Session, rfq: RFQ, actor: User | None = None, limit: int | None = None,
                     after_supplier_id: str | None = None, bounded: bool = False) -> dict:
    """Drain relevant SQL pages; legacy limit is the page size, never a discovery cap."""
    # Serialize every entry point (worker, publish, dispatch) on the same RFQ row.
    db.scalar(select(RFQ).where(RFQ.id == rfq.id).with_for_update())
    summary = {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [],
               'evaluated_suppliers': 0, 'ineligible_suppliers': 0}
    cursor = after_supplier_id
    while True:
        batch = _match_batch(db, rfq, actor, limit, cursor, True)
        for key in summary:
            if key == 'invitations':
                summary[key].extend(batch.get(key, []))
            else:
                summary[key] += batch.get(key, 0)
        db.flush()
        cursor = batch.get('next_cursor')
        if not cursor:
            return summary


def _match_batch(
    db: Session,
    rfq: RFQ,
    actor: User | None = None,
    limit: int | None = None,
    after_supplier_id: str | None = None,
    bounded: bool = False,
    evaluate_only: bool = False,
    supplier_ids: list[str] | None = None,
) -> dict:
    """Filter candidates in PostgreSQL, evaluate eligibility, and write matches/invitations.

    Active status, supplier role, configured capability eligibility, and
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
        sub = item.subcategory_id or (material.subcategory_id if material else None)
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
        db.execute(select(Category.id, Category.parent_id).where(Category.id.in_(category_scopes), Category.active.is_(True))).all()
    )
    exact_material_ids = {item.material_id for item, _ in requested if item.material_id}

    batch_size = max(1, min(limit if limit is not None else 100, 100))
    sub_ids = {sub for _, sub in item_scope.values() if sub}
    relevant = capability_scope(db, exact_material_ids, sub_ids)

    # Phase 3 & 8: Scan candidate supplier organizations offering the required category scope.
    candidate_query = (
        select(Organization.id)
        .where(
            or_(select(Capability.id).where(Capability.org_id == Organization.id, relevant).exists(),
                select(RFQMatch.id).where(RFQMatch.rfq_id == rfq.id, RFQMatch.supplier_org_id == Organization.id).exists()),
            Organization.id != rfq.buyer_org_id,
        )
    )
    if after_supplier_id:
        candidate_query = candidate_query.where(Organization.id > after_supplier_id)
    candidate_query = candidate_query.order_by(Organization.id)
    if supplier_ids is not None:
        candidate_query = candidate_query.where(Organization.id.in_(supplier_ids))
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
        )
    ).all()

    suppliers_caps: dict[str, list[Capability]] = {}
    suppliers_org = {org.id: org for org in db.scalars(select(Organization).where(Organization.id.in_(candidate_ids)))}
    for cap, org in rows:
        suppliers_caps.setdefault(org.id, []).append(cap)
        suppliers_org[org.id] = org

    linked_ids = {mid for cap, _ in rows for mid in (cap.material_ids or [])}
    linked_materials = {m.id: m for m in db.scalars(select(Material).where(Material.id.in_(linked_ids), Material.active.is_(True)))}
    category_names = dict(db.execute(select(Category.id, Category.name_en).where(Category.id.in_(category_scopes))).all())
    active_categories = set(db.scalars(select(Category.id).where(Category.active.is_(True), Category.id.in_({cap.category_id for cap, _ in rows}))))

    rfq_city_norm = (rfq.city or '').strip().casefold()
    required = {value.strip().casefold() for value in (rfq.required_qualifications or []) if value.strip()}
    requested_by_id = {item.id: item for item, _ in requested}

    def capability_level(cap, item_id, main, sub):
        if main not in category_parents or (sub and category_parents.get(sub) != main):
            return None
        material_id = requested_by_id[item_id].material_id
        if material_id and material_id in (cap.material_ids or []) and material_id in linked_materials:
            return 'exact material'
        if sub and category_parents.get(sub) == main:
            if cap.category_id == sub or any(
                linked_materials[mid].subcategory_id == sub and linked_materials[mid].main_category_id == main
                for mid in (cap.material_ids or []) if mid in linked_materials
            ):
                return 'exact subcategory'
        return None
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

        # Check C: Capability approval
        approved_caps = [
            c for c in caps
            if (c.confirmed or not require_approval) and c.eligibility_status == 'enabled' and c.accepts_rfqs and c.category_id in active_categories
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

        approved_caps = [c for c in approved_caps if any(
            capability_level(c, item_id, main, sub) for item_id, (main, sub) in item_scope.items())]
        if not approved_caps:
            ineligible_diagnostics[org_id] = {
                'org': org, 'status': 'ineligible', 'covered': set(),
                'reasons': ['No approved material or subcategory capability supports the requested items. A broad main category does not qualify.'],
            }
            continue

        # Check D: Delivery location coverage
        covered_cities = {c.city.strip().casefold() for c in approved_caps if c.city and c.city.strip()}

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
                'status': 'location_mismatch' if covered_cities else 'coverage_unknown',
                'reasons': [
                    f"Delivery location not covered: supplier serves '{cities_disp}', RFQ requires '{rfq.city}'."
                    if covered_cities else f"Service coverage is unknown; explicit coverage for {rfq.city} is required. Office city is not service coverage."
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
        exact_items = set()
        for item_id, (main, sub) in item_scope.items():
            # Coverage belongs to the capability that supports this item.
            item_caps = [c for c in approved_caps if not rfq_city_norm or
                         (c.city or '').strip().casefold() in NATIONWIDE_KEYWORDS | {rfq_city_norm}]
            levels = {capability_level(c, item_id, main, sub) for c in item_caps}
            exact_material = 'exact material' in levels
            exact_subcategory = 'exact subcategory' in levels
            if exact_material:
                covered.add(item_id)
                exact_items.add(item_id)
            elif exact_subcategory:
                covered.add(item_id)
                exact_subcategory_items.add(item_id)

        if not covered:
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'ineligible',
                'reasons': ['Capability category hierarchy does not match requested items.'],
                'covered': set(),
            }
            continue

        exact_materials = {requested_by_id[item_id].material_id for item_id in exact_items}

        eligible_candidates[org_id] = {
            'org': org,
            'covered': covered,
            'exact_subcategory_items': exact_subcategory_items,
            'main_category_items': main_category_items,
            'exact_materials': exact_materials,
            'exact_items': exact_items,
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

    if evaluate_only:
        return {'eligible_ids': [candidate['org'].id for candidate in ranked],
                'covered_items': {candidate['org'].id: sorted(candidate['covered']) for candidate in ranked}}

    # Persist or update RFQMatch for eligible candidates
    existing_matches = {
        m.supplier_org_id: m
        for m in db.scalars(select(RFQMatch).where(RFQMatch.rfq_id == rfq.id, RFQMatch.supplier_org_id.in_(candidate_ids))).all()
    }
    for obj in db.new:
        if isinstance(obj, RFQMatch) and obj.rfq_id == rfq.id:
            existing_matches[obj.supplier_org_id] = obj

    for candidate in ranked:
        exact_count = len(candidate.get('exact_materials', set()))
        score = (50 + int(30 * len(candidate['exact_items']) / len(item_scope))
                 + int(15 * len(candidate['exact_subcategory_items']) / len(item_scope))
                 + (5 if candidate.get('has_exact_city') else 0))
        reasons = [
            'active supplier company',
            ('Approved exact material capability' if candidate.get('exact_items') else 'Approved exact subcategory capability'),
            f'delivery city match: {rfq.city}' if candidate.get('has_exact_city') else ('service coverage matches delivery requirement' if rfq_city_norm else 'delivery location not specified; city coverage not used'),
            f"coverage:{len(candidate['covered'])}/{len(item_scope)}",
        ]
        if exact_material_ids:
            reasons.append(f"exact_materials:{exact_count}/{len(exact_material_ids)}")
        reasons.extend([f'item:{item_id}' for item_id in sorted(candidate['covered'])])
        reasons.extend([f'exact_subcategory_item:{item_id}' for item_id in sorted(candidate.get('exact_subcategory_items', set()))])
        reasons.extend([f'main_category_item:{item_id}' for item_id in sorted(candidate.get('main_category_items', set()))])
        for item_id in sorted(candidate['covered']):
            main, sub = item_scope[item_id]
            level = 'exact material' if item_id in candidate['exact_items'] else 'exact subcategory'
            reasons.append(f"Approved {level}: {category_names.get(sub) or requested_by_id[item_id].description}.")
        brand_requests = []
        brand_evidence = []
        for item_id in sorted(candidate['covered']):
            item = requested_by_id[item_id]
            match = re.search(r'\bbrands?\s*:\s*([^;\n]+)', f'{item.description}\n{item.specifications}', re.I)
            if not match:
                continue
            requested_brands = {value.strip().casefold() for value in re.split(r'[,/|]', match.group(1)) if value.strip()}
            saved_brands = set()
            main, sub = item_scope[item_id]
            for cap in candidate['approved_caps']:
                if not capability_level(cap, item_id, main, sub):
                    continue
                if rfq_city_norm and (cap.city or '').strip().casefold() not in NATIONWIDE_KEYWORDS | {rfq_city_norm}:
                    continue
                for mid in cap.material_ids or []:
                    product = linked_materials.get(mid)
                    if not product or not (product.id == item.material_id or
                                           (sub and product.subcategory_id == sub and product.main_category_id == main)):
                        continue
                    for key, value in (product.specs_template or {}).items():
                        if str(key).strip().casefold() in {'brand', 'manufacturer'} and isinstance(value, str):
                            saved_brands.add(value.strip().casefold())
            brand_requests.append(match.group(1).strip())
            brand_evidence.append(bool(requested_brands) and requested_brands.issubset(saved_brands))
        if brand_requests and all(brand_evidence):
            reasons.append('Brand requirement matches approved saved product data: ' + ', '.join(brand_requests)
                           + '. Availability and brand authorization require quotation confirmation.')
        else:
            reasons.append('Brand compliance is unverified' + (': ' + ', '.join(brand_requests) if brand_requests else '')
                           + '; requested brands must be confirmed in the supplier quotation.')
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
            if m.status in {'eligible', 'partial'}:
                from app.security import audit
                audit(db, actor, 'rfq.match_eligibility_corrected', rfq.buyer_org_id, m.id,
                      previous_status=m.status, status=inelig['status'], supplier_org_id=org_id,
                      historical_invitation_preserved=True)
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

    return {
        'next_cursor': candidate_ids[-1] if has_more else None,
        'eligible_suppliers': len(ranked),
        'new_invitations': len(invitations),
        'invitations': invitations,
        'evaluated_suppliers': len(candidate_ids),
        'ineligible_suppliers': len(ineligible_diagnostics),
    }


def eligible_supplier_ids(db, rfq, org_ids):
    """Use the matching policy for delivery/reminders without writing or inviting."""
    eligible = set()
    ids = list(org_ids)
    for start in range(0, len(ids), 100):
        result = _match_batch(db, rfq, evaluate_only=True, supplier_ids=ids[start:start + 100])
        eligible.update(result.get('eligible_ids', []))
    return eligible
