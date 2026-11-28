"""Set-based hybrid matching for active suppliers under the configured capability policy."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
import time
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import cast, func, literal, or_, select, text, union
from sqlalchemy.dialects.postgresql import JSONB, array as pg_array
from sqlalchemy.orm import Session, defer

from app import catalog_cache
from app.config import get_settings
from app.models import (
    RFQ,
    Capability,
    Category,
    ImportedSupplierTrade,
    Invitation,
    Material,
    Member,
    Organization,
    Outbox,
    PriceBookItem,
    RFQItem,
    RFQMatch,
    User,
)
from app.modules.materials.service import normalize_text
from app.modules.rfqs.material_matching import (
    PRODUCT_HEADS,
    canonical_product_text,
    canonical_tokens,
    category_pair,
    extract_keywords,
    family_similarity,
    gemini_semantic_supplier_match,
    product_search_terms,
    product_tokens,
    technical_conflict,
)
from app.security import as_utc

LEVEL_EXACT = 'exact material'
LEVEL_ALIAS = 'product alias'
LEVEL_FAMILY = 'product family'
LEVEL_SUB = 'subcategory'
LEVEL_MAIN = 'main category'
LEVEL_AI = 'AI-assisted semantic match'
LEVEL_ORDER = (LEVEL_EXACT, LEVEL_ALIAS, LEVEL_FAMILY, LEVEL_SUB, LEVEL_MAIN, LEVEL_AI)
LEVEL_RANK = {level: rank for rank, level in enumerate(LEVEL_ORDER)}
# Supplier product evidence for the requested product. Only these qualify a line when
# the line describes a specific product; category capability alone qualifies only a
# line whose description adds nothing beyond its category (see scope['generic']).
PRODUCT_LEVELS = frozenset({LEVEL_EXACT, LEVEL_ALIAS, LEVEL_FAMILY})
AUTO_INVITE_LEVELS = PRODUCT_LEVELS  # legacy name
# Final buyer-facing statuses; every other status is internal (admin diagnostics only).
MATCHED_STATUSES = ('eligible', 'partial')
# Candidates retained for admin review only: never shown, counted or invited.
REVIEW_STATUSES = ('insufficient_evidence', 'location_unconfirmed')
LEVEL_WEIGHT = {LEVEL_EXACT: 30, LEVEL_ALIAS: 27, LEVEL_FAMILY: 24, LEVEL_SUB: 18, LEVEL_MAIN: 8, LEVEL_AI: 8}
LEVEL_MARKER = {LEVEL_EXACT: 'exact_material_item:', LEVEL_ALIAS: 'alias_item:', LEVEL_FAMILY: 'family_item:',
                LEVEL_SUB: 'exact_subcategory_item:', LEVEL_MAIN: 'main_category_item:', LEVEL_AI: 'ai_semantic_item:'}
LEVEL_LABEL = {LEVEL_EXACT: 'Approved exact material capability', LEVEL_ALIAS: 'Approved product alias match',
               LEVEL_FAMILY: 'Approved product family match', LEVEL_SUB: 'Approved exact subcategory capability',
               LEVEL_MAIN: 'Approved main category capability',
               LEVEL_AI: 'AI-assisted semantic match based on verified supplier profile'}
# An admin import that maps a supplier to every subcategory (of a main category with
# at least this many) is discipline-wide, not subcategory evidence.
BLANKET_MIN_SUBCATEGORIES = 3
# A capability linked to at least this many materials covering this share of its
# category's active materials is a category-wide assignment, not product evidence.
CATEGORY_WIDE_MIN_LINKS = 20
CATEGORY_WIDE_SHARE = 0.8
# Persisted machine markers; never shown to buyers.
INTERNAL_REASON_PREFIXES = ('item:', 'coverage:', 'exact_materials:', 'taxonomy:', 'broad_only', 'fuzzy_item:',
                            'keyword_item:', *LEVEL_MARKER.values())


BRAND_CLAUSE = re.compile(r'\bbrands?\s*:\s*([^;\n]+)', re.I)
# "Brand: ABB only", "Brand: ABB (mandatory)", "no alternative/equivalent" make a brand mandatory.
MANDATORY_BRAND = re.compile(r'\b(?:only|mandatory|strictly|no\s+(?:alternatives?|equivalents?|substitut\w*))\b|\(\s*must\s*\)', re.I)


def requested_brand(item) -> tuple[str, set[str], bool] | None:
    """(display text, wanted brands, mandatory) from the RFQ line, or None when no brand is requested."""
    text = f'{item.description}\n{item.specifications or ""}'
    found = BRAND_CLAUSE.search(text)
    if not found:
        return None
    clause = found.group(1).strip()
    mandatory = bool(MANDATORY_BRAND.search(clause))
    names = MANDATORY_BRAND.sub(' ', clause).replace('(', ' ').replace(')', ' ')
    wanted = {v.strip().casefold() for v in re.split(r'[,/|]|\bor\b', names) if v.strip()}
    return clause, wanted, mandatory


def match_level(reasons: list[str] | None, item_id: str | None = None) -> list[str]:
    """Evidence levels recorded in persisted markers (optionally for one RFQ line)."""
    found = []
    for level in LEVEL_ORDER:
        marker = LEVEL_MARKER[level]
        if any(r.startswith(marker) and (item_id is None or r == marker + item_id) for r in reasons or []):
            found.append(level)
    return found


NATIONWIDE_KEYWORDS = {
    'all', 'all cities', 'nationwide', 'kingdom-wide', 'kingdom wide', 'ksa', 'saudi arabia',
    'all saudi arabia', 'all ksa', 'all over saudi arabia', 'all over ksa', 'all kingdom', 'whole kingdom',
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


def normalize_product(value: str | None) -> str:
    """Normalize buyer and supplier wording without changing stored descriptions."""
    return canonical_product_text(value)


def city_covered_by(rfq_city: str | None, cap_city: str | None) -> tuple[bool, bool]:
    """Return (covered, exact_city_match) respecting Saudi city aliases, regions, and nationwide coverage."""
    rfq_norm = normalize_city(rfq_city)
    if not rfq_norm:
        return True, False
    cap_raw = normalize_text(cap_city or '')
    if not cap_raw:
        return False, False
    if any(separator in cap_raw for separator in (',', ';', '|')):
        return (True, False) if any(city_covered_by(rfq_city, part)[0] for part in re.split(r'[,;|]', cap_raw)) else (False, False)
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


def match_and_invite(
    db: Session,
    rfq: RFQ,
    actor: User | None = None,
    limit: int | None = None,
    after_supplier_id: str | None = None,
    bounded: bool = False,
    single_batch: bool = False,
    time_budget: float | None = None,
) -> dict:
    """Drain relevant SQL pages; legacy limit is the page size, never a discovery cap.

    Avoids holding a long-lived FOR UPDATE lock on the RFQ row across candidate
    scans so concurrent reads, draft edits, and invitations never hit 55P03.
    ``time_budget`` (seconds) stops after the page that exceeds it and returns the
    cursor so a durable job can continue without repeating finished pages.
    """
    started = time.monotonic()
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
        if time_budget is not None and time.monotonic() - started >= time_budget:
            return summary


_CATEGORY_CACHE: dict[tuple, tuple[float, dict]] = {}
_CATEGORY_CACHE_TTL = 120.0


def active_categories(db: Session) -> dict[str, SimpleNamespace]:
    """Active taxonomy as plain values; cached briefly per PostgreSQL database and catalog epoch."""
    postgres = db.bind.dialect.name == 'postgresql'
    key = (str(db.bind.url), catalog_cache._epoch()) if postgres else None
    if key:
        hit = _CATEGORY_CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CATEGORY_CACHE_TTL:
            return hit[1]
    rows = db.execute(select(Category.id, Category.name_en, Category.name_ar, Category.parent_id,
                             Category.normalized_name).where(Category.active.is_(True))).all()
    categories = {row.id: SimpleNamespace(id=row.id, name_en=row.name_en, name_ar=row.name_ar,
                                          parent_id=row.parent_id, normalized_name=row.normalized_name, active=True)
                  for row in rows}
    if key:
        _CATEGORY_CACHE.clear()
        _CATEGORY_CACHE[key] = (time.monotonic(), categories)
    return categories


def _product_description(description: str | None) -> str:
    """Product wording only: brand clauses describe a requirement, not the product."""
    return re.sub(r'(?:^|[;\n,])\s*brands?\s*:[^;\n]*', ' ', description or '', flags=re.I).strip()


def taxonomy_label(categories: dict, main: str | None, sub: str | None) -> str:
    return ' / '.join(categories[cid].name_en for cid in (main, sub) if cid and cid in categories)


def resolve_item_scopes(db: Session, requested, categories: dict) -> tuple[list[dict], dict]:
    """Resolve buyer taxonomy plus the Material Master product family for every RFQ line.

    One bounded, trigram-indexed Material query serves all lines. The family is limited to
    the buyer's main category when that category contains members, so a discipline
    choice is respected; a conflicting subcategory is reported, not silently rewritten.
    """
    from app.modules.imports.bulk import normalize_discipline
    children: dict[str, set[str]] = {}
    for cid, category in categories.items():
        if category.parent_id:
            children.setdefault(category.parent_id, set()).add(cid)
    scopes: list[dict] = []
    terms: set[str] = set()
    for item, material in requested:
        main = item.main_category_id or (material.main_category_id if material else None)
        sub = item.subcategory_id or (material.subcategory_id if material else None)
        if not main and material:
            leaf = categories.get(material.category_id)
            if leaf:
                main = leaf.parent_id or leaf.id
                sub = leaf.id if leaf.parent_id else None
        text = _product_description(item.description)
        tokens = product_tokens(text)
        # A requested Material Master record with an unrecognized discipline is not
        # matched; approved aliases (e.g. plumbing -> Mechanical) are recognized.
        blocked = bool(material and material.discipline and not normalize_discipline(material.discipline))
        scopes.append({
            'id': item.id, 'item': item, 'material': material, 'material_id': item.material_id,
            'main': main, 'sub': sub, 'tokens': tokens, 'phrase': canonical_product_text(text),
            'keywords': extract_keywords(text), 'blocked': blocked,
            'code': material.code.strip().casefold() if material and material.code else None,
        })
        terms.update(product_search_terms(tokens)[:6])

    info: dict[str, tuple[str, str | None, str | None, set[str]]] = {}
    exact_ids = {scope['material_id'] for scope in scopes if scope['material_id']}
    clauses = [Material.name_en.ilike('%' + t.replace('_', '\\_').replace('%', '\\%') + '%', escape='\\')
               for t in sorted(terms)[:120]]
    if exact_ids:
        clauses.append(Material.id.in_(exact_ids))
    if clauses:
        for row in db.execute(select(Material.id, Material.name_en, Material.name_ar, Material.main_category_id,
                                     Material.subcategory_id, Material.category_id)
                              .where(Material.active.is_(True), or_(*clauses))).all():
            pair = category_pair(row, categories)
            if pair[0] not in categories:
                continue
            info[row.id] = (row.name_en, pair[0], pair[1], product_tokens(row.name_en) | product_tokens(row.name_ar))

    for scope in scopes:
        request = _product_description(scope['item'].description)
        specs = _product_description(scope['item'].specifications)
        # A product with a different critical qualifier (RGB, Cat6a, UPVC...), voltage,
        # size, model or a variant contradicting the buyer's specifications is not the
        # requested product, however similar its wording.
        family = {mid: sim for mid, value in info.items()
                  if (sim := family_similarity(scope['tokens'], value[3])) and not technical_conflict(request, value[0], specs)}
        if len(scope['tokens']) == 1 and scope['sub']:
            # A one-word product ("Plug Mi" -> plug) is too vague across the catalogue, but
            # within the line's own subcategory a product naming that word is the product.
            (word,) = scope['tokens']
            family.update({mid: 0.5 for mid, value in info.items() if mid not in family and value[2] == scope['sub']
                           and word in value[3] and not technical_conflict(request, value[0], specs)})
        if scope['material_id'] in info:
            family[scope['material_id']] = 1.0
        if scope['main']:
            in_main = {mid: sim for mid, sim in family.items() if info[mid][1] == scope['main']}
            family = in_main
        pairs = Counter((info[mid][1], info[mid][2]) for mid in family)
        family_main, family_sub = pairs.most_common(1)[0][0] if pairs else (None, None)
        family_subs = {sub for _, sub in pairs if sub}
        scope_mains = {cid for cid in {scope['main'], *(main for main, _ in pairs)} if cid}
        # Product evidence may come from any subcategory holding the product family.
        scope_subs = {cid for cid in {scope['sub'], *family_subs} if cid}
        # Capability-only (no product evidence) subcategory matching uses the buyer's
        # subcategory AND the Material Master subcategory of the requested product,
        # never every subcategory that happens to contain a similar word.
        exact_sub = info[scope['material_id']][2] if scope['material_id'] in info else None
        capability_subs = {cid for cid in (scope['sub'], exact_sub, family_sub) if cid and cid in categories
                           and (not scope['main'] or cid == scope['sub'] or categories[cid].parent_id == scope['main'])}
        # A description that only restates its category ("Pipe" filed under Pipes) names no
        # specific product, so the most specific approved category capability is the
        # evidence. Any other description needs supplier product evidence.
        # generic_cats: the categories (buyer's or the Material Master's) whose names cover
        # every product word of the line; only capability in one of them is evidence.
        exact_sub_id = info[scope['material_id']][2] if scope['material_id'] in info else None
        generic_cats = set()
        for cid in {scope['main'], scope['sub'], exact_sub_id, family_sub} - {None}:
            if cid not in categories:
                continue
            words = product_tokens(categories[cid].name_en)
            if categories[cid].parent_id in categories:
                words |= product_tokens(categories[categories[cid].parent_id].name_en)
            if not scope['tokens'] or scope['tokens'] <= words:
                generic_cats.add(cid)
        scope['generic_cats'] = generic_cats
        scope['generic'] = bool(generic_cats)
        scope.update({
            'capability_subs': capability_subs,
            'family': family,
            'family_main': family_main,
            'family_sub': family_sub,
            'family_label': taxonomy_label(categories, family_main, family_sub),
            'scope_mains': scope_mains,
            'scope_subs': scope_subs,
            'descriptive_subs': {sid for sid in scope_subs if sid in categories
                                 and scope['tokens'] & product_tokens(categories[sid].name_en)},
            'related': scope_mains | scope_subs | capability_subs | {c for m in scope_mains for c in children.get(m, ())},
            'conflict': bool(family and scope['main'] and (
                family_main != scope['main'] or (scope['sub'] and scope['sub'] not in family_subs))),
        })
    return scopes, info


_SCOPE_CACHE: dict[tuple, tuple[float, list[dict], dict]] = {}
_SCOPE_CACHE_TTL = 300.0


def cached_item_scopes(db: Session, rfq: RFQ, requested, categories: dict) -> tuple[list[dict], dict]:
    """resolve_item_scopes, reused for the same RFQ lines within a few minutes.

    Every matching page and every invitation eligibility check needs the same
    Material Master product family; resolving it per call made each invitation
    job repeat the most expensive query and fuzzy scoring. The key holds the line
    values, the material versions and the taxonomy, so an edit is never served
    stale. Only plain derived values are cached, never ORM rows of another session.
    """
    taxonomy = hash(frozenset((cid, c.parent_id, c.name_en, c.normalized_name) for cid, c in categories.items()))
    # Any Material Master insert, edit, reclassification or deactivation moves this stamp.
    catalog = db.scalar(select(func.max(Material.updated_at)))
    key = (id(db.get_bind()), rfq.id, taxonomy, catalog, tuple(
        (item.id, item.material_id, item.main_category_id, item.subcategory_id, item.description,
         item.specifications, getattr(material, 'version', None)) for item, material in requested))
    now = time.monotonic()
    hit = _SCOPE_CACHE.get(key)
    if hit and now - hit[0] < _SCOPE_CACHE_TTL:
        rows = {item.id: (item, material) for item, material in requested}
        return [{**derived, 'item': rows[derived['id']][0], 'material': rows[derived['id']][1]}
                for derived in hit[1]], hit[2]
    scopes, info = resolve_item_scopes(db, requested, categories)
    if len(_SCOPE_CACHE) >= 64:
        _SCOPE_CACHE.clear()
    _SCOPE_CACHE[key] = (now, [{k: v for k, v in scope.items() if k not in {'item', 'material'}} for scope in scopes], info)
    return scopes, info


def _evidence_texts(trades: list, pricebooks: list, caps: list, terms: tuple[str, ...]) -> list[tuple[str, str, set[str]]]:
    """Supplier product wording as (original, canonical, tokens), computed once per supplier.

    Normalize all category-scoped fragments. An exact-word prefilter would discard
    spelling variants before the fuzzy word comparison can evaluate them.
    """
    parts = []
    for trade in trades:
        parts.extend(re.split(r'[;\n|]', f'{trade.raw_products or ""};{trade.raw_trade or ""}'))
    for pb in pricebooks:
        parts.extend((pb.supplier_description or '', pb.offered_description or ''))
    for cap in caps:
        parts.extend((cap.trade_keywords or '').split('|'))
    unique = dict.fromkeys(p.strip() for p in parts if p and p.strip())
    texts = []
    for text in unique:
        canonical = canonical_product_text(text)
        texts.append((text, canonical, canonical_tokens(canonical)))
    return texts


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
    """Find candidates with indexed SQL, grade product evidence, and persist matches/invitations.

    Candidate discovery (indexed SQL over the discipline, categories, material links,
    price lists and supplier product wording) is separate from final eligibility, which
    is decided per RFQ line:

    - Product evidence qualifies: exact material (the selected material is technically the
      requested product and the link is not a category-wide assignment), exact/alias
      product wording, or a fuzzy product-family match. Critical qualifiers (RGB, Cat6,
      UPVC...) must match; stated voltage, size and model must not conflict.
    - Category capability alone qualifies only a line whose words restate that category
      ("Pipe" under Pipes), and only capability in that category. Otherwise the supplier
      is ``insufficient_evidence``: kept for admin review, never shown, counted or invited.
    - Location: the capability service area (multi-city, region, nationwide), or the
      supplier's office when no area is declared; else ``location_unconfirmed`` or
      ``location_mismatch``. No delivery city means unspecified (no filter).
    - Brand: optional brands never restrict and are never claimed; a mandatory brand must
      be confirmed in supplier data, else ``brand_mismatch``.

    A supplier qualifying for every line is ``eligible``, for some lines ``partial``;
    only qualifying lines are its invitation scope. No score overrides a failed rule.
    """
    empty = {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [], 'evaluated_suppliers': 0}
    if rfq.status not in {'published', 'collecting_quotes'} or as_utc(rfq.response_deadline) <= datetime.now(timezone.utc):
        return empty
    requested = db.execute(
        select(RFQItem, Material)
        .outerjoin(Material, Material.id == RFQItem.material_id)
        .where(RFQItem.rfq_id == rfq.id)
        .order_by(RFQItem.created_at, RFQItem.id)
    ).all()
    if not requested:
        return empty

    categories = active_categories(db)
    scopes, material_info = cached_item_scopes(db, rfq, requested, categories)
    by_id ={scope['id']: scope for scope in scopes}
    postgres = db.bind.dialect.name == 'postgresql'
    batch_size = max(1, min(limit if limit is not None else 50, 100))

    exact_ids = {s['material_id'] for s in scopes if s['material_id']}
    evidence_ids = sorted(exact_ids | {mid for s in scopes for mid in s['family']})
    sub_scope = {sid for s in scopes for sid in s['capability_subs'] | s['descriptive_subs']}
    scope_mains = {mid for s in scopes for mid in s['scope_mains']}
    # A supplier may declare only a child category. Discover it even when the buyer
    # selected a main category without selecting a particular subcategory.
    broad_mains = {mid for scope in scopes if not scope['sub'] for mid in scope['scope_mains']}
    broad_children = {cid for cid, category in categories.items() if category.parent_id in broad_mains}
    related = {cid for s in scopes for cid in s['related']}
    text_terms: set[str] = set()
    for s in scopes:
        heads = s['tokens'] & PRODUCT_HEADS
        if heads:
            text_terms.update(product_search_terms(heads))
        elif len(s['phrase']) >= 6:
            text_terms.add(s['phrase'])
    discovery_terms = {t for s in scopes if not s['main'] and not s['family']
                       for t in sorted(s['tokens'], key=len, reverse=True)[:4]}
    # Words any supplier text must contain to be product evidence for some line.
    evidence_terms = tuple(sorted({t.casefold() for s in scopes for t in [*product_search_terms(s['tokens']), *s['tokens']]
                                   if len(t) >= 3}))

    def like(column, values):
        return or_(*[column.ilike('%' + v.replace('_', '\\_').replace('%', '\\%') + '%', escape='\\')
                     for v in sorted(values)])

    branches = [select(RFQMatch.supplier_org_id.label('id')).where(RFQMatch.rfq_id == rfq.id)]
    if evidence_ids:
        if postgres:
            # GIN ix_capability_material_links serves this ?| membership test.
            branches.append(select(Capability.org_id.label('id')).where(
                cast(Capability.material_ids, JSONB).has_any(pg_array(evidence_ids))))
        else:
            branches.append(select(Capability.org_id.label('id')).where(
                or_(*[material_link(db, mid) for mid in evidence_ids[:200]])))
        branches.append(select(PriceBookItem.org_id.label('id')).where(
            PriceBookItem.active.is_(True), PriceBookItem.material_id.in_(evidence_ids)))
    if text_terms:
        branches.append(select(PriceBookItem.org_id.label('id')).where(
            PriceBookItem.active.is_(True), like(PriceBookItem.supplier_description, text_terms)))
        branches.append(select(ImportedSupplierTrade.org_id.label('id')).where(
            or_(like(ImportedSupplierTrade.raw_trade, text_terms), like(ImportedSupplierTrade.raw_products, text_terms))))
    if postgres:
        phrases = {s['phrase'] for s in scopes if len(s['phrase']) >= 5}
        if phrases:
            branches.append(select(PriceBookItem.org_id.label('id')).where(
                PriceBookItem.active.is_(True),
                or_(*(PriceBookItem.supplier_description.op('%')(phrase) for phrase in phrases))))
            branches.append(select(ImportedSupplierTrade.org_id.label('id')).where(
                or_(*(ImportedSupplierTrade.raw_products.op('%')(phrase) for phrase in phrases))))
    # Subcategory and main-category capabilities are candidates whatever their source
    # (portal, admin import, bulk import). Indexed by ix_capabilities_cat_elig_org.
    if sub_scope or broad_children:
        branches.append(select(Capability.org_id.label('id')).where(Capability.category_id.in_(sub_scope | broad_children)))
    if scope_mains:
        branches.append(select(Capability.org_id.label('id')).where(Capability.category_id.in_(scope_mains)))
    if discovery_terms:
        branches.append(select(Capability.org_id.label('id')).where(like(Capability.trade_keywords, discovery_terms)))
        branches.append(select(PriceBookItem.org_id.label('id')).where(
            PriceBookItem.active.is_(True), like(PriceBookItem.supplier_description, discovery_terms)))
        branches.append(select(ImportedSupplierTrade.org_id.label('id')).where(
            like(ImportedSupplierTrade.raw_trade, discovery_terms)))

    if supplier_ids is not None:
        candidate_query = select(Organization.id).where(
            Organization.id.in_(supplier_ids), Organization.id != rfq.buyer_org_id).order_by(Organization.id)
    else:
        combined = union(*branches).subquery('candidate_orgs')
        candidate_query = select(combined.c.id).where(combined.c.id != rfq.buyer_org_id)
        if after_supplier_id:
            candidate_query = candidate_query.where(combined.c.id > after_supplier_id)
        candidate_query = candidate_query.order_by(combined.c.id)
        if bounded:
            candidate_query = candidate_query.limit(batch_size + 1)
    candidate_ids = list(db.scalars(candidate_query).all())
    has_more = bounded and supplier_ids is None and len(candidate_ids) > batch_size
    if bounded:
        candidate_ids = candidate_ids[:batch_size]
    if not candidate_ids:
        return empty

    # One query per source for the whole page: no per-supplier round trips.
    orgs = {org.id: org for org in db.scalars(select(Organization).where(Organization.id.in_(candidate_ids)))}
    caps_by_org: dict[str, list[Capability]] = {}
    cap_query = select(Capability).where(Capability.org_id.in_(candidate_ids))
    if all(s['related'] for s in scopes):
        cap_query = cap_query.where(Capability.category_id.in_(related))
    if postgres:
        # Imported capabilities can link hundreds of materials; transfer only the links
        # that are evidence for this RFQ (GIN-indexed) instead of every array.
        cap_query = cap_query.options(defer(Capability.material_ids))
    for cap in db.scalars(cap_query):
        caps_by_org.setdefault(cap.org_id, []).append(cap)
    evidence_set = set(evidence_ids)
    link_counts: dict[str, int] = {}
    category_wide_caps: set[str] = set()
    if postgres:
        cap_links: dict[str, set[str]] = {}
        if evidence_ids:
            for cap_id, linked, total in db.execute(text(
                    'SELECT c.id, ARRAY(SELECT v FROM jsonb_array_elements_text(c.material_ids::jsonb) v '
                    'WHERE v = ANY(:ids)), jsonb_array_length(c.material_ids::jsonb) FROM supplier_capabilities c '
                    'WHERE c.org_id = ANY(:orgs) AND c.material_ids::jsonb ?| :ids'),
                    {'ids': evidence_ids, 'orgs': candidate_ids}):
                cap_links[cap_id] = set(linked)
                link_counts[cap_id] = int(total or 0)
    else:
        cap_links = {cap.id: set(cap.material_ids or []) & evidence_set
                     for caps in caps_by_org.values() for cap in caps}
        link_counts = {cap.id: len(cap.material_ids or []) for caps in caps_by_org.values() for cap in caps}
    # Provenance: a capability linked to (nearly) every active material of its category
    # records category membership, not products the supplier offers. Such links are
    # not product evidence; the capability still counts as category capability.
    linked_caps = {cap.id: cap.category_id for caps in caps_by_org.values() for cap in caps
                   if cap_links.get(cap.id) and link_counts.get(cap.id, 0) >= CATEGORY_WIDE_MIN_LINKS}
    if linked_caps:
        cats = set(linked_caps.values())
        per_category = Counter()
        for column in (Material.category_id, Material.subcategory_id, Material.main_category_id):
            for cid, count in db.execute(select(column, func.count()).where(
                    Material.active.is_(True), column.in_(cats)).group_by(column)).all():
                per_category[cid] = max(per_category[cid], int(count))
        for cap_id, cid in linked_caps.items():
            if per_category[cid] and link_counts[cap_id] >= CATEGORY_WIDE_SHARE * per_category[cid]:
                cap_links[cap_id] = set()
                category_wide_caps.add(cap_id)

    def links(cap: Capability) -> set[str]:
        return cap_links.get(cap.id, set())
    pricebooks_by_org: dict[str, list[PriceBookItem]] = {}
    for pb in db.scalars(select(PriceBookItem).where(PriceBookItem.org_id.in_(candidate_ids), PriceBookItem.active.is_(True))):
        pricebooks_by_org.setdefault(pb.org_id, []).append(pb)
    trades_by_org: dict[str, list] = {}
    # A pending material link still has an approved category capability; its source
    # product wording is evidence. Unmapped source rows have no capability and add nothing.
    trade_query = (select(ImportedSupplierTrade.org_id, ImportedSupplierTrade.raw_trade,
                          ImportedSupplierTrade.raw_products, ImportedSupplierTrade.raw_brands)
                   .where(ImportedSupplierTrade.org_id.in_(candidate_ids),
                          ImportedSupplierTrade.mapping_status != 'unreviewed'))
    for trade in db.execute(trade_query).all():
        trades_by_org.setdefault(trade.org_id, []).append(trade)

    rfq_city_norm = normalize_city(rfq.city)
    required = {value.strip().casefold() for value in (rfq.required_qualifications or []) if value.strip()}

    template_brands: dict[frozenset, set[str]] = {}

    def brand_confirmed(org_id: str, item, entry: dict, wanted: set[str]) -> bool:
        """Requested brands appear in this supplier's saved/imported product data."""
        saved = {b.strip().casefold() for c in entry['caps'] for b in (c.brands or []) if isinstance(b, str) and b.strip()}
        product_ids = frozenset(mid for c in entry['caps'] for mid in links(c)
                                if mid == item.material_id or mid in entry['materials'])
        if product_ids:
            if product_ids not in template_brands:
                template_brands[product_ids] = {
                    str(v).strip().casefold() for template in db.scalars(
                        select(Material.specs_template).where(Material.id.in_(product_ids)))
                    for k, v in (template or {}).items()
                    if str(k).strip().casefold() in {'brand', 'manufacturer'} and isinstance(v, str)}
            saved |= template_brands[product_ids]
        item_main, item_sub = entry['taxonomy']
        saved.update(pb.brand.strip().casefold() for pb in pricebooks_by_org.get(org_id, [])
                     if pb.brand and (pb.material_id == item.material_id or pb.material_id in entry['materials']
                                      or (item_sub and pb.subcategory_id == item_sub)
                                      or (not item_sub and item_main and pb.main_category_id == item_main)))
        for trade in trades_by_org.get(org_id, []):
            saved.update(v.strip().casefold() for v in re.split(r'[,;/|\n]', trade.raw_brands or '') if v.strip())
        # A buyer's comma/or-separated list offers alternatives, not a requirement
        # that the supplier carry every listed brand.
        return bool(wanted & saved)

    children_by_main: dict[str, set[str]] = {}
    for cid, category in categories.items():
        if category.parent_id:
            children_by_main.setdefault(category.parent_id, set()).add(cid)

    def blanket_imported_mains(caps: list[Capability]) -> frozenset:
        """Main categories where admin import mapped this supplier to every subcategory."""
        held: dict[str, set[str]] = {}
        for cap in caps:
            parent = categories[cap.category_id].parent_id
            if parent and cap.mapping_source == 'admin_import':
                held.setdefault(parent, set()).add(cap.category_id)
        return frozenset(main for main, subs in held.items()
                         if len(children_by_main.get(main, ())) >= BLANKET_MIN_SUBCATEGORIES
                         and children_by_main[main] <= subs)

    def approved(cap: Capability) -> bool:
        return bool(cap.confirmed and cap.eligibility_status == 'enabled' and cap.accepts_rfqs
                    and cap.category_id in categories)

    def grade(scope: dict, caps: list[Capability], pbs: list, texts: list[str], blanket_mains: frozenset = frozenset()) -> dict | None:
        """Best evidence for one RFQ line from the given approved capabilities."""
        if scope['blocked']:
            return None
        # Unclassified lines without a Material Master family may use any approved capability.
        in_scope = [c for c in caps if c.category_id in scope['related']] if scope['related'] else list(caps)
        if not in_scope:
            return None
        preferred = [c for c in in_scope if c.category_id in scope['scope_subs']] or in_scope
        mid, family = scope['material_id'], scope['family']
        default_tax = (scope['family_main'] or scope['main'], scope['family_sub'] or scope['sub'])
        request = _product_description(scope['item'].description)
        specs = _product_description(scope['item'].specifications)
        # A selected Material Master record that is technically different from the
        # requested description or specifications (e.g. a daylight floodlight for
        # "RGB Flood Light") was mapped wrongly; its links are not exact evidence.
        if mid and mid in material_info and technical_conflict(request, material_info[mid][0], specs):
            mid = None
        if mid:
            linked = [c for c in in_scope if mid in links(c)]
            if linked or any(pb.material_id == mid for pb in pbs) or (scope['code'] and any(
                    scope['code'] in (pb.supplier_description or '').casefold()
                    or scope['code'] in (pb.model or '').casefold() for pb in pbs)):
                name = material_info.get(mid, (scope['item'].description,))[0]
                return {'level': LEVEL_EXACT, 'caps': linked or preferred, 'sim': 1.0, 'detail': name,
                        'taxonomy': (scope['main'], scope['sub']), 'materials': {mid}}
        phrase = scope['phrase']
        best_link = None
        for cap in in_scope:
            for linked_id in links(cap):
                sim = family.get(linked_id)
                if sim and (best_link is None or sim > best_link[0]):
                    best_link = (sim, linked_id, cap)
        if phrase:
            if best_link and canonical_product_text(material_info[best_link[1]][0]) == phrase:
                return {'level': LEVEL_ALIAS, 'caps': [best_link[2]], 'sim': 1.0, 'detail': material_info[best_link[1]][0],
                        'taxonomy': material_info[best_link[1]][1:3], 'materials': {best_link[1]}}
            alias = next((text for text, canonical, _ in texts if canonical == phrase), None)
            if alias:
                return {'level': LEVEL_ALIAS, 'caps': preferred, 'sim': 1.0, 'detail': alias.strip(),
                        'taxonomy': default_tax, 'materials': set()}
        candidates = []
        if best_link:
            candidates.append((best_link[0], material_info[best_link[1]][0], [c for c in in_scope if best_link[1] in links(c)],
                               material_info[best_link[1]][1:3], {m for c in in_scope for m in links(c) if m in family}))
        for pb in pbs:
            pb_pair = (pb.main_category_id, pb.subcategory_id)
            if pb.material_id in material_info:
                pb_pair = material_info[pb.material_id][1:3]
            if scope['main'] and pb_pair[0] not in scope['scope_mains']:
                continue
            if pb.material_id and pb.material_id in family and not technical_conflict(request, pb.supplier_description, specs):
                candidates.append((family[pb.material_id], pb.supplier_description or material_info[pb.material_id][0],
                                   preferred, material_info[pb.material_id][1:3], {pb.material_id}))
        text_best = max(((family_similarity(scope['tokens'], tokens), text) for text, _, tokens in texts
                         if not technical_conflict(request, text, specs)), default=(0.0, ''))
        if text_best[0]:
            candidates.append((text_best[0], text_best[1].strip(), preferred, default_tax, set()))
        if candidates:
            sim, detail, support, taxonomy, materials = max(candidates, key=lambda c: c[0])
            return {'level': LEVEL_FAMILY, 'caps': support, 'sim': sim, 'detail': detail,
                    'taxonomy': taxonomy, 'materials': materials}
        # Subcategory capability without product evidence: a subcategory match. An
        # imported supplier mapped to EVERY subcategory of the main category is not
        # subcategory evidence (that is discipline-wide), so it is a broad match only.
        sub_caps = [c for c in in_scope if c.category_id in scope['capability_subs'] | scope['descriptive_subs']]
        specific = [c for c in sub_caps
                    if not (c.mapping_source == 'admin_import' and categories[c.category_id].parent_id in blanket_mains)]
        if specific:
            # Prefer the category the line restates, then the buyer's own subcategory.
            specific.sort(key=lambda c: (c.category_id not in scope['generic_cats'], c.category_id != scope['sub']))
            sub = specific[0].category_id
            return {'level': LEVEL_SUB, 'caps': specific, 'sim': 0.0, 'detail': categories[sub].name_en,
                    'taxonomy': (categories[sub].parent_id, sub), 'materials': set()}
        # Main-category capability (or blanket import): broader recommendation that
        # requires product confirmation; never auto-invited.
        root_caps = [c for c in in_scope if c.category_id in scope['scope_mains']] or sub_caps
        if not root_caps and scope['main'] and not scope['sub']:
            root_caps = [c for c in in_scope if categories[c.category_id].parent_id == scope['main']]
        if root_caps:
            main = root_caps[0].category_id if root_caps[0].category_id in scope['scope_mains'] \
                else categories[root_caps[0].category_id].parent_id
            return {'level': LEVEL_MAIN, 'caps': root_caps, 'sim': 0.0, 'detail': categories[main].name_en,
                    'taxonomy': (main, None), 'materials': set()}
        return None

    eligible: dict[str, dict] = {}
    ineligible: dict[str, dict] = {}
    for org_id in candidate_ids:
        org = orgs.get(org_id)
        if not org:
            continue
        caps = caps_by_org.get(org_id, [])
        if not org.is_active or not org.is_supplier:
            ineligible[org_id] = {'org': org, 'status': 'inactive',
                                  'reasons': ['Supplier organization is inactive or not marked as supplier.']}
            continue
        if org.verified_status in {'rejected', 'suspended'}:
            ineligible[org_id] = {'org': org, 'status': org.verified_status,
                                  'reasons': [f'Supplier organization is {org.verified_status} and excluded from RFQ matching.']}
            continue
        approved_caps = [c for c in caps if approved(c)]
        if not approved_caps:
            if caps:
                reason = ('Capability was rejected by administration.' if any(c.eligibility_status == 'rejected' for c in caps)
                          else 'Capability pending administrative review/approval.')
                ineligible[org_id] = {'org': org, 'status': 'pending_approval', 'reasons': [reason]}
            else:
                ineligible[org_id] = {'org': org, 'status': 'ineligible', 'reasons': [
                    'No approved capability in the requested category for: '
                    + '; '.join(s['item'].description for s in scopes) + '.']}
            continue
        pbs = pricebooks_by_org.get(org_id, [])
        blanket = blanket_imported_mains(approved_caps)
        # A capability without a declared service area is covered when the supplier's own
        # office is in the delivery city; otherwise its coverage stays unconfirmed.
        local_office = bool(rfq_city_norm) and normalize_city(org.city) == rfq_city_norm
        covering = [c for c in approved_caps if city_covered_by(rfq.city, c.city)[0]
                    or (local_office and not (c.city or '').strip())]
        blank = [c for c in approved_caps if not (c.city or '').strip()]
        items: dict[str, dict] = {}
        mismatched: dict[str, dict] = {}
        for scope in scopes:
            scoped_pbs = [pb for pb in pbs if (
                pb.material_id == scope['material_id'] or pb.material_id in scope['family'] or
                pb.main_category_id in scope['scope_mains'] or pb.subcategory_id in scope['scope_subs'])]
            scoped_caps = [cap for cap in approved_caps if cap.category_id in scope['related']]
            texts = _evidence_texts(trades_by_org.get(org_id, []), scoped_pbs, scoped_caps, evidence_terms)
            found = grade(scope, covering, scoped_pbs, texts, blanket) if covering else None
            if found:
                found['coverage'] = 'covered'
            elif rfq_city_norm and blank and (found := grade(scope, blank, scoped_pbs, texts, blanket)):
                found['coverage'] = 'unknown'
            if found:
                items[scope['id']] = found
            elif rfq_city_norm and (other := grade(scope, approved_caps, scoped_pbs, texts, blanket)):
                mismatched[scope['id']] = other
        if not items:
            if mismatched:
                cities = sorted({c.city.strip() for e in mismatched.values() for c in e['caps'] if c.city and c.city.strip()})
                ineligible[org_id] = {'org': org, 'status': 'location_mismatch', 'reasons': [
                    f"Delivery location not covered: supplier serves '{', '.join(cities) or org.city or 'None'}', RFQ requires '{rfq.city}'."]}
            else:
                held = sorted({taxonomy_label(categories, categories[c.category_id].parent_id, c.category_id)
                               if categories[c.category_id].parent_id else categories[c.category_id].name_en
                               for c in approved_caps})
                ineligible[org_id] = {'org': org, 'status': 'ineligible', 'reasons': [
                    'No approved material, subcategory or main-category capability supports the requested items.',
                    f"Approved capabilities ({', '.join(held[:4])}{'…' if len(held) > 4 else ''}) are outside the "
                    f"requested categories for: {'; '.join(s['item'].description for s in scopes)}."]}
            continue
        brand_missing = []
        for iid in list(items):
            brand = requested_brand(by_id[iid]['item'])
            if brand is None:
                continue  # No brand requested: no brand restriction.
            confirmed = brand_confirmed(org_id, by_id[iid]['item'], items[iid], brand[1])
            items[iid]['brand'] = (brand[0], confirmed)
            if brand[2] and not confirmed:
                brand_missing.append(f"{by_id[iid]['item'].description} (mandatory brand: {brand[0]})")
                del items[iid]
        if not items:
            ineligible[org_id] = {'org': org, 'status': 'brand_mismatch', 'reasons': [
                'Mandatory brand not confirmed in supplier product data for: ' + '; '.join(brand_missing) + '.']}
            continue
        evidence_caps = {id(c): c for e in items.values() for c in e['caps']}.values()
        supplier_quals = {q.strip().casefold() for c in evidence_caps for q in (c.qualifications or [])}
        if required and not required.issubset(supplier_quals):
            ineligible[org_id] = {'org': org, 'status': 'ineligible', 'reasons': ['Missing required project qualifications.']}
            continue
        coverage_unknown = bool(rfq_city_norm) and not any(e['coverage'] == 'covered' for e in items.values())
        exact_city = any(city_covered_by(rfq.city, c.city)[1] for e in items.values() if e['coverage'] == 'covered' for c in e['caps'])
        eligible[org_id] = {'org': org, 'items': items, 'coverage_unknown': coverage_unknown,
                            'has_exact_city': exact_city, 'ai_explanations': {}, 'brand_missing': brand_missing,
                            'office_city': local_office and not any(
                                city_covered_by(rfq.city, c.city)[0] for e in items.values() for c in e['caps'])}

    # Optional AI-assisted matching, only for lines with no taxonomy and no Material Master family.
    unresolved = [s for s in scopes if not s['main'] and not s['family']
                  and not any(s['id'] in c['items'] for c in eligible.values())]
    settings = get_settings()
    if (semantic_enabled and unresolved and not evaluate_only and getattr(settings, 'gemini_enabled', True)
            and settings.gemini_api_key.get_secret_value()):
        pool = {oid: orgs[oid] for oid in candidate_ids if oid not in eligible and oid in orgs
                and orgs[oid].is_active and orgs[oid].is_supplier and orgs[oid].verified_status not in {'rejected', 'suspended'}
                and any(approved(c) and city_covered_by(rfq.city, c.city)[0] for c in caps_by_org.get(oid, []))}
        payload = [{'supplier_org_id': oid, 'categories': sorted({categories[c.category_id].name_en for c in caps_by_org.get(oid, []) if c.category_id in categories})[:10],
                    'trade_keywords': ' | '.join(c.trade_keywords for c in caps_by_org.get(oid, []) if c.trade_keywords)[:300],
                    'catalog_items': [pb.supplier_description for pb in pricebooks_by_org.get(oid, []) if pb.supplier_description][:10],
                    'description': (org.description or '')[:240]} for oid, org in pool.items()]
        if payload:
            unresolved_payload = [{'item_id': s['id'], 'description': s['item'].description,
                                   'specifications': s['item'].specifications or '', 'category': ''}
                                  for s in unresolved]
            ai_matches = {}
            for item_start in range(0, len(unresolved_payload), 20):
                for supplier_start in range(0, len(payload), 40):
                    hits, _failed = gemini_semantic_supplier_match(
                        unresolved_payload[item_start:item_start + 20], payload[supplier_start:supplier_start + 40])
                    for item_id, entries in hits.items():
                        ai_matches.setdefault(item_id, []).extend(entries)
            for item_id, hits in ai_matches.items():
                for hit in hits:
                    oid = hit['supplier_org_id']
                    if oid not in pool or item_id not in by_id:
                        continue
                    ineligible.pop(oid, None)
                    caps = [c for c in caps_by_org.get(oid, []) if approved(c) and city_covered_by(rfq.city, c.city)[0]]
                    entry = eligible.setdefault(oid, {'org': pool[oid], 'items': {}, 'coverage_unknown': False, 'office_city': False,
                                                      'has_exact_city': any(city_covered_by(rfq.city, c.city)[1] for c in caps),
                                                      'ai_explanations': {}})
                    entry['items'][item_id] = {'level': LEVEL_AI, 'caps': caps, 'sim': 0.0, 'detail': hit['explanation'],
                                               'taxonomy': (None, None), 'materials': set(), 'coverage': 'covered'}
                    entry['ai_explanations'][item_id] = f"{hit['explanation']} (confidence {hit['confidence']:.2f})"

    def qualifies(scope: dict, e: dict) -> bool:
        """Final per-line eligibility: covered delivery and product evidence, or the most
        specific category capability when the line names no specific product."""
        if e['coverage'] != 'covered':
            return False
        if e['level'] in PRODUCT_LEVELS:
            return True
        if e['level'] == LEVEL_SUB:
            return e['taxonomy'][1] in scope['generic_cats']
        return e['level'] == LEVEL_MAIN and not scope['sub'] and e['taxonomy'][0] in scope['generic_cats']

    order = {s['id']: n for n, s in enumerate(scopes)}
    for oid, candidate in list(eligible.items()):
        found = candidate['items']
        qualifying = {iid: e for iid, e in found.items() if qualifies(by_id[iid], e)}
        if qualifying:
            candidate['items'] = qualifying
            continue
        # Candidate only: retained for admin diagnostics, never shown, counted or invited.
        del eligible[oid]
        ids = sorted(found, key=order.get)
        lines = '; '.join(by_id[iid]['item'].description for iid in ids)
        markers = [f"{LEVEL_MARKER[found[iid]['level']]}{iid}" for iid in ids]
        if candidate.get('brand_missing'):
            status, reason = 'brand_mismatch', ('Mandatory brand not confirmed in supplier product data for: '
                                                + '; '.join(candidate['brand_missing']) + '.')
        elif any(qualifies(by_id[iid], {**e, 'coverage': 'covered'}) for iid, e in found.items()):
            status, reason = 'location_unconfirmed', (f'Product evidence found for: {lines}; delivery coverage for '
                                                      f'{rfq.city} is not declared by the supplier.')
        elif any(e['level'] == LEVEL_AI for e in found.values()):
            status, reason = 'insufficient_evidence', f'AI suggestion only; supplier product evidence is required for: {lines}.'
        else:
            kind = 'subcategory' if any(e['level'] == LEVEL_SUB for e in found.values()) else 'main category'
            status, reason = 'insufficient_evidence', (f'Category capability only ({kind}); no supplier product, '
                                                       f'price-list or material evidence for: {lines}.')
        ineligible[oid] = {'org': candidate['org'], 'status': status, 'reasons': [reason, *markers]}

    total = max(1, len(scopes))
    for candidate in eligible.values():
        items = candidate['items']
        candidate['auto_invite'] = True
        candidate['score'] = min(100, 50 + int(sum(LEVEL_WEIGHT[e['level']] + 4 * e['sim'] for e in items.values()) / total)
                                 + (5 if candidate['has_exact_city'] else 0) - (3 if candidate['coverage_unknown'] else 0))
    ranked = sorted(eligible.values(), key=lambda c: (
        not c['auto_invite'], -c['score'], -len(c['items']), c['coverage_unknown'], c['org'].name.casefold(), c['org'].id))

    if evaluate_only:
        return {
            'eligible_ids': [c['org'].id for c in ranked],
            'auto_invite_ids': [c['org'].id for c in ranked if c['auto_invite']],
            'evaluated_suppliers': len(candidate_ids),
            'ineligible_by_status': dict(Counter(e['status'] for e in ineligible.values())),
            'ineligible_reasons': dict(Counter(e['reasons'][0] for e in ineligible.values() if e['reasons']).most_common(10)),
            'ineligible_detail': {oid: f"{e['status']}: {e['reasons'][0] if e['reasons'] else ''}".strip()
                                  for oid, e in ineligible.items()},
            # Per RFQ line: suppliers at each (strongest) evidence level.
            'item_levels': {s['id']: dict(Counter(c['items'][s['id']]['level'] for c in ranked if s['id'] in c['items']))
                            for s in scopes},
            'covered_items': {c['org'].id: sorted(c['items']) for c in ranked},
            'match_levels': {c['org'].id: [level for level in LEVEL_ORDER if any(e['level'] == level for e in c['items'].values())]
                             for c in ranked},
            # Recipient routing prefers contacts responsible for the evidence that matched.
            'item_evidence': {c['org'].id: {iid: {'material_ids': sorted(e['materials']),
                                                   'category_ids': sorted({cap.category_id for cap in e['caps']})}
                                             for iid, e in c['items'].items()} for c in ranked},
        }

    touched = set(candidate_ids) | set(eligible)
    existing = {m.supplier_org_id: m for m in db.scalars(
        select(RFQMatch).where(RFQMatch.rfq_id == rfq.id, RFQMatch.supplier_org_id.in_(touched)))}
    for obj in db.new:
        if isinstance(obj, RFQMatch) and obj.rfq_id == rfq.id:
            existing[obj.supplier_org_id] = obj

    for candidate in ranked:
        items = candidate['items']
        best = min(items.values(), key=lambda e: LEVEL_RANK[e['level']])['level']
        if candidate['coverage_unknown']:
            coverage_reason = 'delivery coverage unconfirmed'
        elif candidate['has_exact_city']:
            coverage_reason = f'delivery city match: {rfq.city}'
        elif rfq_city_norm:
            coverage_reason = 'service coverage matches delivery requirement'
        else:
            coverage_reason = 'delivery location not specified; city coverage not used'
        reasons = ['active supplier company', LEVEL_LABEL[best], coverage_reason, f'coverage:{len(items)}/{len(scopes)}']
        if exact_ids:
            matched_exact = {by_id[iid]['material_id'] for iid, e in items.items() if e['level'] == LEVEL_EXACT}
            reasons.append(f'exact_materials:{len(matched_exact)}/{len(exact_ids)}')
        reasons.extend(f'item:{iid}' for iid in sorted(items))
        for level in LEVEL_ORDER:
            reasons.extend(f'{LEVEL_MARKER[level]}{iid}' for iid in sorted(items) if items[iid]['level'] == level)
        reasons.extend(f'taxonomy:{iid}={e["taxonomy"][0] or ""}/{e["taxonomy"][1] or ""}' for iid, e in sorted(items.items()))
        if candidate['office_city']:
            reasons.append(f'Supplier office is in {rfq.city}; delivery to the office city is accepted as coverage.')
        for iid, e in sorted(items.items()):
            label = taxonomy_label(categories, *e['taxonomy'])
            detail = candidate['ai_explanations'].get(iid) or e['detail']
            reasons.append(f"{LEVEL_LABEL[e['level']]}: {by_id[iid]['item'].description}"
                           + (f' — {detail}' if detail and detail != by_id[iid]['item'].description else '')
                           + (f' ({label})' if label else '') + '.')

        brand_requests = [e['brand'][0] for _, e in sorted(items.items()) if e.get('brand')]
        brand_evidence = [e['brand'][1] for _, e in sorted(items.items()) if e.get('brand')]
        if brand_requests and all(brand_evidence):
            reasons.append('Brand requirement matches approved saved product data: ' + ', '.join(brand_requests)
                           + '. Availability and brand authorization require quotation confirmation.')
        else:
            reasons.append('Brand compliance is unverified' + (': ' + ', '.join(brand_requests) if brand_requests else '')
                           + '; requested brands must be confirmed in the supplier quotation.')
        reasons.append('Product evidence does not confirm technical compliance; confirm in the quotation')

        # Items holds qualifying lines only: full coverage means every RFQ line qualifies.
        status = 'eligible' if len(items) == len(scopes) else 'partial'
        match = existing.get(candidate['org'].id)
        if match:
            match.score, match.reasons, match.status = candidate['score'], reasons, status
        else:
            db.add(RFQMatch(rfq_id=rfq.id, supplier_org_id=candidate['org'].id, score=candidate['score'],
                            reasons=reasons, status=status))

    for org_id, entry in ineligible.items():
        match = existing.get(org_id)
        reasons = list(entry['reasons'])
        if match:
            if match.status in MATCHED_STATUSES:
                from app.security import audit
                audit(db, actor, 'rfq.match_eligibility_corrected', rfq.buyer_org_id, match.id,
                      previous_status=match.status, status=entry['status'], supplier_org_id=org_id,
                      historical_invitation_preserved=True)
            match.score, match.reasons, match.status = 0, reasons, entry['status']
        else:
            db.add(RFQMatch(rfq_id=rfq.id, supplier_org_id=org_id, score=0, reasons=reasons, status=entry['status']))

    def invited_evidence(candidate: dict) -> dict:
        # Recipient routing reads this instead of re-running matching per notification.
        return {iid: {'material_ids': sorted(e['materials']), 'category_ids': sorted({cap.category_id for cap in e['caps']})}
                for iid, e in candidate['items'].items()}

    def invited_items(candidate: dict) -> list[str]:
        # Only qualifying lines are the supplier's quotation scope.
        return sorted(candidate['items'])

    # Suppliers that no longer qualify: unsent invitations are superseded (kept, marked
    # cancelled, queued messages cancelled); delivered or answered ones are preserved.
    superseded = supersede_invitations(db, rfq, {oid: entry['reasons'][0] for oid, entry in ineligible.items()
                                                 if entry['reasons']}, actor)

    # One invitation per supplier per RFQ (uq_invitation); reruns only refresh item scope.
    invitable = [c for c in ranked if c['auto_invite']]
    invited = set(db.scalars(select(Invitation.supplier_org_id).where(
        Invitation.rfq_id == rfq.id, Invitation.supplier_org_id.in_([c['org'].id for c in ranked])))) if ranked else set()
    for obj in db.new:
        if isinstance(obj, Invitation) and obj.rfq_id == rfq.id:
            invited.add(obj.supplier_org_id)
    reinstated = []
    if invited:
        for old in db.scalars(select(Invitation).where(Invitation.rfq_id == rfq.id, Invitation.supplier_org_id.in_(invited))):
            candidate = eligible.get(old.supplier_org_id)
            if candidate and old.status in {'invited', 'accepted'}:
                old.matched_item_ids = invited_items(candidate)
                old.match_evidence = invited_evidence(candidate)
            elif candidate and old.status == 'cancelled':
                # Qualifies again after a correction: the never-delivered invitation is reinstated.
                old.status, old.cancelled_at, old.cancellation_reason = 'invited', None, None
                old.matched_item_ids = invited_items(candidate)
                old.match_evidence = invited_evidence(candidate)
                reinstated.append(old)
    invitations = [Invitation(rfq_id=rfq.id, supplier_org_id=c['org'].id, status='invited',
                              matched_item_ids=invited_items(c), match_evidence=invited_evidence(c))
                   for c in invitable if c['org'].id not in invited]
    db.add_all(invitations)
    if invitations:
        db.flush()
        db.add_all([Outbox(event='rfq.invitation', dedupe_key=f'rfq-invitation:{invitation.id}',
                           payload={'rfq_id': rfq.id, 'supplier_org_id': invitation.supplier_org_id,
                                    'invitation_id': invitation.id}) for invitation in invitations])
    for old in reinstated:
        db.add(Outbox(event='rfq.invitation', dedupe_key=f'rfq-invitation:{old.id}:reinstated:{uuid4().hex}',
                      payload={'rfq_id': rfq.id, 'supplier_org_id': old.supplier_org_id, 'invitation_id': old.id}))
    if invitations:
        # Route in-app alerts through the same responsibility mapping as email/SMS.
        from app.modules.notifications.recipients import queue_invitation_in_app
        db.flush()
        member_orgs = set(db.scalars(select(Member.organization_id).join(User, User.id == Member.user_id).where(
            Member.organization_id.in_([inv.supplier_org_id for inv in invitations]), User.is_active.is_(True))))
        for invitation in invitations:
            if invitation.supplier_org_id in member_orgs:
                queue_invitation_in_app(db, invitation, rfq)
    if invitations or invited:
        rfq.status = 'collecting_quotes'

    return {
        'next_cursor': candidate_ids[-1] if has_more else None,
        'eligible_suppliers': len(ranked),
        'new_invitations': len(invitations),
        'invitations': invitations,
        'evaluated_suppliers': len(touched),
        'ineligible_suppliers': len(ineligible),
        'superseded_invitations': superseded,
    }


