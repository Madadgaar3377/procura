"""Set-based hybrid matching for active suppliers under the configured capability policy."""
from __future__ import annotations

from datetime import datetime, timezone
import re

from sqlalchemy import cast, func, literal, or_, select, union
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    RFQ,
    Capability,
    Category,
    ImportedSupplierTrade,
    Invitation,
    Material,
    Member,
    Notification,
    Organization,
    Outbox,
    PriceBookItem,
    RFQItem,
    RFQMatch,
    User,
)
from app.modules.materials.service import normalize_text
from app.modules.rfqs.material_matching import (
    extract_keywords,
    gemini_semantic_supplier_match,
    score_categories_for_text,
)
from app.security import as_utc

NATIONWIDE_KEYWORDS = {
    'all', 'all cities', 'nationwide', 'kingdom-wide', 'ksa', 'saudi arabia',
    'المملكة', 'جميع المدن', 'كل المدن', 'كافة المدن', 'السعودية',
}

CITY_ALIASES: dict[str, str] = {
    'riyadh': 'riyadh', 'al riyadh': 'riyadh', 'الرياض': 'riyadh',
    'jeddah': 'jeddah', 'jiddah': 'jeddah', 'جدة': 'jeddah', 'جده': 'jeddah',
    'dammam': 'dammam', 'al dammam': 'dammam', 'الدمام': 'dammam',
    'khobar': 'khobar', 'al khobar': 'khobar', 'alkhobar': 'khobar', 'الخبر': 'khobar',
    'dhahran': 'dhahran', 'الظهران': 'dhahran',
    'jubail': 'jubail', 'al jubail': 'jubail', 'الجبيل': 'jubail',
    'makkah': 'makkah', 'mecca': 'makkah', 'مكة': 'makkah', 'مكة المكرمة': 'makkah',
    'madinah': 'madinah', 'medina': 'madinah', 'المدينة': 'madinah', 'المدينة المنورة': 'madinah',
    'taif': 'taif', 'al taif': 'taif', 'الطائف': 'taif',
    'yanbu': 'yanbu', 'ينبع': 'yanbu',
    'abha': 'abha', 'أبها': 'abha', 'ابها': 'abha',
    'tabuk': 'tabuk', 'تبوك': 'tabuk',
    'qassim': 'qassim', 'buraydah': 'qassim', 'buraidah': 'qassim', 'القصيم': 'qassim', 'بريدة': 'qassim',
    'hofuf': 'hofuf', 'al ahsa': 'hofuf', 'alahsa': 'hofuf', 'الأحساء': 'hofuf', 'الهفوف': 'hofuf',
    'qatif': 'qatif', 'القطيف': 'qatif',
    'kharj': 'kharj', 'al kharj': 'kharj', 'الخرج': 'kharj',
    'hail': 'hail', 'حائل': 'hail',
    'najran': 'najran', 'نجران': 'najran',
    'jazan': 'jazan', 'jizan': 'jazan', 'جازان': 'jazan',
}

REGION_CITIES: dict[str, set[str]] = {
    'eastern province': {'dammam', 'khobar', 'dhahran', 'jubail', 'qatif', 'hofuf'},
    'eastern region': {'dammam', 'khobar', 'dhahran', 'jubail', 'qatif', 'hofuf'},
    'المنطقة الشرقية': {'dammam', 'khobar', 'dhahran', 'jubail', 'qatif', 'hofuf'},
    'الشرقية': {'dammam', 'khobar', 'dhahran', 'jubail', 'qatif', 'hofuf'},
    'riyadh region': {'riyadh', 'kharj'},
    'منطقة الرياض': {'riyadh', 'kharj'},
    'makkah region': {'jeddah', 'makkah', 'taif'},
    'western region': {'jeddah', 'makkah', 'taif', 'madinah', 'yanbu'},
    'western province': {'jeddah', 'makkah', 'taif', 'madinah', 'yanbu'},
    'منطقة مكة': {'jeddah', 'makkah', 'taif'},
    'المنطقة الغربية': {'jeddah', 'makkah', 'taif', 'madinah', 'yanbu'},
    'central region': {'riyadh', 'kharj', 'qassim'},
    'المنطقة الوسطى': {'riyadh', 'kharj', 'qassim'},
}


def normalize_city(value: str | None) -> str:
    raw = normalize_text(value or '')
    return CITY_ALIASES.get(raw, raw)