RECEIVED_DELIVERY_STATUSES = ('processed', 'awaiting_status', 'uncertain')


def supersede_plan(db, rfq, org_ids) -> dict:
    """Read-only: which active invitations of these suppliers would be cancelled or preserved.

    Preserved: delivered (provider accepted/uncertain), answered (non-draft quotation) or
    already accepted/declined. Cancelled: still 'invited' and never delivered. Also
    returns the unsent queued invitation/delivery events that cancelling would stop.
    """
    from app.models import Quotation
    empty = {'cancel': [], 'preserve': [], 'deliveries': [], 'unsent_events': 0}
    if not org_ids:
        return empty
    candidates = db.scalars(select(Invitation).where(
        Invitation.rfq_id == rfq.id, Invitation.status.in_({'invited', 'accepted', 'declined'}),
        Invitation.supplier_org_id.in_(list(org_ids)))).all()
    if not candidates:
        return empty
    ids = [inv.supplier_org_id for inv in candidates]
    answered = set(db.scalars(select(Quotation.supplier_org_id).where(
        Quotation.rfq_id == rfq.id, Quotation.supplier_org_id.in_(ids), Quotation.status != 'draft')))
    deliveries = db.scalars(select(Outbox).where(
        Outbox.event == 'notification.delivery', Outbox.payload['context']['rfq_id'].as_string() == rfq.id,
        Outbox.payload['context']['org_id'].as_string().in_(ids))).all()
    received = {e.payload['context']['org_id'] for e in deliveries if e.status in RECEIVED_DELIVERY_STATUSES}
    cancel = [inv for inv in candidates if inv.status == 'invited'
              and inv.supplier_org_id not in answered and inv.supplier_org_id not in received]
    cancel_orgs = {inv.supplier_org_id for inv in cancel}
    unsent = sum(1 for e in deliveries if e.payload['context']['org_id'] in cancel_orgs and e.status in {'pending', 'retry'})
    if cancel:
        unsent += int(db.scalar(select(func.count()).select_from(Outbox).where(
            Outbox.event == 'rfq.invitation', Outbox.status.in_({'pending', 'retry'}),
            Outbox.payload['invitation_id'].as_string().in_([inv.id for inv in cancel]))) or 0)
    return {'cancel': cancel, 'preserve': [inv for inv in candidates if inv.supplier_org_id not in cancel_orgs],
            'deliveries': deliveries, 'unsent_events': unsent}


def supersede_invitations(db, rfq, reasons_by_org: dict[str, str], actor=None) -> int:
    """Cancel never-delivered invitations of suppliers that no longer qualify.

    An invitation is preserved unchanged when the supplier already received it (an
    accepted/uncertain external delivery), responded (accepted/declined) or has a
    non-draft quotation. Otherwise it is kept as a 'cancelled' record with the reason,
    and its queued invitation and delivery events are cancelled before sending.
    Returns the number of invitations cancelled.
    """
    if not reasons_by_org:
        return 0
    from app.security import audit
    plan = supersede_plan(db, rfq, list(reasons_by_org))
    deliveries = plan['deliveries']
    now = datetime.now(timezone.utc)
    cancelled = 0
    for inv in plan['cancel']:
        reason = reasons_by_org[inv.supplier_org_id][:200]
        inv.status, inv.cancelled_at, inv.cancellation_reason = 'cancelled', now, reason
        for event in db.scalars(select(Outbox).where(
                Outbox.event == 'rfq.invitation', Outbox.payload['invitation_id'].as_string() == inv.id,
                Outbox.status.in_({'pending', 'retry'}))):
            event.status, event.delivery_status, event.next_attempt_at = 'skipped', 'superseded_not_eligible', None
        for event in deliveries:
            if event.payload['context']['org_id'] == inv.supplier_org_id and event.status in {'pending', 'retry'}:
                event.status, event.delivery_status, event.next_attempt_at = 'skipped', 'superseded_not_eligible', None
        audit(db, actor, 'rfq.invitation_superseded', rfq.buyer_org_id, inv.id, rfq_id=rfq.id,
              supplier_org_id=inv.supplier_org_id, reason=reason)
        cancelled += 1
    return cancelled