def city_covered_by(rfq_city: str | None, cap_city: str | None) -> tuple[bool, bool]:
    """Return (covered, exact_city_match) respecting Saudi city aliases, regions, and nationwide coverage."""
    rfq_norm = normalize_city(rfq_city)
    if not rfq_norm:
        return True, False
    cap_raw = normalize_text(cap_city or '')
    if not cap_raw:
        return False, False
    if cap_raw in NATIONWIDE_KEYWORDS:
        return True, False
    cap_norm = normalize_city(cap_raw)
    if cap_norm == rfq_norm:
        return True, True
    if rfq_norm in REGION_CITIES.get(cap_raw, set()):
        return True, False
    if cap_norm in REGION_CITIES.get(rfq_norm, set()):
        return True, False
    return False, False


def material_link(db, material_id):
    """JSON membership is evaluated by SQL, never by a supplier-wide Python scan."""
    if db.bind.dialect.name == 'postgresql':
        return cast(Capability.material_ids, JSONB).contains(func.jsonb_build_array(material_id))
    values = func.json_each(Capability.material_ids).table_valued('value')
    return select(literal(1)).select_from(values).where(values.c.value == material_id).exists()


def capability_scope(
    db,
    material_ids: set[str],
    sub_ids: set[str],
    main_ids: set[str] | None = None,
    keywords: set[str] | None = None,
    include_root_only: bool = False,
):
    """SQL predicate for candidate capabilities matching exact material, subcategory, main+evidence, or trade keywords."""
    clauses = []
    if sub_ids:
        clauses.append(Capability.category_id.in_(sub_ids))
    if material_ids or sub_ids:
        mat_conds = []
        if material_ids:
            mat_conds.append(Material.id.in_(material_ids))
        if sub_ids:
            mat_conds.append(Material.subcategory_id.in_(sub_ids))
        linked_material = select(Material.id).where(
            Material.active.is_(True),
            or_(*mat_conds),
            material_link(db, Material.id),
        ).exists()
        clauses.append(linked_material)
    if main_ids:
        if include_root_only:
            clauses.append(Capability.category_id.in_(main_ids))
        else:
            # Main-category capability qualifies as a candidate when the capability carries trade_keywords
            # (so empty broad main-category records with no evidence are not needlessly pulled in).
            clauses.append(
                (Capability.category_id.in_(main_ids))
                & (func.coalesce(Capability.trade_keywords, '') != '')
            )
    if keywords:
        top_kw = sorted(keywords, key=len, reverse=True)[:12]
        for kw in top_kw:
            escaped = kw.replace('_', '\\_').replace('%', '\\%')
            clauses.append(Capability.trade_keywords.ilike('%' + escaped + '%', escape='\\'))
    if not clauses:
        return literal(False)
    return or_(*clauses)