def eligible_supplier_ids(db, rfq, org_ids):
    """Use the matching policy for delivery/reminders/quotation drafts without writing or inviting.

    Only invitable (non-broad) matches qualify: a broad main-category recommendation is
    shown to the buyer but never receives invitation email/SMS or an automatic draft.
    """
    return {oid for oid, reason in supplier_eligibility(db, rfq, org_ids).items() if reason is None}


BROAD_ONLY_REASON = ('review_required: only main-category, AI-suggested or unconfirmed delivery-city evidence; '
                     'shown to the buyer for review, never invited automatically')
NOT_CANDIDATE_REASON = 'not_matched: no capability, product or price-list evidence for any RFQ line'


def supplier_eligibility(db, rfq, org_ids) -> dict[str, str | None]:
    """Each supplier's invitation eligibility under the matching policy: None, or the exact reason.

    Results are kept on the session for the current outbox event only (the worker
    clears ``db.info['eligibility_memo']`` per event), so the quotation-draft and
    notification steps of one invitation job evaluate the supplier once.
    """
    return {oid: entry['reason'] for oid, entry in supplier_evaluations(db, rfq, org_ids).items()}


def supplier_evaluations(db, rfq, org_ids) -> dict[str, dict]:
    """Per supplier: eligibility ``reason`` (None when invitable), ``covered_items`` and
    ``item_evidence`` (for recipient routing), evaluated once per outbox event."""
    memo = db.info.setdefault('eligibility_memo', {})
    ids = [oid for oid in dict.fromkeys(org_ids) if (rfq.id, oid) not in memo]
    for start in range(0, len(ids), 100):
        page = ids[start:start + 100]
        result = _match_batch(db, rfq, evaluate_only=True, supplier_ids=page)
        auto = set(result.get('auto_invite_ids', []))
        shown = set(result.get('eligible_ids', []))
        detail = result.get('ineligible_detail', {})
        for oid in page:
            memo[(rfq.id, oid)] = {
                'reason': (None if oid in auto else BROAD_ONLY_REASON if oid in shown
                           else detail.get(oid, NOT_CANDIDATE_REASON)),
                'covered_items': result.get('covered_items', {}).get(oid, []),
                'item_evidence': result.get('item_evidence', {}).get(oid, {}),
            }
    return {oid: memo[(rfq.id, oid)] for oid in org_ids}