def match_and_invite(
    db: Session,
    rfq: RFQ,
    actor: User | None = None,
    limit: int | None = None,
    after_supplier_id: str | None = None,
    bounded: bool = False,
    single_batch: bool = False,
) -> dict:
    """Drain relevant SQL pages; legacy limit is the page size, never a discovery cap.

    Avoids holding a long-lived FOR UPDATE lock on the RFQ row across candidate
    scans so concurrent reads, draft edits, and invitations never hit 55P03.
    """
    summary = {
        'eligible_suppliers': 0,
        'new_invitations': 0,
        'invitations': [],
        'evaluated_suppliers': 0,
        'ineligible_suppliers': 0,
    }
    cursor = after_supplier_id
    while True:
        batch = _match_batch(db, rfq, actor, limit, cursor, True)
        for key in summary:
            if key == 'invitations':
                summary[key].extend(batch.get(key, []))
            elif key != 'next_cursor':
                summary[key] += batch.get(key, 0)
        db.flush()
        cursor = batch.get('next_cursor')
        summary['next_cursor'] = cursor
        if single_batch or not cursor:
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
    semantic_enabled: bool = False,
) -> dict:
    """Filter candidates in PostgreSQL, evaluate hybrid eligibility, and write matches/invitations."""
    if rfq.status not in {'published', 'collecting_quotes'} or as_utc(rfq.response_deadline) <= datetime.now(timezone.utc):
        return {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [], 'evaluated_suppliers': 0}

    requested = db.execute(
        select(RFQItem, Material)
        .outerjoin(Material, Material.id == RFQItem.material_id)
        .where(RFQItem.rfq_id == rfq.id)
    ).all()
    if not requested:
        return {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [], 'evaluated_suppliers': 0}

    all_active_categories = {c.id: c for c in db.scalars(select(Category).where(Category.active.is_(True))).all()}

    item_scope: dict[str, tuple[str | None, str | None]] = {}
    item_keywords: dict[str, set[str]] = {}
    item_material_codes: dict[str, str] = {}
    include_root_only = False

    for item, material in requested:
        main = item.main_category_id or (material.main_category_id if material else None)
        sub = item.subcategory_id or (material.subcategory_id if material else None)
        if not main and material:
            leaf = all_active_categories.get(material.category_id) or db.get(Category, material.category_id)
            if leaf:
                main = leaf.parent_id or leaf.id
                sub = leaf.id if leaf.parent_id else None
        # Fallback for unclassified items: infer active category in-memory without mutating the RFQ item
        if not main:
            inferred = score_categories_for_text(f'{item.description} {item.specifications or ""}', all_active_categories)
            if inferred:
                main = inferred[0]['main_category_id']
                sub = inferred[0]['subcategory_id']
        if main and not sub:
            include_root_only = True
        item_scope[item.id] = (main, sub)
        item_keywords[item.id] = extract_keywords(f'{item.description} {item.specifications or ""}')
        if material and material.code:
            item_material_codes[item.id] = material.code.strip().casefold()

    category_scopes = {cid for main, sub in item_scope.values() for cid in (main, sub) if cid}
    category_parents = {
        cid: all_active_categories[cid].parent_id
        for cid in category_scopes
        if cid in all_active_categories
    }
    exact_material_ids = {item.material_id for item, _ in requested if item.material_id}
    sub_ids = {sub for _, sub in item_scope.values() if sub}
    main_ids = {main for main, _ in item_scope.values() if main}
    all_keywords = {kw for kws in item_keywords.values() for kw in kws}

    batch_size = max(1, min(limit if limit is not None else 50, 100))
    all_have_canonical_ids = all(bool(main) for main, _ in item_scope.values())
    use_keyword_discovery = (not all_have_canonical_ids) or (not exact_material_ids and not sub_ids)

    # Set-based UNION of indexed candidate org_id sources so PostgreSQL uses Index Scans
    # rather than a full sequential scan over all organizations with OR EXISTS.
    union_branches = [
        select(RFQMatch.supplier_org_id.label('id')).where(RFQMatch.rfq_id == rfq.id),
    ]
    if sub_ids:
        union_branches.append(
            select(Capability.org_id.label('id')).where(Capability.category_id.in_(sub_ids))
        )
    if main_ids:
        if include_root_only:
            union_branches.append(
                select(Capability.org_id.label('id')).where(Capability.category_id.in_(main_ids))
            )
        else:
            union_branches.append(
                select(Capability.org_id.label('id')).where(
                    Capability.category_id.in_(main_ids),
                    func.coalesce(Capability.trade_keywords, '') != '',
                )
            )
    if exact_material_ids:
        mat_preds = [material_link(db, mid) for mid in sorted(exact_material_ids)]
        union_branches.append(
            select(Capability.org_id.label('id')).where(or_(*mat_preds))
        )
        union_branches.append(
            select(PriceBookItem.org_id.label('id')).where(
                PriceBookItem.active.is_(True),
                PriceBookItem.material_id.in_(exact_material_ids),
            )
        )
    if sub_ids:
        union_branches.append(
            select(PriceBookItem.org_id.label('id')).where(
                PriceBookItem.active.is_(True),
                PriceBookItem.subcategory_id.in_(sub_ids),
            )
        )
    if use_keyword_discovery and all_keywords:
        top_kw = sorted(all_keywords, key=len, reverse=True)[:8]
        kw_cap_preds = []
        kw_pb_preds = []
        kw_trade_preds = []
        for kw in top_kw:
            escaped = kw.replace('_', '\\_').replace('%', '\\%')
            pattern = '%' + escaped + '%'
            kw_cap_preds.append(Capability.trade_keywords.ilike(pattern, escape='\\'))
            kw_pb_preds.append(PriceBookItem.supplier_description.ilike(pattern, escape='\\'))
            kw_trade_preds.append(ImportedSupplierTrade.raw_trade.ilike(pattern, escape='\\'))
        union_branches.append(select(Capability.org_id.label('id')).where(or_(*kw_cap_preds)))
        union_branches.append(
            select(PriceBookItem.org_id.label('id')).where(
                PriceBookItem.active.is_(True),
                or_(*kw_pb_preds),
            )
        )
        union_branches.append(select(ImportedSupplierTrade.org_id.label('id')).where(or_(*kw_trade_preds)))

    if supplier_ids is not None:
        # Targeted evaluation (e.g. eligible_supplier_ids) already knows the exact org IDs.
        candidate_query = (
            select(Organization.id)
            .where(
                Organization.id.in_(supplier_ids),
                Organization.id != rfq.buyer_org_id,
            )
            .order_by(Organization.id)
        )
    else:
        combined = union(*union_branches).subquery('candidate_orgs')
        candidate_query = (
            select(combined.c.id)
            .where(combined.c.id != rfq.buyer_org_id)
        )
        if after_supplier_id:
            candidate_query = candidate_query.where(combined.c.id > after_supplier_id)
        candidate_query = candidate_query.order_by(combined.c.id)
        if bounded:
            candidate_query = candidate_query.limit(batch_size + 1)

    candidate_ids = list(db.scalars(candidate_query).all())
    # If canonical IDs yielded 0 candidates on the first page and keywords exist, try indexed keyword fallback once.
    if (
        not candidate_ids
        and supplier_ids is None
        and not after_supplier_id
        and not use_keyword_discovery
        and all_keywords
    ):
        top_kw = sorted(all_keywords, key=len, reverse=True)[:8]
        kw_branches = []
        kw_cap_preds = []
        kw_pb_preds = []
        kw_trade_preds = []
        for kw in top_kw:
            escaped = kw.replace('_', '\\_').replace('%', '\\%')
            pattern = '%' + escaped + '%'
            kw_cap_preds.append(Capability.trade_keywords.ilike(pattern, escape='\\'))
            kw_pb_preds.append(PriceBookItem.supplier_description.ilike(pattern, escape='\\'))
            kw_trade_preds.append(ImportedSupplierTrade.raw_trade.ilike(pattern, escape='\\'))
        kw_branches.append(select(Capability.org_id.label('id')).where(or_(*kw_cap_preds)))
        kw_branches.append(
            select(PriceBookItem.org_id.label('id')).where(
                PriceBookItem.active.is_(True),
                or_(*kw_pb_preds),
            )
        )
        kw_branches.append(select(ImportedSupplierTrade.org_id.label('id')).where(or_(*kw_trade_preds)))
        kw_combined = union(*kw_branches).subquery('kw_candidate_orgs')
        kw_query = select(kw_combined.c.id).where(kw_combined.c.id != rfq.buyer_org_id).order_by(kw_combined.c.id)
        if bounded:
            kw_query = kw_query.limit(batch_size + 1)
        candidate_ids = list(db.scalars(kw_query).all())

    has_more = bounded and len(candidate_ids) > batch_size
    if bounded:
        candidate_ids = candidate_ids[:batch_size]

    # Batch-fetch capabilities, organizations, pricebook items, and imported trades (no N+1, no duplicate candidate scan)
    rows = db.execute(
        select(Capability, Organization)
        .join(Organization, Organization.id == Capability.org_id)
        .where(Capability.org_id.in_(candidate_ids))
    ).all() if candidate_ids else []

    suppliers_caps: dict[str, list[Capability]] = {}
    suppliers_org = {
        org.id: org
        for org in db.scalars(select(Organization).where(Organization.id.in_(candidate_ids)))
    } if candidate_ids else {}
    for cap, org in rows:
        suppliers_caps.setdefault(org.id, []).append(cap)
        suppliers_org[org.id] = org

    pricebooks_by_org: dict[str, list[PriceBookItem]] = {}
    if candidate_ids:
        for pb in db.scalars(
            select(PriceBookItem).where(PriceBookItem.org_id.in_(candidate_ids), PriceBookItem.active.is_(True))
        ).all():
            pricebooks_by_org.setdefault(pb.org_id, []).append(pb)

    trades_by_org: dict[str, list[ImportedSupplierTrade]] = {}
    if candidate_ids:
        for tr in db.scalars(
            select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id.in_(candidate_ids))
        ).all():
            trades_by_org.setdefault(tr.org_id, []).append(tr)

    linked_ids = {mid for cap, _ in rows for mid in (cap.material_ids or [])}
    linked_materials = {
        m.id: m
        for m in db.scalars(select(Material).where(Material.id.in_(linked_ids), Material.active.is_(True)))
    } if linked_ids else {}

    category_names = {cid: all_active_categories[cid].name_en for cid in all_active_categories}
    active_categories = set(all_active_categories.keys())

    rfq_city_norm = normalize_city(rfq.city)
    required = {value.strip().casefold() for value in (rfq.required_qualifications or []) if value.strip()}
    requested_by_id = {item.id: item for item, _ in requested}

    def _supplier_profile_keywords(org: Organization, caps: list[Capability]) -> set[str]:
        parts = [org.description or '']
        for c in caps:
            if c.trade_keywords:
                parts.append(c.trade_keywords)
            cat = all_active_categories.get(c.category_id)
            if cat:
                parts.append(f'{cat.name_en} {cat.name_ar or ""}')
        for tr in trades_by_org.get(org.id, []):
            parts.append(f'{tr.raw_trade or ""} {tr.raw_brands or ""}')
        for pb in pricebooks_by_org.get(org.id, []):
            parts.append(f'{pb.supplier_description or ""} {pb.brand or ""} {pb.model or ""}')
        return extract_keywords(' '.join(parts))

    def capability_level(org: Organization, cap: Capability, item_id: str, main: str | None, sub: str | None) -> str | None:
        item = requested_by_id[item_id]
        material_id = item.material_id
        org_pbs = pricebooks_by_org.get(org.id, [])

        # 1. Exact material / material-code / verified pricebook match
        if material_id and (
            (material_id in (cap.material_ids or []) and material_id in linked_materials)
            or any(pb.material_id == material_id for pb in org_pbs)
        ):
            return 'exact material'
        code = item_material_codes.get(item_id)
        if code and any(code in (pb.supplier_description or '').casefold() or code in (pb.model or '').casefold() for pb in org_pbs):
            return 'exact material'

        # 2. Exact subcategory match
        if main and sub and main in category_parents and category_parents.get(sub) == main:
            if (
                cap.category_id == sub
                or any(
                    linked_materials[mid].subcategory_id == sub and linked_materials[mid].main_category_id == main
                    for mid in (cap.material_ids or []) if mid in linked_materials
                )
                or any(pb.subcategory_id == sub for pb in org_pbs)
            ):
                return 'exact subcategory'

        # 3. Keyword / catalog / trade-keyword match (requires approved capability + genuine lexical/synonym overlap)
        item_kw = item_keywords.get(item_id, set())
        if item_kw:
            cap_text_parts = [cap.trade_keywords or '', org.description or '']
            for tr in trades_by_org.get(org.id, []):
                cap_text_parts.append(f'{tr.raw_trade or ""} {tr.raw_brands or ""}')
            for pb in org_pbs:
                cap_text_parts.append(f'{pb.supplier_description or ""} {pb.brand or ""} {pb.model or ""}')
            evidence_kw = extract_keywords(' '.join(cap_text_parts))
            # Match if there is concrete product keyword overlap between RFQ item and supplier's trade/catalog evidence
            overlap = item_kw & evidence_kw
            if overlap:
                return 'keyword catalog'

        # 4. Main category match (only when RFQ item has no subcategory, or supplier has a pricebook in that main category)
        if main and main in category_parents and cap.category_id == main:
            if not sub:
                return 'main category'
            if any(pb.main_category_id == main for pb in org_pbs):
                return 'main category'

        return None

    require_approval = True
    eligible_candidates: dict[str, dict] = {}
    ineligible_diagnostics: dict[str, dict] = {}
    ai_pool_orgs: dict[str, tuple[Organization, list[Capability], bool]] = {}

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

        # Check B: Exclude rejected or suspended supplier organizations
        if org.verified_status in {'rejected', 'suspended'}:
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': org.verified_status,
                'reasons': [f'Supplier organization is {org.verified_status} and excluded from RFQ matching.'],
                'covered': set(),
            }
            continue

        # Check C: Capability approval
        approved_caps = [
            c for c in caps
            if (c.confirmed or not require_approval)
            and c.eligibility_status == 'enabled'
            and c.accepts_rfqs
            and c.category_id in active_categories
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

        matching_caps = [
            c for c in approved_caps
            if any(capability_level(org, c, item_id, main, sub) for item_id, (main, sub) in item_scope.items())
        ]
        if not matching_caps:
            # Keep as potential AI semantic pool ONLY if location + qualifications pass
            loc_caps = [c for c in approved_caps if city_covered_by(rfq.city, c.city)[0]]
            if loc_caps:
                supplier_quals = {q.strip().casefold() for c in loc_caps for q in (c.qualifications or [])}
                if not required or required.issubset(supplier_quals):
                    has_exact_city = any(city_covered_by(rfq.city, c.city)[1] for c in loc_caps)
                    ai_pool_orgs[org_id] = (org, loc_caps, has_exact_city)
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'ineligible',
                'covered': set(),
                'reasons': ['No approved material, subcategory, or verified keyword capability supports the requested items.'],
            }
            continue

        # Check D: Delivery location / region coverage
        covered_cities = {c.city.strip() for c in matching_caps if c.city and c.city.strip()}
        city_checks = [city_covered_by(rfq.city, c.city) for c in matching_caps]
        location_matches = any(cov for cov, _ in city_checks)
        has_exact_city = any(exact for _, exact in city_checks)

        if not location_matches:
            cities_disp = ', '.join(sorted(covered_cities)) or org.city or 'None'
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'location_mismatch' if covered_cities else 'coverage_unknown',
                'reasons': [
                    f"Delivery location not covered: supplier serves '{cities_disp}', RFQ requires '{rfq.city}'."
                    if covered_cities
                    else f"Service coverage is unknown; explicit coverage for {rfq.city} is required. Office city is not service coverage."
                ],
                'covered': set(),
            }
            continue

        # Check E: Required qualifications
        supplier_quals = {q.strip().casefold() for c in matching_caps for q in (c.qualifications or [])}
        if required and not required.issubset(supplier_quals):
            ineligible_diagnostics[org_id] = {
                'org': org,
                'status': 'ineligible',
                'reasons': ['Missing required project qualifications.'],
                'covered': set(),
            }
            continue

        # Item coverage evaluation
        covered: set[str] = set()
        exact_items: set[str] = set()
        exact_subcategory_items: set[str] = set()
        keyword_items: set[str] = set()
        main_category_items: set[str] = set()

        for item_id, (main, sub) in item_scope.items():
            item_caps = [c for c in matching_caps if city_covered_by(rfq.city, c.city)[0]]
            levels = {capability_level(org, c, item_id, main, sub) for c in item_caps}
            if 'exact material' in levels:
                covered.add(item_id)
                exact_items.add(item_id)
            elif 'exact subcategory' in levels:
                covered.add(item_id)
                exact_subcategory_items.add(item_id)
            elif 'keyword catalog' in levels:
                covered.add(item_id)
                keyword_items.add(item_id)
            elif 'main category' in levels:
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

        exact_materials = {requested_by_id[item_id].material_id for item_id in exact_items if requested_by_id[item_id].material_id}
        eligible_candidates[org_id] = {
            'org': org,
            'covered': covered,
            'exact_items': exact_items,
            'exact_subcategory_items': exact_subcategory_items,
            'keyword_items': keyword_items,
            'main_category_items': main_category_items,
            'ai_items': set(),
            'ai_explanations': {},
            'exact_materials': exact_materials,
            'has_exact_city': has_exact_city,
            'approved_caps': matching_caps,
        }

    # AI-assisted semantic matching ONLY for unresolved items without canonical IDs (never slows canonical matching)
    covered_by_any = {item_id for cand in eligible_candidates.values() for item_id in cand['covered']}
    unresolved_item_ids = [
        item_id for item_id in item_scope
        if item_id not in covered_by_any and not item_scope[item_id][0]
    ]
    settings = get_settings()
    if semantic_enabled and unresolved_item_ids and not evaluate_only and getattr(settings, 'gemini_enabled', True) and settings.gemini_api_key.get_secret_value():
        # If ai_pool_orgs is empty, fetch a small bounded slice of active approved suppliers covering the RFQ city
        if not ai_pool_orgs and supplier_ids is None:
            extra_rows = db.execute(
                select(Capability, Organization)
                .join(Organization, Organization.id == Capability.org_id)
                .where(
                    Organization.is_active.is_(True),
                    Organization.is_supplier.is_(True),
                    Organization.verified_status.notin_({'rejected', 'suspended'}),
                    Organization.id != rfq.buyer_org_id,
                    Capability.confirmed.is_(True),
                    Capability.accepts_rfqs.is_(True),
                    Capability.eligibility_status == 'enabled',
                    Capability.category_id.in_(active_categories),
                )
                .limit(120)
            ).all()
            grouped_extra: dict[str, tuple[Organization, list[Capability]]] = {}
            for cap, org in extra_rows:
                if org.id in eligible_candidates:
                    continue
                if not city_covered_by(rfq.city, cap.city)[0]:
                    continue
                grouped_extra.setdefault(org.id, (org, []))[1].append(cap)
            for oid, (org, caps) in list(grouped_extra.items())[:30]:
                supplier_quals = {q.strip().casefold() for c in caps for q in (c.qualifications or [])}
                if required and not required.issubset(supplier_quals):
                    continue
                has_exact = any(city_covered_by(rfq.city, c.city)[1] for c in caps)
                ai_pool_orgs[oid] = (org, caps, has_exact)

        if ai_pool_orgs:
            unresolved_payload = [
                {
                    'item_id': item_id,
                    'description': requested_by_id[item_id].description,
                    'specifications': requested_by_id[item_id].specifications or '',
                    'category': ' / '.join(
                        category_names[cid] for cid in item_scope[item_id] if cid and cid in category_names
                    ),
                }
                for item_id in unresolved_item_ids
            ]
            supplier_payload = []
            for oid, (org, caps, _) in list(ai_pool_orgs.items())[:30]:
                cat_list = sorted({category_names.get(c.category_id, '') for c in caps if c.category_id in category_names})
                kw_list = [c.trade_keywords for c in caps if c.trade_keywords]
                pb_list = [pb.supplier_description for pb in pricebooks_by_org.get(oid, []) if pb.supplier_description][:10]
                if not (cat_list or kw_list or pb_list or org.description):
                    continue
                supplier_payload.append({
                    'supplier_org_id': oid,
                    'categories': cat_list[:10],
                    'trade_keywords': ' | '.join(kw_list)[:300],
                    'catalog_items': pb_list,
                    'description': (org.description or '')[:240],
                })
            if supplier_payload:
                ai_matches, _failed = gemini_semantic_supplier_match(unresolved_payload, supplier_payload)
                for item_id, hits in ai_matches.items():
                    for hit in hits:
                        oid = hit['supplier_org_id']
                        if oid not in ai_pool_orgs:
                            continue
                        org, caps, has_exact = ai_pool_orgs[oid]
                        ineligible_diagnostics.pop(oid, None)
                        cand = eligible_candidates.setdefault(oid, {
                            'org': org,
                            'covered': set(),
                            'exact_items': set(),
                            'exact_subcategory_items': set(),
                            'keyword_items': set(),
                            'main_category_items': set(),
                            'ai_items': set(),
                            'ai_explanations': {},
                            'exact_materials': set(),
                            'has_exact_city': has_exact,
                            'approved_caps': caps,
                        })
                        cand['covered'].add(item_id)
                        cand['ai_items'].add(item_id)
                        cand['ai_explanations'][item_id] = f"{hit['explanation']} (confidence {hit['confidence']:.2f})"

    if not candidate_ids and not eligible_candidates:
        return {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [], 'evaluated_suppliers': 0}

    # Rank eligible candidates
    ranked = sorted(
        eligible_candidates.values(),
        key=lambda cand: (
            -len(cand.get('exact_items', set())),
            -len(cand.get('exact_subcategory_items', set())),
            -len(cand.get('keyword_items', set())),
            -len(cand.get('main_category_items', set())),
            -len(cand['covered']),
            0 if cand.get('has_exact_city') else 1,
            cand['org'].name.casefold(),
            cand['org'].id,
        ),
    )

    if evaluate_only:
        return {
            'eligible_ids': [candidate['org'].id for candidate in ranked],
            'covered_items': {candidate['org'].id: sorted(candidate['covered']) for candidate in ranked},
        }

    all_touch_ids = set(candidate_ids) | set(eligible_candidates.keys())
    existing_matches = {
        m.supplier_org_id: m
        for m in db.scalars(
            select(RFQMatch).where(RFQMatch.rfq_id == rfq.id, RFQMatch.supplier_org_id.in_(all_touch_ids))
        ).all()
    }
    for obj in db.new:
        if isinstance(obj, RFQMatch) and obj.rfq_id == rfq.id:
            existing_matches[obj.supplier_org_id] = obj

    for candidate in ranked:
        exact_count = len(candidate.get('exact_materials', set()))
        total_items = max(1, len(item_scope))
        score = (
            50
            + int(30 * len(candidate['exact_items']) / total_items)
            + int(20 * len(candidate['exact_subcategory_items']) / total_items)
            + int(14 * len(candidate.get('keyword_items', set())) / total_items)
            + int(10 * len(candidate.get('main_category_items', set())) / total_items)
            + int(8 * len(candidate.get('ai_items', set())) / total_items)
            + (5 if candidate.get('has_exact_city') else 0)
        )
        score = min(100, score)

        if candidate['exact_items']:
            primary_reason = 'Approved exact material capability'
        elif candidate['exact_subcategory_items']:
            primary_reason = 'Approved exact subcategory capability'
        elif candidate.get('keyword_items'):
            primary_reason = 'Approved supplier keyword/catalog capability'
        elif candidate.get('main_category_items'):
            primary_reason = 'Approved main category capability'
        else:
            primary_reason = 'AI-assisted semantic match based on verified supplier profile'

        reasons = [
            'active supplier company',
            primary_reason,
            (
                f'delivery city match: {rfq.city}'
                if candidate.get('has_exact_city')
                else ('service coverage matches delivery requirement' if rfq_city_norm else 'delivery location not specified; city coverage not used')
            ),
            f"coverage:{len(candidate['covered'])}/{len(item_scope)}",
        ]
        if exact_material_ids:
            reasons.append(f'exact_materials:{exact_count}/{len(exact_material_ids)}')
        reasons.extend([f'item:{item_id}' for item_id in sorted(candidate['covered'])])
        reasons.extend([f'exact_subcategory_item:{item_id}' for item_id in sorted(candidate.get('exact_subcategory_items', set()))])
        reasons.extend([f'keyword_item:{item_id}' for item_id in sorted(candidate.get('keyword_items', set()))])
        reasons.extend([f'main_category_item:{item_id}' for item_id in sorted(candidate.get('main_category_items', set()))])
        reasons.extend([f'ai_semantic_item:{item_id}' for item_id in sorted(candidate.get('ai_items', set()))])

        for item_id in sorted(candidate['covered']):
            main, sub = item_scope[item_id]
            if item_id in candidate['exact_items']:
                level = 'exact material'
            elif item_id in candidate['exact_subcategory_items']:
                level = 'exact subcategory'
            elif item_id in candidate.get('keyword_items', set()):
                level = 'keyword/catalog match'
            elif item_id in candidate.get('main_category_items', set()):
                level = 'main category'
            else:
                level = 'AI-assisted semantic match'
            detail = candidate.get('ai_explanations', {}).get(item_id)
            label = category_names.get(sub) or category_names.get(main) or requested_by_id[item_id].description
            reasons.append(f'Approved {level}: {label}.' + (f' {detail}' if detail else ''))

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
                if not capability_level(candidate['org'], cap, item_id, main, sub):
                    continue
                if not city_covered_by(rfq.city, cap.city)[0]:
                    continue
                for brand in cap.brands or []:
                    if isinstance(brand, str) and brand.strip():
                        saved_brands.add(brand.strip().casefold())
                for mid in cap.material_ids or []:
                    product = linked_materials.get(mid)
                    if not product or not (
                        product.id == item.material_id
                        or (sub and product.subcategory_id == sub and product.main_category_id == main)
                    ):
                        continue
                    for key, value in (product.specs_template or {}).items():
                        if str(key).strip().casefold() in {'brand', 'manufacturer'} and isinstance(value, str):
                            saved_brands.add(value.strip().casefold())
            for pb in pricebooks_by_org.get(candidate['org'].id, []):
                if pb.brand:
                    saved_brands.add(pb.brand.strip().casefold())
            brand_requests.append(match.group(1).strip())
            brand_evidence.append(bool(requested_brands) and requested_brands.issubset(saved_brands))

        if brand_requests and all(brand_evidence):
            reasons.append(
                'Brand requirement matches approved saved product data: '
                + ', '.join(brand_requests)
                + '. Availability and brand authorization require quotation confirmation.'
            )
        else:
            reasons.append(
                'Brand compliance is unverified'
                + (': ' + ', '.join(brand_requests) if brand_requests else '')
                + '; requested brands must be confirmed in the supplier quotation.'
            )
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

    # Persist non-match diagnostics
    for org_id, inelig in ineligible_diagnostics.items():
        m = existing_matches.get(org_id)
        diag_reasons = inelig['reasons'] + ['Category eligibility does not confirm technical compliance']
        if m:
            if m.status in {'eligible', 'partial'}:
                from app.security import audit
                audit(
                    db, actor, 'rfq.match_eligibility_corrected', rfq.buyer_org_id, m.id,
                    previous_status=m.status, status=inelig['status'], supplier_org_id=org_id,
                    historical_invitation_preserved=True,
                )
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

    # Create invitations for eligible candidates (duplicate-safe)
    eligible_org_ids = [cand['org'].id for cand in ranked]
    invited = set(db.scalars(
        select(Invitation.supplier_org_id).where(
            Invitation.rfq_id == rfq.id,
            Invitation.supplier_org_id.in_(eligible_org_ids),
        )
    ).all()) if eligible_org_ids else set()
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

        # Route in-app alerts through the same responsibility mapping as email/SMS.
        from app.modules.notifications.recipients import queue_invitation_in_app
        db.flush()
        member_orgs=set(db.scalars(select(Member.organization_id).join(User,User.id==Member.user_id).where(
            Member.organization_id.in_([inv.supplier_org_id for inv in invitations]),User.is_active.is_(True))).all())
        for invitation in invitations:
            if invitation.supplier_org_id in member_orgs:
                queue_invitation_in_app(db, invitation, rfq)

        rfq.status = 'collecting_quotes'
    elif invited:
        rfq.status = 'collecting_quotes'

    return {
        'next_cursor': candidate_ids[-1] if has_more else None,
        'eligible_suppliers': len(ranked),
        'new_invitations': len(invitations),
        'invitations': invitations,
        'evaluated_suppliers': len(all_touch_ids),
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