def run_matching_job(bind, job_id: str, *, time_budget: float | None, restart: bool = False,
                     leased: bool = False) -> dict | None:
    """Advance one durable rfq.published job: the only way any path runs RFQ matching.

    Publication (inline, short time budget), the outbox worker (``leased``, longer
    budget) and the repair script (no budget) all call this, so every path runs the
    same matcher pages, queries and eligibility rules. Each page of suppliers is
    matched, persisted and checkpointed (cursor, duration, evaluated count) in its
    own short transaction, so an interruption never repeats or loses finished pages.

    Not leased: a job the worker holds is skipped (SKIP LOCKED) and None is returned;
    the worker completes it. Leased: the worker already marked the job 'processing'.
    ``restart`` ignores a saved cursor once, which repairs runs paged by an older matcher.
    Returns the summed page results with ``next_cursor`` set while pages remain.
    """
    from sqlalchemy.orm import sessionmaker
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    started = time.monotonic()
    summary: dict | None = None
    first = True
    while True:
        with sessions() as db:
            try:
                job = db.scalar(select(Outbox).where(
                    Outbox.id == job_id, Outbox.status.in_({'processing'} if leased else {'pending', 'retry'}))
                    .with_for_update(skip_locked=True))
                rfq = db.get(RFQ, job.payload.get('rfq_id')) if job else None
                if job is None or rfq is None or rfq.status not in {'published', 'collecting_quotes'}:
                    db.rollback()
                    return summary
                page_started = time.monotonic()
                fresh = restart and first
                page = match_and_invite(db, rfq, bounded=True, single_batch=True,
                                        limit=get_settings().matching_batch_size,
                                        after_supplier_id=None if fresh else job.payload.get('cursor'))
                cursor = page.get('next_cursor')
                payload = {key: value for key, value in job.payload.items() if key != 'cursor'}
                previous_ms = 0 if fresh else float(job.payload.get('matching_duration_ms') or 0)
                previous_evaluated = 0 if fresh else int(job.payload.get('evaluated_suppliers') or 0)
                payload.update(progressed_at=datetime.now(timezone.utc).isoformat(),
                               matching_duration_ms=round(previous_ms + (time.monotonic() - page_started) * 1000, 1),
                               evaluated_suppliers=previous_evaluated + page.get('evaluated_suppliers', 0))
                if cursor:
                    payload['cursor'] = cursor
                else:
                    payload['completed_at'] = payload['progressed_at']
                job.payload = payload
                over_budget = time_budget is not None and time.monotonic() - started >= time_budget
                # A leased job stays 'processing' between its own pages; anything left
                # when the budget runs out returns to 'pending' for the next worker poll.
                job.status = 'processed' if not cursor else ('processing' if leased and not over_budget else 'pending')
                job.next_attempt_at = None
                job.last_error = None
                db.commit()
            except Exception:
                db.rollback()
                raise
        first = False
        if summary is None:
            summary = {'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [],
                       'evaluated_suppliers': 0, 'ineligible_suppliers': 0}
        for key in ('eligible_suppliers', 'new_invitations', 'evaluated_suppliers', 'ineligible_suppliers'):
            summary[key] += page.get(key, 0)
        summary['invitations'].extend(page.get('invitations', []))
        summary['next_cursor'] = cursor
        if not cursor or (time_budget is not None and time.monotonic() - started >= time_budget):
            return summary
