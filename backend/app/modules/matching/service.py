"""Set-based hybrid matching for active suppliers under the configured capability policy."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import time
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import cast, func, literal, or_, select, text, union, union_all, update
from sqlalchemy.dialects.postgresql import JSONB, array as pg_array, insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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

log = logging.getLogger(__name__)

LEVEL_EXACT = 'exact material'
LEVEL_ALIAS = 'product alias'
LEVEL_FAMILY = 'product family'
LEVEL_SUB = 'subcategory'
LEVEL_MAIN = 'main category'
LEVEL_AI = 'AI-assisted semantic match'
LEVEL_ORDER = (LEVEL_EXACT, LEVEL_ALIAS, LEVEL_FAMILY, LEVEL_SUB, LEVEL_MAIN, LEVEL_AI)
LEVEL_RANK = {level: rank for rank, level in enumerate(LEVEL_ORDER)}
PRODUCT_LEVELS = frozenset({LEVEL_EXACT, LEVEL_ALIAS, LEVEL_FAMILY})
AUTO_INVITE_LEVELS = PRODUCT_LEVELS  # legacy name
MATCHED_STATUSES = ('eligible', 'partial')
REVIEW_STATUSES = ('insufficient_evidence', 'location_unconfirmed')
LEVEL_WEIGHT = {LEVEL_EXACT: 30, LEVEL_ALIAS: 27, LEVEL_FAMILY: 24, LEVEL_SUB: 18, LEVEL_MAIN: 8, LEVEL_AI: 8}
LEVEL_MARKER = {
    LEVEL_EXACT: 'exact_material_item:',
    LEVEL_ALIAS: 'alias_item:',
    LEVEL_FAMILY: 'family_item:',
    LEVEL_SUB: 'exact_subcategory_item:',
    LEVEL_MAIN: 'main_category_item:',
    LEVEL_AI: 'ai_semantic_item:',
}
LEVEL_LABEL = {
    LEVEL_EXACT: 'Approved exact material capability',
    LEVEL_ALIAS: 'Approved product alias match',
    LEVEL_FAMILY: 'Approved product family match',
    LEVEL_SUB: 'Approved exact subcategory capability',
    LEVEL_MAIN: 'Approved main category capability',
    LEVEL_AI: 'AI-assisted semantic match based on verified supplier profile',
}
BLANKET_MIN_SUBCATEGORIES = 3
CATEGORY_WIDE_MIN_LINKS = 20
CATEGORY_WIDE_SHARE = 0.8
INTERNAL_REASON_PREFIXES = (
    'item:', 'coverage:', 'exact_materials:', 'taxonomy:', 'broad_only',
    'fuzzy_item:', 'keyword_item:', *LEVEL_MARKER.values(),
)
UNBOUNDED_CHUNK_SIZE = 2500
BULK_WRITE_CHUNK = 500

BRAND_CLAUSE = re.compile(r'\bbrands?\s*:\s*([^;\n]+)', re.I)
MANDATORY_BRAND = re.compile(
    r'\b(?:only|mandatory|strictly|no\s+(?:alternatives?|equivalents?|substitut\w*))\b|\(\s*must\s*\)',
    re.I,
)


def requested_brand(item) -> tuple[str, set[str], bool] | None:
    """(display text, wanted brands, mandatory) from the RFQ line, or None when no brand is requested."""
    explicit = (getattr(item, 'required_brand', '') or '').strip()
    if explicit:
        names = {v.strip().casefold() for v in re.split(r'[,/|]|\bor\b', explicit) if v.strip()}
        return explicit, names, True
    raw_text = f'{item.description}\n{item.specifications or ""}'
    found = BRAND_CLAUSE.search(raw_text)
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


def rfq_fingerprint(rfq: RFQ, items: list) -> str:
    """Deterministic fingerprint of an RFQ's matching-relevant fields and lines."""
    payload = {
        'rev': int(getattr(rfq, 'revision', 0) or 0),
        'city': rfq.city or '',
        'country': getattr(rfq, 'delivery_country', '') or '',
        'quals': sorted(rfq.required_qualifications or []),
        'items': [
            (
                i.id,
                i.material_id or '',
                i.main_category_id or '',
                i.subcategory_id or '',
                i.description or '',
                i.specifications or '',
                getattr(i, 'discipline', '') or '',
                getattr(i, 'required_brand', '') or '',
            )
            for i in items
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:24]


def match_and_invite(
    db: Session,
    rfq: RFQ,
    actor: User | None = None,
    limit: int | None = None,
    after_supplier_id: str | None = None,
    bounded: bool = False,
    single_batch: bool = False,
    time_budget: float | None = None,
    changed_item_ids: set[str] | None = None,
    expected_revision: int | None = None,
    skip_evaluated_revision: int | None = None,
) -> dict:
    """Drain relevant SQL candidate sets without arbitrary discovery caps.

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
        'candidate_query_ms': 0.0,
        'db_write_ms': 0.0,
        'quotation_jobs_queued': 0,
        'notification_jobs_queued': 0,
        'rejection_counts': {},
    }
    cursor = after_supplier_id
    page_limit = limit if bounded else UNBOUNDED_CHUNK_SIZE
    while True:
        batch = _match_batch(
            db,
            rfq,
            actor,
            page_limit,
            cursor,
            True,
            changed_item_ids=changed_item_ids,
            expected_revision=expected_revision,
            skip_evaluated_revision=skip_evaluated_revision,
        )
        summary['eligible_suppliers'] += batch.get('eligible_suppliers', 0)
        summary['new_invitations'] += batch.get('new_invitations', 0)
        summary['invitations'].extend(batch.get('invitations', []))
        summary['evaluated_suppliers'] += batch.get('evaluated_suppliers', 0)
        summary['ineligible_suppliers'] += batch.get('ineligible_suppliers', 0)
        summary['candidate_query_ms'] = round(summary['candidate_query_ms'] + float(batch.get('candidate_query_ms', 0.0)), 2)
        summary['db_write_ms'] = round(summary['db_write_ms'] + float(batch.get('db_write_ms', 0.0)), 2)
        summary['quotation_jobs_queued'] += batch.get('quotation_jobs_queued', 0)
        summary['notification_jobs_queued'] += batch.get('notification_jobs_queued', 0)
        for status_key, count in (batch.get('rejection_counts') or {}).items():
            summary['rejection_counts'][status_key] = summary['rejection_counts'].get(status_key, 0) + count
        if batch.get('stale_revision'):
            summary['stale_revision'] = True
            summary['next_cursor'] = None
            return summary
        db.flush()
        cursor = batch.get('next_cursor')
        summary['next_cursor'] = cursor
        if single_batch or not cursor:
            return summary
        if time_budget is not None and time.monotonic() - started >= time_budget:
            return summary


_CATEGORY_CACHE: dict[tuple, tuple[float, dict]] = {}
_CATEGORY_CACHE_TTL = 120.0
_CATALOG_STAMP_CACHE: dict[tuple, tuple[float, object]] = {}
_CATALOG_STAMP_TTL = 15.0


def active_categories(db: Session) -> dict[str, SimpleNamespace]:
    """Active taxonomy as plain values; cached briefly per PostgreSQL database and catalog epoch."""
    postgres = db.bind.dialect.name == 'postgresql'
    key = (str(db.bind.url), catalog_cache._epoch()) if postgres else None
    if key:
        hit = _CATEGORY_CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CATEGORY_CACHE_TTL:
            return hit[1]
    rows = db.execute(select(
        Category.id, Category.name_en, Category.name_ar, Category.parent_id, Category.normalized_name
    ).where(Category.active.is_(True))).all()
    categories = {
        row.id: SimpleNamespace(
            id=row.id, name_en=row.name_en, name_ar=row.name_ar,
            parent_id=row.parent_id, normalized_name=row.normalized_name, active=True,
        )
        for row in rows
    }
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
    """Resolve buyer taxonomy plus the Material Master product family for every RFQ line."""
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
        clean_desc = _product_description(item.description)
        tokens = product_tokens(clean_desc)
        item_disc = getattr(item, 'discipline', '') or ''
        blocked = bool(
            (material and material.discipline and not normalize_discipline(material.discipline))
            or (item_disc and not normalize_discipline(item_disc))
        )
        scopes.append({
            'id': item.id, 'item': item, 'material': material, 'material_id': item.material_id,
            'main': main, 'sub': sub, 'tokens': tokens, 'phrase': canonical_product_text(clean_desc),
            'keywords': extract_keywords(clean_desc), 'blocked': blocked,
            'code': material.code.strip().casefold() if material and material.code else None,
        })
        terms.update(product_search_terms(tokens)[:6])

    info: dict[str, tuple[str, str | None, str | None, set[str]]] = {}
    exact_ids = {scope['material_id'] for scope in scopes if scope['material_id']}
    clauses = [
        Material.name_en.ilike('%' + t.replace('_', '\\_').replace('%', '\\%') + '%', escape='\\')
        for t in sorted(terms)[:120]
    ]
    if exact_ids:
        clauses.append(Material.id.in_(exact_ids))
    if clauses:
        for row in db.execute(select(
            Material.id, Material.name_en, Material.name_ar,
            Material.main_category_id, Material.subcategory_id, Material.category_id,
        ).where(Material.active.is_(True), or_(*clauses))).all():
            pair = category_pair(row, categories)
            if pair[0] not in categories:
                continue
            info[row.id] = (row.name_en, pair[0], pair[1], product_tokens(row.name_en) | product_tokens(row.name_ar))

    for scope in scopes:
        request = _product_description(scope['item'].description)
        specs = _product_description(scope['item'].specifications)
        family = {
            mid: sim for mid, value in info.items()
            if (sim := family_similarity(scope['tokens'], value[3]))
            and not technical_conflict(request, value[0], specs)
        }
        if len(scope['tokens']) == 1 and scope['sub']:
            (word,) = scope['tokens']
            family.update({
                mid: 0.5 for mid, value in info.items()
                if mid not in family and value[2] == scope['sub']
                and word in value[3] and not technical_conflict(request, value[0], specs)
            })
        if scope['material_id'] in info:
            family[scope['material_id']] = 1.0
        if scope['main']:
            family = {mid: sim for mid, sim in family.items() if info[mid][1] == scope['main']}
        pairs = Counter((info[mid][1], info[mid][2]) for mid in family)
        family_main, family_sub = pairs.most_common(1)[0][0] if pairs else (None, None)
        family_subs = {sub for _, sub in pairs if sub}
        scope_mains = {cid for cid in {scope['main'], *(main for main, _ in pairs)} if cid}
        scope_subs = {cid for cid in {scope['sub'], *family_subs} if cid}
        exact_sub = info[scope['material_id']][2] if scope['material_id'] in info else None
        capability_subs = {
            cid for cid in (scope['sub'], exact_sub, family_sub)
            if cid and cid in categories
            and (not scope['main'] or cid == scope['sub'] or categories[cid].parent_id == scope['main'])
        }
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
            'descriptive_subs': {
                sid for sid in scope_subs
                if sid in categories and scope['tokens'] & product_tokens(categories[sid].name_en)
            },
            'related': scope_mains | scope_subs | capability_subs | {c for m in scope_mains for c in children.get(m, ())},
            'conflict': bool(family and scope['main'] and (
                family_main != scope['main'] or (scope['sub'] and scope['sub'] not in family_subs)
            )),
            'clean_request': request,
            'clean_specs': specs,
        })
    return scopes, info


_SCOPE_CACHE: dict[tuple, tuple[float, list[dict], dict]] = {}
_SCOPE_CACHE_TTL = 300.0


def _catalog_stamp(db: Session):
    postgres = db.bind.dialect.name == 'postgresql'
    now = time.monotonic()
    if postgres:
        skey = (str(db.bind.url), catalog_cache._epoch())
        hit = _CATALOG_STAMP_CACHE.get(skey)
        if hit and now - hit[0] < _CATALOG_STAMP_TTL:
            return hit[1]
        stamp = db.scalar(select(func.max(Material.updated_at)))
        _CATALOG_STAMP_CACHE.clear()
        _CATALOG_STAMP_CACHE[skey] = (now, stamp)
        return stamp
    return db.scalar(select(func.max(Material.updated_at)))


def cached_item_scopes(db: Session, rfq: RFQ, requested, categories: dict) -> tuple[list[dict], dict]:
    """resolve_item_scopes, reused for the same RFQ lines within a few minutes."""
    taxonomy = hash(frozenset((cid, c.parent_id, c.name_en, c.normalized_name) for cid, c in categories.items()))
    catalog = _catalog_stamp(db)
    key = (
        id(db.get_bind()), rfq.id, taxonomy, catalog,
        tuple(
            (
                item.id, item.material_id, item.main_category_id, item.subcategory_id,
                item.description, item.specifications, getattr(item, 'discipline', ''),
                getattr(material, 'version', None),
            )
            for item, material in requested
        ),
    )
    now = time.monotonic()
    hit = _SCOPE_CACHE.get(key)
    if hit and now - hit[0] < _SCOPE_CACHE_TTL:
        rows = {item.id: (item, material) for item, material in requested}
        return [
            {**derived, 'item': rows[derived['id']][0], 'material': rows[derived['id']][1]}
            for derived in hit[1]
        ], hit[2]
    scopes, info = resolve_item_scopes(db, requested, categories)
    if len(_SCOPE_CACHE) >= 128:
        _SCOPE_CACHE.clear()
    _SCOPE_CACHE[key] = (
        now,
        [{k: v for k, v in scope.items() if k not in {'item', 'material'}} for scope in scopes],
        info,
    )
    return scopes, info


def _tokenize_fragments(fragments: list[tuple[str, str | None]]) -> list[tuple[str, str, set[str], str | None]]:
    """Convert (raw_fragment, trade_id) pairs into deduplicated (text, canonical, tokens, trade_id)."""
    seen: dict[str, str | None] = {}
    for frag, tid in fragments:
        if not frag:
            continue
        stripped = frag.strip()
        if not stripped:
            continue
        if stripped not in seen or (seen[stripped] is None and tid is not None):
            seen[stripped] = tid
    out = []
    for stripped, tid in seen.items():
        canonical = canonical_product_text(stripped)
        out.append((stripped, canonical, canonical_tokens(canonical), tid))
    return out


def _evidence_texts(trades: list, pricebooks: list, caps: list, terms: tuple[str, ...]) -> list[tuple[str, str, set[str]]]:
    """Legacy-compatible helper returning (original, canonical, tokens)."""
    frags: list[tuple[str, str | None]] = []
    for trade in trades:
        tid = getattr(trade, 'id', None)
        for p in re.split(r'[;\n|]', f'{trade.raw_products or ""};{trade.raw_trade or ""}'):
            frags.append((p, tid))
    for pb in pricebooks:
        frags.append((pb.supplier_description or '', None))
        frags.append((pb.offered_description or '', None))
    for cap in caps:
        for p in (cap.trade_keywords or '').split('|'):
            frags.append((p, None))
    return [(t, c, tok) for t, c, tok, _ in _tokenize_fragments(frags)]


def _chunked(seq: list, size: int = BULK_WRITE_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _dialect_insert(db: Session, model):
    if db.bind.dialect.name == 'postgresql':
        return pg_insert(model)
    return sqlite_insert(model)


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
    changed_item_ids: set[str] | None = None,
    expected_revision: int | None = None,
    skip_evaluated_revision: int | None = None,
) -> dict:
    """Find candidates with one set-based SQL query, grade product evidence, and bulk-persist matches."""
    empty = {
        'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [],
        'evaluated_suppliers': 0, 'ineligible_suppliers': 0,
        'candidate_query_ms': 0.0, 'db_write_ms': 0.0,
        'quotation_jobs_queued': 0, 'notification_jobs_queued': 0, 'rejection_counts': {},
    }
    if rfq.status not in {'published', 'collecting_quotes'} or as_utc(rfq.response_deadline) <= datetime.now(timezone.utc):
        return empty
    current_rev = int(getattr(rfq, 'revision', 0) or 0)
    if expected_revision is not None and expected_revision < current_rev:
        return {**empty, 'stale_revision': True}

    requested = db.execute(
        select(RFQItem, Material)
        .outerjoin(Material, Material.id == RFQItem.material_id)
        .where(RFQItem.rfq_id == rfq.id, RFQItem.is_active.is_(True))
        .order_by(RFQItem.created_at, RFQItem.id)
    ).all()
    if not requested:
        return empty

    categories = active_categories(db)
    scopes, material_info = cached_item_scopes(db, rfq, requested, categories)
    discovery = scopes if changed_item_ids is None else [scope for scope in scopes if scope['id'] in changed_item_ids]
    by_id = {scope['id']: scope for scope in scopes}
    postgres = db.bind.dialect.name == 'postgresql'
    default_batch = get_settings().matching_batch_size
    batch_size = max(1, min(limit if limit is not None else default_batch, 20000))

    exact_ids = {s['material_id'] for s in scopes if s['material_id']}
    discovery_exact = {s['material_id'] for s in discovery if s['material_id']}
    evidence_ids = sorted(discovery_exact | {mid for s in discovery for mid in s['family']})
    all_evidence_ids = sorted(exact_ids | {mid for s in scopes for mid in s['family']})
    sub_scope = {sid for s in discovery for sid in s['capability_subs'] | s['descriptive_subs']}
    scope_mains = {mid for s in discovery for mid in s['scope_mains']}
    broad_mains = {mid for scope in discovery if not scope['sub'] for mid in scope['scope_mains']}
    broad_children = {cid for cid, category in categories.items() if category.parent_id in broad_mains}
    related = {cid for s in scopes for cid in s['related']}
    text_terms: set[str] = set()
    for s in discovery:
        heads = s['tokens'] & PRODUCT_HEADS
        if heads:
            text_terms.update(product_search_terms(heads))
        elif len(s['phrase']) >= 6:
            text_terms.add(s['phrase'])
    discovery_terms = {
        t for s in discovery if not s['main'] and not s['family']
        for t in sorted(s['tokens'], key=len, reverse=True)[:4]
    }

    def like(column, values):
        return or_(*[
            column.ilike('%' + v.replace('_', '\\_').replace('%', '\\%') + '%', escape='\\')
            for v in sorted(values)
        ])

    cand_t0 = time.perf_counter()
    branches = []
    if skip_evaluated_revision is None:
        branches.append(select(RFQMatch.supplier_org_id.label('id')).where(RFQMatch.rfq_id == rfq.id))
    if evidence_ids:
        if postgres:
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
        phrases = {s['phrase'] for s in discovery if len(s['phrase']) >= 5}
        if phrases:
            branches.append(select(PriceBookItem.org_id.label('id')).where(
                PriceBookItem.active.is_(True),
                or_(*(PriceBookItem.supplier_description.op('%')(phrase) for phrase in phrases))))
            branches.append(select(ImportedSupplierTrade.org_id.label('id')).where(
                or_(*(ImportedSupplierTrade.raw_products.op('%')(phrase) for phrase in phrases))))
    if sub_scope or broad_children:
        branches.append(select(Capability.org_id.label('id')).where(
            Capability.category_id.in_(sub_scope | broad_children)))
    if scope_mains:
        branches.append(select(Capability.org_id.label('id')).where(
            Capability.category_id.in_(scope_mains)))
    if discovery_terms:
        branches.append(select(Capability.org_id.label('id')).where(like(Capability.trade_keywords, discovery_terms)))
        branches.append(select(PriceBookItem.org_id.label('id')).where(
            PriceBookItem.active.is_(True), like(PriceBookItem.supplier_description, discovery_terms)))
        branches.append(select(ImportedSupplierTrade.org_id.label('id')).where(
            like(ImportedSupplierTrade.raw_trade, discovery_terms)))

    if supplier_ids is not None:
        candidate_query = select(Organization.id).where(
            Organization.id.in_(supplier_ids),
            Organization.id != rfq.buyer_org_id,
        ).order_by(Organization.id)
    elif not branches:
        return empty
    else:
        combined = union(*branches).cte('candidate_orgs')
        candidate_query = select(combined.c.id).where(combined.c.id != rfq.buyer_org_id)
        if after_supplier_id:
            candidate_query = candidate_query.where(combined.c.id > after_supplier_id)
        if skip_evaluated_revision is not None and changed_item_ids is None:
            candidate_query = candidate_query.where(~select(literal(1)).where(
                RFQMatch.rfq_id == rfq.id,
                RFQMatch.rfq_revision == skip_evaluated_revision,
                RFQMatch.supplier_org_id == combined.c.id,
            ).exists())
        candidate_query = candidate_query.order_by(combined.c.id)
        if bounded:
            candidate_query = candidate_query.limit(batch_size + 1)

    candidate_ids = list(db.scalars(candidate_query).all())
    candidate_query_ms = (time.perf_counter() - cand_t0) * 1000.0
    has_more = bounded and supplier_ids is None and len(candidate_ids) > batch_size
    if bounded:
        candidate_ids = candidate_ids[:batch_size]
    if not candidate_ids:
        return {**empty, 'candidate_query_ms': round(candidate_query_ms, 2)}

    # Lightweight column projection for candidate organizations (avoids loading large text/JSON columns or reflection).
    need_semantic_desc = bool(
        semantic_enabled and not evaluate_only
        and any(not s['main'] and not s['family'] for s in scopes)
        and getattr(get_settings(), 'gemini_enabled', True)
        and get_settings().gemini_api_key.get_secret_value()
    )
    org_cols = [
        Organization.id, Organization.name, Organization.city, Organization.country,
        Organization.is_active, Organization.is_supplier, Organization.verified_status,
    ]
    if need_semantic_desc:
        org_cols.append(Organization.description)
    orgs: dict[str, SimpleNamespace] = {}
    for row in db.execute(select(*org_cols).where(Organization.id.in_(candidate_ids))).all():
        orgs[row.id] = SimpleNamespace(
            id=row.id, name=row.name, city=row.city, country=row.country,
            is_active=row.is_active, is_supplier=row.is_supplier, verified_status=row.verified_status,
            description=getattr(row, 'description', None),
        )

    caps_by_org: dict[str, list[SimpleNamespace]] = {}
    cap_links: dict[str, set[str]] = {}
    link_counts: dict[str, int] = {}
    filter_related = bool(all(s['related'] for s in scopes) and related)

    if postgres:
        # Single PostgreSQL query fetches capabilities + matched material_ids + array lengths at once.
        rel_clause = ' AND c.category_id = ANY(:related)' if filter_related else ''
        if all_evidence_ids:
            sql = text(
                'SELECT c.id, c.org_id, c.category_id, c.city, c.qualifications, c.brands, '
                'c.trade_keywords, c.mapping_source, c.confirmed, c.accepts_rfqs, c.eligibility_status, '
                'CASE WHEN c.material_ids::jsonb ?| :ids '
                '  THEN ARRAY(SELECT v FROM jsonb_array_elements_text(c.material_ids::jsonb) v WHERE v = ANY(:ids)) '
                '  ELSE ARRAY[]::text[] END AS matched_links, '
                'CASE WHEN c.material_ids::jsonb ?| :ids '
                '  THEN jsonb_array_length(c.material_ids::jsonb) ELSE 0 END AS total_links '
                f'FROM supplier_capabilities c WHERE c.org_id = ANY(:orgs){rel_clause}'
            )
            params: dict = {'orgs': candidate_ids, 'ids': all_evidence_ids}
        else:
            sql = text(
                'SELECT c.id, c.org_id, c.category_id, c.city, c.qualifications, c.brands, '
                'c.trade_keywords, c.mapping_source, c.confirmed, c.accepts_rfqs, c.eligibility_status, '
                "ARRAY[]::text[] AS matched_links, 0 AS total_links "
                f'FROM supplier_capabilities c WHERE c.org_id = ANY(:orgs){rel_clause}'
            )
            params = {'orgs': candidate_ids}
        if filter_related:
            params['related'] = sorted(related)
        for row in db.execute(sql, params).all():
            cap_ns = SimpleNamespace(
                id=row.id, org_id=row.org_id, category_id=row.category_id, city=row.city,
                qualifications=row.qualifications, brands=row.brands, trade_keywords=row.trade_keywords,
                mapping_source=row.mapping_source, confirmed=row.confirmed, accepts_rfqs=row.accepts_rfqs,
                eligibility_status=row.eligibility_status,
            )
            caps_by_org.setdefault(row.org_id, []).append(cap_ns)
            if row.matched_links:
                cap_links[row.id] = set(row.matched_links)
                link_counts[row.id] = int(row.total_links or 0)
    else:
        cap_query = select(Capability).where(Capability.org_id.in_(candidate_ids))
        if filter_related:
            cap_query = cap_query.where(Capability.category_id.in_(related))
        evidence_set = set(all_evidence_ids)
        for cap in db.scalars(cap_query):
            caps_by_org.setdefault(cap.org_id, []).append(cap)
            cap_links[cap.id] = set(cap.material_ids or []) & evidence_set
            link_counts[cap.id] = len(cap.material_ids or [])

    # Provenance: category-wide material link assignments are category capability, not product evidence.
    linked_caps = {
        cap.id: cap.category_id
        for caps in caps_by_org.values() for cap in caps
        if cap_links.get(cap.id) and link_counts.get(cap.id, 0) >= CATEGORY_WIDE_MIN_LINKS
    }
    if linked_caps:
        cats = set(linked_caps.values())
        per_category: Counter = Counter()
        count_union = union_all(
            select(Material.category_id.label('cid'), func.count().label('cnt')).where(
                Material.active.is_(True), Material.category_id.in_(cats)).group_by(Material.category_id),
            select(Material.subcategory_id.label('cid'), func.count().label('cnt')).where(
                Material.active.is_(True), Material.subcategory_id.in_(cats)).group_by(Material.subcategory_id),
            select(Material.main_category_id.label('cid'), func.count().label('cnt')).where(
                Material.active.is_(True), Material.main_category_id.in_(cats)).group_by(Material.main_category_id),
        ).subquery()
        for cid, cnt in db.execute(select(count_union.c.cid, func.max(count_union.c.cnt)).group_by(count_union.c.cid)).all():
            per_category[cid] = int(cnt or 0)
        for cap_id, cid in linked_caps.items():
            if per_category[cid] and link_counts[cap_id] >= CATEGORY_WIDE_SHARE * per_category[cid]:
                cap_links[cap_id] = set()

    def links(cap) -> set[str]:
        return cap_links.get(cap.id, set())

    def approved(cap) -> bool:
        return bool(
            cap.confirmed and cap.eligibility_status == 'enabled'
            and cap.accepts_rfqs and cap.category_id in categories
        )

    # Only fetch pricebooks and imported trades for active, non-rejected/suspended suppliers with approved capabilities.
    active_approved_orgs = [
        oid for oid in candidate_ids
        if (o := orgs.get(oid)) and o.is_active and o.is_supplier
        and o.verified_status not in {'rejected', 'suspended'}
        and any(approved(c) for c in caps_by_org.get(oid, []))
    ]

    pricebooks_by_org: dict[str, list[SimpleNamespace]] = {}
    if active_approved_orgs:
        pb_query = select(
            PriceBookItem.org_id, PriceBookItem.material_id, PriceBookItem.main_category_id,
            PriceBookItem.subcategory_id, PriceBookItem.supplier_description,
            PriceBookItem.offered_description, PriceBookItem.brand, PriceBookItem.model,
        ).where(PriceBookItem.org_id.in_(active_approved_orgs), PriceBookItem.active.is_(True))
        if all(s['scope_mains'] or s['scope_subs'] or s['material_id'] or s['family'] for s in scopes) and not need_semantic_desc:
            all_pb_mains = {m for s in scopes for m in s['scope_mains']}
            all_pb_subs = {sub for s in scopes for sub in s['scope_subs']}
            pb_conds = []
            if all_evidence_ids:
                pb_conds.append(PriceBookItem.material_id.in_(all_evidence_ids))
            if all_pb_mains:
                pb_conds.append(PriceBookItem.main_category_id.in_(all_pb_mains))
            if all_pb_subs:
                pb_conds.append(PriceBookItem.subcategory_id.in_(all_pb_subs))
            if pb_conds:
                pb_query = pb_query.where(or_(*pb_conds))
        for pb in db.execute(pb_query).all():
            pricebooks_by_org.setdefault(pb.org_id, []).append(pb)

    trades_by_org: dict[str, list] = {}
    if active_approved_orgs:
        trade_query = select(
            ImportedSupplierTrade.id, ImportedSupplierTrade.org_id,
            ImportedSupplierTrade.raw_trade, ImportedSupplierTrade.raw_products,
            ImportedSupplierTrade.raw_brands,
        ).where(
            ImportedSupplierTrade.org_id.in_(active_approved_orgs),
            ImportedSupplierTrade.mapping_status != 'unreviewed',
        )
        for trade in db.execute(trade_query).all():
            trades_by_org.setdefault(trade.org_id, []).append(trade)

    # Lazy per-supplier cache of tokenized trade fragments and brand sets.
    org_trade_tokens_cache: dict[str, list[tuple[str, str, set[str], str | None]]] = {}
    org_trade_brands_cache: dict[str, set[str]] = {}

    def get_org_trade_tokens(org_id: str) -> list[tuple[str, str, set[str], str | None]]:
        cached = org_trade_tokens_cache.get(org_id)
        if cached is not None:
            return cached
        frags: list[tuple[str, str | None]] = []
        for trade in trades_by_org.get(org_id, []):
            for p in re.split(r'[;\n|]', f'{trade.raw_products or ""};{trade.raw_trade or ""}'):
                frags.append((p, trade.id))
        parsed = _tokenize_fragments(frags)
        org_trade_tokens_cache[org_id] = parsed
        return parsed

    def get_org_trade_brands(org_id: str) -> set[str]:
        cached = org_trade_brands_cache.get(org_id)
        if cached is not None:
            return cached
        brands = {
            v.strip().casefold()
            for trade in trades_by_org.get(org_id, [])
            for v in re.split(r'[,;/|\n]', trade.raw_brands or '')
            if v.strip()
        }
        org_trade_brands_cache[org_id] = brands
        return brands

    rfq_city_norm = normalize_city(rfq.city)
    required = {value.strip().casefold() for value in (rfq.required_qualifications or []) if value.strip()}
    item_brands = {s['id']: requested_brand(s['item']) for s in scopes}

    # Preload Material.specs_template brands in a single query when any RFQ item specifies a brand.
    material_brands_by_id: dict[str, set[str]] = {}
    if any(b is not None for b in item_brands.values()):
        candidate_mat_ids = {mid for cap_set in cap_links.values() for mid in cap_set} | exact_ids
        if candidate_mat_ids:
            for mid, template in db.execute(
                select(Material.id, Material.specs_template).where(Material.id.in_(candidate_mat_ids))
            ).all():
                bset = {
                    str(v).strip().casefold()
                    for k, v in (template or {}).items()
                    if str(k).strip().casefold() in {'brand', 'manufacturer'} and isinstance(v, str) and v.strip()
                }
                if bset:
                    material_brands_by_id[mid] = bset

    def brand_confirmed(org_id: str, item, entry: dict, wanted: set[str]) -> bool:
        """Requested brands appear in this supplier's saved/imported product data."""
        saved = {b.strip().casefold() for c in entry['caps'] for b in (c.brands or []) if isinstance(b, str) and b.strip()}
        for c in entry['caps']:
            for mid in links(c):
                if mid == item.material_id or mid in entry['materials']:
                    saved |= material_brands_by_id.get(mid, set())
        item_main, item_sub = entry['taxonomy']
        saved.update(
            pb.brand.strip().casefold()
            for pb in pricebooks_by_org.get(org_id, [])
            if pb.brand and (
                pb.material_id == item.material_id
                or pb.material_id in entry['materials']
                or (item_sub and pb.subcategory_id == item_sub)
                or (not item_sub and item_main and pb.main_category_id == item_main)
            )
        )
        saved |= get_org_trade_brands(org_id)
        return bool(wanted & saved)

    children_by_main: dict[str, set[str]] = {}
    for cid, category in categories.items():
        if category.parent_id:
            children_by_main.setdefault(category.parent_id, set()).add(cid)

    def blanket_imported_mains(caps: list) -> frozenset:
        """Main categories where admin import mapped this supplier to every subcategory."""
        held: dict[str, set[str]] = {}
        for cap in caps:
            parent = categories[cap.category_id].parent_id
            if parent and cap.mapping_source == 'admin_import':
                held.setdefault(parent, set()).add(cap.category_id)
        return frozenset(
            main for main, subs in held.items()
            if len(children_by_main.get(main, ())) >= BLANKET_MIN_SUBCATEGORIES
            and children_by_main[main] <= subs
        )

    def grade(scope: dict, caps: list, pbs: list, get_texts, blanket_mains: frozenset = frozenset()) -> dict | None:
        """Best evidence for one RFQ line from the given approved capabilities."""
        if scope['blocked']:
            return None
        in_scope = [c for c in caps if c.category_id in scope['related']] if scope['related'] else list(caps)
        if not in_scope:
            return None
        preferred = [c for c in in_scope if c.category_id in scope['scope_subs']] or in_scope
        mid, family = scope['material_id'], scope['family']
        default_tax = (scope['family_main'] or scope['main'], scope['family_sub'] or scope['sub'])
        request = scope['clean_request']
        specs = scope['clean_specs']
        if mid and mid in material_info and technical_conflict(request, material_info[mid][0], specs):
            mid = None
        if mid:
            linked = [c for c in in_scope if mid in links(c)]
            if linked or any(pb.material_id == mid for pb in pbs) or (scope['code'] and any(
                scope['code'] in (pb.supplier_description or '').casefold()
                or scope['code'] in (pb.model or '').casefold()
                for pb in pbs
            )):
                name = material_info.get(mid, (scope['item'].description,))[0]
                return {
                    'level': LEVEL_EXACT, 'caps': linked or preferred, 'sim': 1.0, 'detail': name,
                    'taxonomy': (scope['main'], scope['sub']), 'materials': {mid}, 'trades': set(),
                }
        phrase = scope['phrase']
        best_link = None
        for cap in in_scope:
            for linked_id in links(cap):
                sim = family.get(linked_id)
                if sim and (best_link is None or sim > best_link[0]):
                    best_link = (sim, linked_id, cap)
        if phrase and best_link and canonical_product_text(material_info[best_link[1]][0]) == phrase:
            return {
                'level': LEVEL_ALIAS, 'caps': [best_link[2]], 'sim': 1.0,
                'detail': material_info[best_link[1]][0],
                'taxonomy': material_info[best_link[1]][1:3],
                'materials': {best_link[1]}, 'trades': set(),
            }
        texts = get_texts()
        if phrase:
            alias_hit = next(((txt, tid) for txt, canonical, _, tid in texts if canonical == phrase), None)
            if alias_hit:
                return {
                    'level': LEVEL_ALIAS, 'caps': preferred, 'sim': 1.0, 'detail': alias_hit[0].strip(),
                    'taxonomy': default_tax, 'materials': set(),
                    'trades': {alias_hit[1]} if alias_hit[1] else set(),
                }
        candidates = []
        if best_link:
            candidates.append((
                best_link[0], material_info[best_link[1]][0],
                [c for c in in_scope if best_link[1] in links(c)],
                material_info[best_link[1]][1:3],
                {m for c in in_scope for m in links(c) if m in family},
                set(),
            ))
        for pb in pbs:
            pb_pair = (pb.main_category_id, pb.subcategory_id)
            if pb.material_id in material_info:
                pb_pair = material_info[pb.material_id][1:3]
            if scope['main'] and pb_pair[0] not in scope['scope_mains']:
                continue
            if pb.material_id and pb.material_id in family and not technical_conflict(request, pb.supplier_description, specs):
                candidates.append((
                    family[pb.material_id],
                    pb.supplier_description or material_info[pb.material_id][0],
                    preferred, material_info[pb.material_id][1:3], {pb.material_id}, set(),
                ))
        item_tokens = scope['tokens']
        if item_tokens and texts:
            long_item_lens = [len(t) for t in item_tokens if len(t) >= 5]
            min_cand_len = int(min(long_item_lens) * 0.72) if long_item_lens else 999
            max_cand_len = int(max(long_item_lens) / 0.72) + 1 if long_item_lens else 0
            best_sim = 0.0
            best_txt = ''
            best_tid = None
            for txt, _, tokens, tid in texts:
                if not tokens:
                    continue
                # Fast pre-filter: skip SequenceMatcher if neither token overlap nor plausible >=5-char word exists.
                if not (item_tokens & tokens) and not (
                    long_item_lens and any(min_cand_len <= len(w) <= max_cand_len for w in tokens)
                ):
                    continue
                sim = family_similarity(item_tokens, tokens)
                if sim > best_sim and not technical_conflict(request, txt, specs):
                    best_sim, best_txt, best_tid = sim, txt, tid
            if best_sim > 0.0:
                candidates.append((
                    best_sim, best_txt.strip(), preferred, default_tax, set(),
                    {best_tid} if best_tid else set(),
                ))
        if candidates:
            sim, detail, support, taxonomy, materials, trades = max(candidates, key=lambda c: c[0])
            return {
                'level': LEVEL_FAMILY, 'caps': support, 'sim': sim, 'detail': detail,
                'taxonomy': taxonomy, 'materials': materials, 'trades': trades,
            }
        sub_caps = [c for c in in_scope if c.category_id in scope['capability_subs'] | scope['descriptive_subs']]
        specific = [
            c for c in sub_caps
            if not (c.mapping_source == 'admin_import' and categories[c.category_id].parent_id in blanket_mains)
        ]
        if specific:
            specific.sort(key=lambda c: (c.category_id not in scope['generic_cats'], c.category_id != scope['sub']))
            sub = specific[0].category_id
            return {
                'level': LEVEL_SUB, 'caps': specific, 'sim': 0.0, 'detail': categories[sub].name_en,
                'taxonomy': (categories[sub].parent_id, sub), 'materials': set(), 'trades': set(),
            }
        root_caps = [c for c in in_scope if c.category_id in scope['scope_mains']] or sub_caps
        if not root_caps and scope['main'] and not scope['sub']:
            root_caps = [c for c in in_scope if categories[c.category_id].parent_id == scope['main']]
        if root_caps:
            main = (
                root_caps[0].category_id if root_caps[0].category_id in scope['scope_mains']
                else categories[root_caps[0].category_id].parent_id
            )
            return {
                'level': LEVEL_MAIN, 'caps': root_caps, 'sim': 0.0, 'detail': categories[main].name_en,
                'taxonomy': (main, None), 'materials': set(), 'trades': set(),
            }
        return None

    def country_key(value):
        key = (value or '').strip().casefold()
        return 'sa' if key in {'sa', 'ksa', 'saudi arabia', 'المملكة العربية السعودية'} else key

    rfq_country = country_key(getattr(rfq, 'delivery_country', None))

    eligible: dict[str, dict] = {}
    ineligible: dict[str, dict] = {}
    for org_id in candidate_ids:
        org = orgs.get(org_id)
        if not org:
            continue
        caps = caps_by_org.get(org_id, [])
        if not org.is_active or not org.is_supplier:
            ineligible[org_id] = {
                'org': org, 'status': 'inactive',
                'reasons': ['Supplier organization is inactive or not marked as supplier.'],
            }
            continue
        if org.verified_status in {'rejected', 'suspended'}:
            ineligible[org_id] = {
                'org': org, 'status': org.verified_status,
                'reasons': [f'Supplier organization is {org.verified_status} and excluded from RFQ matching.'],
            }
            continue
        if rfq_country and org.country and rfq_country != country_key(org.country):
            ineligible[org_id] = {
                'org': org, 'status': 'location_mismatch',
                'reasons': [f'Supplier country {org.country} does not match delivery country {rfq.delivery_country}.'],
            }
            continue
        approved_caps = [c for c in caps if approved(c)]
        if not approved_caps:
            if caps:
                reason = (
                    'Capability was rejected by administration.'
                    if any(c.eligibility_status == 'rejected' for c in caps)
                    else 'Capability pending administrative review/approval.'
                )
                ineligible[org_id] = {'org': org, 'status': 'pending_approval', 'reasons': [reason]}
            else:
                ineligible[org_id] = {
                    'org': org, 'status': 'ineligible',
                    'reasons': [
                        'No approved capability in the requested category for: '
                        + '; '.join(s['item'].description for s in scopes) + '.'
                    ],
                }
            continue
        pbs = pricebooks_by_org.get(org_id, [])
        blanket = blanket_imported_mains(approved_caps)
        local_office = bool(rfq_city_norm) and normalize_city(org.city) == rfq_city_norm
        covering = [
            c for c in approved_caps
            if city_covered_by(rfq.city, c.city)[0] or (local_office and not (c.city or '').strip())
        ]
        blank = [c for c in approved_caps if not (c.city or '').strip()]
        items: dict[str, dict] = {}
        mismatched: dict[str, dict] = {}
        for scope in scopes:
            scoped_pbs = [
                pb for pb in pbs
                if (
                    pb.material_id == scope['material_id']
                    or pb.material_id in scope['family']
                    or pb.main_category_id in scope['scope_mains']
                    or pb.subcategory_id in scope['scope_subs']
                )
            ]
            scoped_caps = [cap for cap in approved_caps if cap.category_id in scope['related']]
            cached_scope_texts: list | None = None

            def lazy_texts(_oid=org_id, _spbs=scoped_pbs, _scaps=scoped_caps):
                nonlocal cached_scope_texts
                if cached_scope_texts is not None:
                    return cached_scope_texts
                base_trades = get_org_trade_tokens(_oid)
                extra_frags: list[tuple[str, str | None]] = []
                for pb in _spbs:
                    extra_frags.append((pb.supplier_description or '', None))
                    extra_frags.append((pb.offered_description or '', None))
                for cap in _scaps:
                    for kw in (cap.trade_keywords or '').split('|'):
                        extra_frags.append((kw, None))
                if not extra_frags:
                    cached_scope_texts = base_trades
                else:
                    seen_str = {t[0] for t in base_trades}
                    extra_parsed = [e for e in _tokenize_fragments(extra_frags) if e[0] not in seen_str]
                    cached_scope_texts = base_trades + extra_parsed if extra_parsed else base_trades
                return cached_scope_texts

            found = grade(scope, covering, scoped_pbs, lazy_texts, blanket) if covering else None
            if found:
                found['coverage'] = 'covered'
            elif rfq_city_norm and blank and (found := grade(scope, blank, scoped_pbs, lazy_texts, blanket)):
                found['coverage'] = 'unknown'
            if found:
                items[scope['id']] = found
            elif rfq_city_norm and (other := grade(scope, approved_caps, scoped_pbs, lazy_texts, blanket)):
                mismatched[scope['id']] = other
        if not items:
            if mismatched:
                cities = sorted({c.city.strip() for e in mismatched.values() for c in e['caps'] if c.city and c.city.strip()})
                ineligible[org_id] = {
                    'org': org, 'status': 'location_mismatch',
                    'reasons': [
                        f"Delivery location not covered: supplier serves '{', '.join(cities) or org.city or 'None'}', RFQ requires '{rfq.city}'."
                    ],
                }
            else:
                held = sorted({
                    taxonomy_label(categories, categories[c.category_id].parent_id, c.category_id)
                    if categories[c.category_id].parent_id else categories[c.category_id].name_en
                    for c in approved_caps
                })
                ineligible[org_id] = {
                    'org': org, 'status': 'ineligible',
                    'reasons': [
                        'No approved material, subcategory or main-category capability supports the requested items.',
                        f"Approved capabilities ({', '.join(held[:4])}{'…' if len(held) > 4 else ''}) are outside the "
                        f"requested categories for: {'; '.join(s['item'].description for s in scopes)}.",
                    ],
                }
            continue
        brand_missing = []
        for iid in list(items):
            brand = item_brands[iid]
            if brand is None:
                continue
            confirmed = brand_confirmed(org_id, by_id[iid]['item'], items[iid], brand[1])
            items[iid]['brand'] = (brand[0], confirmed)
            if brand[2] and not confirmed:
                brand_missing.append(f"{by_id[iid]['item'].description} (mandatory brand: {brand[0]})")
                del items[iid]
        if not items:
            ineligible[org_id] = {
                'org': org, 'status': 'brand_mismatch',
                'reasons': ['Mandatory brand not confirmed in supplier product data for: ' + '; '.join(brand_missing) + '.'],
            }
            continue
        evidence_caps = {id(c): c for e in items.values() for c in e['caps']}.values()
        supplier_quals = {q.strip().casefold() for c in evidence_caps for q in (c.qualifications or [])}
        if required and not required.issubset(supplier_quals):
            ineligible[org_id] = {'org': org, 'status': 'ineligible', 'reasons': ['Missing required project qualifications.']}
            continue
        coverage_unknown = bool(rfq_city_norm) and not any(e['coverage'] == 'covered' for e in items.values())
        exact_city = any(city_covered_by(rfq.city, c.city)[1] for e in items.values() if e['coverage'] == 'covered' for c in e['caps'])
        eligible[org_id] = {
            'org': org, 'items': items, 'coverage_unknown': coverage_unknown,
            'has_exact_city': exact_city, 'ai_explanations': {}, 'brand_missing': brand_missing,
            'office_city': local_office and not any(
                city_covered_by(rfq.city, c.city)[0] for e in items.values() for c in e['caps']
            ),
        }

    # Optional AI-assisted matching, only for lines with no taxonomy and no Material Master family.
    unresolved = [
        s for s in scopes
        if not s['main'] and not s['family'] and not any(s['id'] in c['items'] for c in eligible.values())
    ]
    settings = get_settings()
    if (
        semantic_enabled and unresolved and not evaluate_only
        and getattr(settings, 'gemini_enabled', True) and settings.gemini_api_key.get_secret_value()
    ):
        pool = {
            oid: orgs[oid] for oid in candidate_ids
            if oid not in eligible and oid in orgs
            and orgs[oid].is_active and orgs[oid].is_supplier
            and orgs[oid].verified_status not in {'rejected', 'suspended'}
            and any(approved(c) and city_covered_by(rfq.city, c.city)[0] for c in caps_by_org.get(oid, []))
        }
        payload = [{
            'supplier_org_id': oid,
            'categories': sorted({categories[c.category_id].name_en for c in caps_by_org.get(oid, []) if c.category_id in categories})[:10],
            'trade_keywords': ' | '.join(c.trade_keywords for c in caps_by_org.get(oid, []) if c.trade_keywords)[:300],
            'catalog_items': [pb.supplier_description for pb in pricebooks_by_org.get(oid, []) if pb.supplier_description][:10],
            'description': (org.description or '')[:240],
        } for oid, org in pool.items()]
        if payload:
            unresolved_payload = [
                {'item_id': s['id'], 'description': s['item'].description, 'specifications': s['item'].specifications or '', 'category': ''}
                for s in unresolved
            ]
            ai_matches = {}
            for item_start in range(0, len(unresolved_payload), 20):
                for supplier_start in range(0, len(payload), 40):
                    hits, _failed = gemini_semantic_supplier_match(
                        unresolved_payload[item_start:item_start + 20],
                        payload[supplier_start:supplier_start + 40],
                    )
                    for item_id, entries in hits.items():
                        ai_matches.setdefault(item_id, []).extend(entries)
            for item_id, hits in ai_matches.items():
                for hit in hits:
                    oid = hit['supplier_org_id']
                    if oid not in pool or item_id not in by_id:
                        continue
                    ineligible.pop(oid, None)
                    caps = [c for c in caps_by_org.get(oid, []) if approved(c) and city_covered_by(rfq.city, c.city)[0]]
                    entry = eligible.setdefault(oid, {
                        'org': pool[oid], 'items': {}, 'coverage_unknown': False, 'office_city': False,
                        'has_exact_city': any(city_covered_by(rfq.city, c.city)[1] for c in caps),
                        'ai_explanations': {},
                    })
                    entry['items'][item_id] = {
                        'level': LEVEL_AI, 'caps': caps, 'sim': 0.0, 'detail': hit['explanation'],
                        'taxonomy': (None, None), 'materials': set(), 'trades': set(), 'coverage': 'covered',
                    }
                    entry['ai_explanations'][item_id] = f"{hit['explanation']} (confidence {hit['confidence']:.2f})"

    def qualifies(scope: dict, e: dict) -> bool:
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
        del eligible[oid]
        ids = sorted(found, key=order.get)
        lines = '; '.join(by_id[iid]['item'].description for iid in ids)
        markers = [f"{LEVEL_MARKER[found[iid]['level']]}{iid}" for iid in ids]
        if candidate.get('brand_missing'):
            status, reason = 'brand_mismatch', (
                'Mandatory brand not confirmed in supplier product data for: '
                + '; '.join(candidate['brand_missing']) + '.'
            )
        elif any(qualifies(by_id[iid], {**e, 'coverage': 'covered'}) for iid, e in found.items()):
            status, reason = 'location_unconfirmed', (
                f'Product evidence found for: {lines}; delivery coverage for {rfq.city} is not declared by the supplier.'
            )
        elif any(e['level'] == LEVEL_AI for e in found.values()):
            status, reason = 'insufficient_evidence', f'AI suggestion only; supplier product evidence is required for: {lines}.'
        else:
            kind = 'subcategory' if any(e['level'] == LEVEL_SUB for e in found.values()) else 'main category'
            status, reason = 'insufficient_evidence', (
                f'Category capability only ({kind}); no supplier product, price-list or material evidence for: {lines}.'
            )
        ineligible[oid] = {'org': candidate['org'], 'status': status, 'reasons': [reason, *markers]}

    total = max(1, len(scopes))
    for candidate in eligible.values():
        items = candidate['items']
        candidate['auto_invite'] = True
        candidate['score'] = min(
            100,
            50 + int(sum(LEVEL_WEIGHT[e['level']] + 4 * e['sim'] for e in items.values()) / total)
            + (5 if candidate['has_exact_city'] else 0)
            - (3 if candidate['coverage_unknown'] else 0),
        )
    ranked = sorted(eligible.values(), key=lambda c: (
        not c['auto_invite'], -c['score'], -len(c['items']),
        c['coverage_unknown'], c['org'].name.casefold(), c['org'].id,
    ))

    rejection_counts = dict(Counter(e['status'] for e in ineligible.values()))

    if evaluate_only:
        return {
            'eligible_ids': [c['org'].id for c in ranked],
            'auto_invite_ids': [c['org'].id for c in ranked if c['auto_invite']],
            'evaluated_suppliers': len(candidate_ids),
            'ineligible_by_status': rejection_counts,
            'ineligible_reasons': dict(Counter(e['reasons'][0] for e in ineligible.values() if e['reasons']).most_common(10)),
            'ineligible_detail': {
                oid: f"{e['status']}: {e['reasons'][0] if e['reasons'] else ''}".strip()
                for oid, e in ineligible.items()
            },
            'item_levels': {
                s['id']: dict(Counter(c['items'][s['id']]['level'] for c in ranked if s['id'] in c['items']))
                for s in scopes
            },
            'covered_items': {c['org'].id: sorted(c['items']) for c in ranked},
            'match_levels': {
                c['org'].id: [level for level in LEVEL_ORDER if any(e['level'] == level for e in c['items'].values())]
                for c in ranked
            },
            'item_evidence': {
                c['org'].id: {
                    iid: {
                        'material_ids': sorted(e['materials']),
                        'category_ids': sorted({cap.category_id for cap in e['caps']}),
                        **({'trade_ids': sorted(e['trades'])} if e.get('trades') else {}),
                    }
                    for iid, e in c['items'].items()
                }
                for c in ranked
            },
            'candidate_query_ms': round(candidate_query_ms, 2),
        }

    # Verify RFQ revision has not moved forward before committing writes.
    write_t0 = time.perf_counter()
    if expected_revision is not None:
        live_rev = db.scalar(select(RFQ.revision).where(RFQ.id == rfq.id))
        if live_rev is not None and int(live_rev) > expected_revision:
            return {**empty, 'stale_revision': True, 'candidate_query_ms': round(candidate_query_ms, 2)}

    now_utc = datetime.now(timezone.utc)
    touched = set(candidate_ids) | set(eligible)
    existing_matches: dict[str, tuple[str, str]] = {}
    if touched:
        for oid, mid, st in db.execute(
            select(RFQMatch.supplier_org_id, RFQMatch.id, RFQMatch.status).where(
                RFQMatch.rfq_id == rfq.id, RFQMatch.supplier_org_id.in_(touched)
            )
        ).all():
            existing_matches[oid] = (mid, st)

    match_upsert_rows: list[dict] = []
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
            reasons.append(
                f"{LEVEL_LABEL[e['level']]}: {by_id[iid]['item'].description}"
                + (f' — {detail}' if detail and detail != by_id[iid]['item'].description else '')
                + (f' ({label})' if label else '') + '.'
            )

        brand_requests = [e['brand'][0] for _, e in sorted(items.items()) if e.get('brand')]
        brand_evidence = [e['brand'][1] for _, e in sorted(items.items()) if e.get('brand')]
        if brand_requests and all(brand_evidence):
            reasons.append(
                'Brand requirement matches approved saved product data: ' + ', '.join(brand_requests)
                + '. Availability and brand authorization require quotation confirmation.'
            )
        else:
            reasons.append(
                'Brand compliance is unverified' + (': ' + ', '.join(brand_requests) if brand_requests else '')
                + '; requested brands must be confirmed in the supplier quotation.'
            )
        reasons.append('Product evidence does not confirm technical compliance; confirm in the quotation')

        status = 'eligible' if len(items) == len(scopes) else 'partial'
        oid = candidate['org'].id
        match_upsert_rows.append({
            'id': existing_matches[oid][0] if oid in existing_matches else str(uuid4()),
            'created_at': now_utc,
            'rfq_id': rfq.id,
            'supplier_org_id': oid,
            'score': candidate['score'],
            'reasons': reasons,
            'status': status,
            'rfq_revision': current_rev,
        })

    for org_id, entry in ineligible.items():
        reasons = list(entry['reasons'])
        prev = existing_matches.get(org_id)
        if prev and prev[1] in MATCHED_STATUSES:
            from app.security import audit
            audit(
                db, actor, 'rfq.match_eligibility_corrected', rfq.buyer_org_id, prev[0],
                previous_status=prev[1], status=entry['status'], supplier_org_id=org_id,
                historical_invitation_preserved=True,
            )
        match_upsert_rows.append({
            'id': prev[0] if prev else str(uuid4()),
            'created_at': now_utc,
            'rfq_id': rfq.id,
            'supplier_org_id': org_id,
            'score': 0,
            'reasons': reasons,
            'status': entry['status'],
            'rfq_revision': current_rev,
        })

    if match_upsert_rows:
        for chunk in _chunked(match_upsert_rows):
            stmt = _dialect_insert(db, RFQMatch).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=['rfq_id', 'supplier_org_id'],
                set_={
                    'score': stmt.excluded.score,
                    'reasons': stmt.excluded.reasons,
                    'status': stmt.excluded.status,
                    'rfq_revision': stmt.excluded.rfq_revision,
                },
                where=(RFQMatch.rfq_revision <= stmt.excluded.rfq_revision),
            )
            db.execute(stmt)
        # Ensure identity map reflects updated RFQMatch values if already loaded in session.
        for obj in list(db.identity_map.values()):
            if isinstance(obj, RFQMatch) and obj.rfq_id == rfq.id and obj.supplier_org_id in touched:
                db.expire(obj)

    def invited_evidence(candidate: dict) -> dict:
        return {
            iid: {
                'material_ids': sorted(e['materials']),
                'category_ids': sorted({cap.category_id for cap in e['caps']}),
                **({'trade_ids': sorted(e['trades'])} if e.get('trades') else {}),
            }
            for iid, e in candidate['items'].items()
        }

    def invited_items(candidate: dict) -> list[str]:
        return sorted(candidate['items'])

    # Load existing invitations for all touched suppliers in one query.
    existing_invs: dict[str, Invitation] = {}
    if touched:
        for inv in db.scalars(select(Invitation).where(
            Invitation.rfq_id == rfq.id,
            Invitation.supplier_org_id.in_(touched),
        )):
            existing_invs[inv.supplier_org_id] = inv
    for obj in db.new:
        if isinstance(obj, Invitation) and obj.rfq_id == rfq.id:
            existing_invs[obj.supplier_org_id] = obj

    ineligible_with_inv = {
        oid: entry['reasons'][0]
        for oid, entry in ineligible.items()
        if oid in existing_invs and entry['reasons']
    }
    superseded = supersede_invitations(db, rfq, ineligible_with_inv, actor) if ineligible_with_inv else 0

    invitable = [c for c in ranked if c['auto_invite']]
    reinstated: list[Invitation] = []
    for c in invitable:
        oid = c['org'].id
        old = existing_invs.get(oid)
        if not old:
            continue
        if old.status in {'invited', 'accepted'}:
            old.matched_item_ids = invited_items(c)
            old.match_evidence = invited_evidence(c)
            old.rfq_revision = current_rev
        elif old.status == 'cancelled':
            old.status, old.cancelled_at, old.cancellation_reason = 'invited', None, None
            old.matched_item_ids = invited_items(c)
            old.match_evidence = invited_evidence(c)
            old.rfq_revision = current_rev
            reinstated.append(old)

    new_inv_rows: list[dict] = []
    for c in invitable:
        oid = c['org'].id
        if oid in existing_invs:
            continue
        new_inv_rows.append({
            'id': str(uuid4()),
            'created_at': now_utc,
            'invited_at': now_utc,
            'rfq_id': rfq.id,
            'supplier_org_id': oid,
            'status': 'invited',
            'rfq_revision': current_rev,
            'matched_item_ids': invited_items(c),
            'match_evidence': invited_evidence(c),
            'reminder_count': 0,
        })

    invitations: list[Invitation] = []
    if new_inv_rows:
        for chunk in _chunked(new_inv_rows):
            stmt = _dialect_insert(db, Invitation).values(chunk).on_conflict_do_nothing(
                index_elements=['rfq_id', 'supplier_org_id']
            )
            db.execute(stmt)
        inserted_orgs = [r['supplier_org_id'] for r in new_inv_rows]
        invitations = list(db.scalars(select(Invitation).where(
            Invitation.rfq_id == rfq.id,
            Invitation.supplier_org_id.in_(inserted_orgs),
        )))

    outbox_rows: list[dict] = []
    outbox_id_by_inv: dict[str, str] = {}
    for inv in invitations:
        oid_event = str(uuid4())
        outbox_id_by_inv[inv.id] = oid_event
        outbox_rows.append({
            'id': oid_event,
            'created_at': now_utc,
            'event': 'rfq.invitation',
            'dedupe_key': f'rfq-invitation:{inv.id}',
            'status': 'pending',
            'attempts': 0,
            'status_checks': 0,
            'payload': {
                'rfq_id': rfq.id,
                'rfq_revision': current_rev,
                'supplier_org_id': inv.supplier_org_id,
                'invitation_id': inv.id,
            },
        })
    for old in reinstated:
        outbox_rows.append({
            'id': str(uuid4()),
            'created_at': now_utc,
            'event': 'rfq.invitation',
            'dedupe_key': f'rfq-invitation:{old.id}:reinstated:{current_rev}:{uuid4().hex[:8]}',
            'status': 'pending',
            'attempts': 0,
            'status_checks': 0,
            'payload': {
                'rfq_id': rfq.id,
                'rfq_revision': current_rev,
                'supplier_org_id': old.supplier_org_id,
                'invitation_id': old.id,
            },
        })

    notification_jobs_queued = len(outbox_rows)
    quotation_jobs_queued = 0

    # Queue independent Private Price Book quotation jobs for suppliers with authorized automation.
    dispatch_invs = [*invitations, *reinstated]
    if dispatch_invs:
        dispatch_orgs = [inv.supplier_org_id for inv in dispatch_invs]
        auto_orgs = set()
        for oid, qa in db.execute(
            select(Organization.id, Organization.quote_automation).where(Organization.id.in_(dispatch_orgs))
        ).all():
            if isinstance(qa, dict) and qa.get('enabled'):
                auto_orgs.add(oid)
        for inv in dispatch_invs:
            if inv.supplier_org_id in auto_orgs:
                outbox_rows.append({
                    'id': str(uuid4()),
                    'created_at': now_utc,
                    'event': 'quotation.auto_prepare',
                    'dedupe_key': f'quotation-auto-prepare:{inv.id}',
                    'status': 'pending',
                    'attempts': 0,
                    'status_checks': 0,
                    'payload': {
                        'invitation_id': inv.id,
                        'rfq_id': rfq.id,
                        'rfq_revision': current_rev,
                        'supplier_org_id': inv.supplier_org_id,
                    },
                })
                quotation_jobs_queued += 1

    if outbox_rows:
        for chunk in _chunked(outbox_rows):
            stmt = _dialect_insert(db, Outbox).values(chunk).on_conflict_do_nothing(
                index_elements=['dedupe_key']
            )
            db.execute(stmt)

    if invitations:
        from app.modules.notifications.recipients import queue_invitations_in_app_batch
        queue_invitations_in_app_batch(db, invitations, rfq, outbox_id_by_inv)

    if invitations or existing_invs:
        if any(inv.status != 'cancelled' for inv in [*invitations, *existing_invs.values()]):
            rfq.status = 'collecting_quotes'

    db.flush()
    db_write_ms = (time.perf_counter() - write_t0) * 1000.0

    return {
        'next_cursor': candidate_ids[-1] if has_more else None,
        'eligible_suppliers': len(ranked),
        'new_invitations': len(invitations),
        'invitations': invitations,
        'evaluated_suppliers': len(touched),
        'ineligible_suppliers': len(ineligible),
        'superseded_invitations': superseded,
        'candidate_query_ms': round(candidate_query_ms, 2),
        'db_write_ms': round(db_write_ms, 2),
        'quotation_jobs_queued': quotation_jobs_queued,
        'notification_jobs_queued': notification_jobs_queued,
        'rejection_counts': rejection_counts,
    }


RECEIVED_DELIVERY_STATUSES = ('processed', 'awaiting_status', 'uncertain')


def supersede_plan(db, rfq, org_ids) -> dict:
    """Read-only: which active invitations of these suppliers would be cancelled or preserved."""
    from app.models import Quotation
    empty = {'cancel': [], 'preserve': [], 'deliveries': [], 'unsent_events': 0}
    if not org_ids:
        return empty
    candidates = db.scalars(select(Invitation).where(
        Invitation.rfq_id == rfq.id,
        Invitation.status.in_({'invited', 'accepted', 'declined'}),
        Invitation.supplier_org_id.in_(list(org_ids)),
    )).all()
    if not candidates:
        return empty
    ids = [inv.supplier_org_id for inv in candidates]
    answered = set(db.scalars(select(Quotation.supplier_org_id).where(
        Quotation.rfq_id == rfq.id, Quotation.supplier_org_id.in_(ids), Quotation.status != 'draft'
    )))
    deliveries = db.scalars(select(Outbox).where(
        Outbox.event == 'notification.delivery',
        Outbox.payload['context']['rfq_id'].as_string() == rfq.id,
        Outbox.payload['context']['org_id'].as_string().in_(ids),
    )).all()
    received = {e.payload['context']['org_id'] for e in deliveries if e.status in RECEIVED_DELIVERY_STATUSES}
    cancel = [
        inv for inv in candidates
        if inv.status == 'invited' and inv.supplier_org_id not in answered and inv.supplier_org_id not in received
    ]
    cancel_orgs = {inv.supplier_org_id for inv in cancel}
    unsent = sum(1 for e in deliveries if e.payload['context']['org_id'] in cancel_orgs and e.status in {'pending', 'retry'})
    if cancel:
        unsent += int(db.scalar(select(func.count()).select_from(Outbox).where(
            Outbox.event == 'rfq.invitation',
            Outbox.status.in_({'pending', 'retry'}),
            Outbox.payload['invitation_id'].as_string().in_([inv.id for inv in cancel]),
        )) or 0)
    return {
        'cancel': cancel,
        'preserve': [inv for inv in candidates if inv.supplier_org_id not in cancel_orgs],
        'deliveries': deliveries,
        'unsent_events': unsent,
    }


def supersede_invitations(db, rfq, reasons_by_org: dict[str, str], actor=None) -> int:
    """Cancel never-delivered invitations of suppliers that no longer qualify."""
    if not reasons_by_org:
        return 0
    from app.security import audit
    plan = supersede_plan(db, rfq, list(reasons_by_org))
    deliveries = plan['deliveries']
    now = datetime.now(timezone.utc)
    cancelled = 0
    cancel_ids = [inv.id for inv in plan['cancel']]
    inv_events_by_inv: dict[str, list[Outbox]] = {}
    if cancel_ids:
        for ev in db.scalars(select(Outbox).where(
            Outbox.event == 'rfq.invitation',
            Outbox.payload['invitation_id'].as_string().in_(cancel_ids),
            Outbox.status.in_({'pending', 'retry'}),
        )):
            inv_events_by_inv.setdefault(ev.payload.get('invitation_id'), []).append(ev)
    for inv in plan['cancel']:
        reason = reasons_by_org[inv.supplier_org_id][:200]
        inv.status, inv.cancelled_at, inv.cancellation_reason = 'cancelled', now, reason
        for event in inv_events_by_inv.get(inv.id, []):
            event.status, event.delivery_status, event.next_attempt_at = 'skipped', 'superseded_not_eligible', None
        for event in deliveries:
            if event.payload['context']['org_id'] == inv.supplier_org_id and event.status in {'pending', 'retry'}:
                event.status, event.delivery_status, event.next_attempt_at = 'skipped', 'superseded_not_eligible', None
        audit(
            db, actor, 'rfq.invitation_superseded', rfq.buyer_org_id, inv.id,
            rfq_id=rfq.id, supplier_org_id=inv.supplier_org_id, reason=reason,
        )
        cancelled += 1
    return cancelled


def eligible_supplier_ids(db, rfq, org_ids):
    return {oid for oid, reason in supplier_eligibility(db, rfq, org_ids).items() if reason is None}


BROAD_ONLY_REASON = (
    'review_required: only main-category, AI-suggested or unconfirmed delivery-city evidence; '
    'shown to the buyer for review, never invited automatically'
)
NOT_CANDIDATE_REASON = 'not_matched: no capability, product or price-list evidence for any RFQ line'


def supplier_eligibility(db, rfq, org_ids) -> dict[str, str | None]:
    return {oid: entry['reason'] for oid, entry in supplier_evaluations(db, rfq, org_ids).items()}


def supplier_evaluations(db, rfq, org_ids) -> dict[str, dict]:
    memo = db.info.setdefault('eligibility_memo', {})
    ids = [oid for oid in dict.fromkeys(org_ids) if (rfq.id, oid) not in memo]
    for start in range(0, len(ids), 250):
        page = ids[start:start + 250]
        result = _match_batch(db, rfq, evaluate_only=True, supplier_ids=page)
        auto = set(result.get('auto_invite_ids', []))
        shown = set(result.get('eligible_ids', []))
        detail = result.get('ineligible_detail', {})
        for oid in page:
            memo[(rfq.id, oid)] = {
                'reason': (
                    None if oid in auto else BROAD_ONLY_REASON if oid in shown
                    else detail.get(oid, NOT_CANDIDATE_REASON)
                ),
                'covered_items': result.get('covered_items', {}).get(oid, []),
                'item_evidence': result.get('item_evidence', {}).get(oid, {}),
            }
    return {oid: memo[(rfq.id, oid)] for oid in org_ids}


def _emit_matching_log(
    *,
    rfq_id: str | None,
    rfq_revision: int,
    job_id: str,
    queue_wait_ms: float,
    candidate_query_ms: float,
    candidates_found: int,
    filtered_suppliers: int,
    rejection_counts: dict[str, int],
    db_write_ms: float,
    total_matching_ms: float,
    quotation_jobs_queued: int,
    notification_jobs_queued: int,
    status: str,
    worker_error: str | None = None,
) -> None:
    record_data = {
        'event': 'rfq.matching.telemetry',
        'rfq_id': rfq_id,
        'rfq_revision': rfq_revision,
        'job_id': job_id,
        'status': status,
        'queue_wait_ms': round(queue_wait_ms, 1),
        'candidate_query_ms': round(candidate_query_ms, 1),
        'candidates_found': candidates_found,
        'filtered_suppliers': filtered_suppliers,
        'rejection_counts': rejection_counts,
        'db_write_ms': round(db_write_ms, 1),
        'total_matching_ms': round(total_matching_ms, 1),
        'quotation_jobs_queued': quotation_jobs_queued,
        'notification_jobs_queued': notification_jobs_queued,
        'worker_error': worker_error,
    }
    if worker_error:
        log.error('rfq_matching %s', json.dumps(record_data, sort_keys=True))
    else:
        log.info('rfq_matching %s', json.dumps(record_data, sort_keys=True))


def run_matching_job(
    bind,
    job_id: str,
    *,
    time_budget: float | None,
    restart: bool = False,
    leased: bool = False,
) -> dict | None:
    """Advance one durable rfq.published job with revision safety and structured telemetry."""
    from sqlalchemy.orm import sessionmaker
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    started = time.monotonic()
    summary: dict | None = None
    first = True
    while True:
        with sessions() as db:
            rfq_id = None
            rfq_rev = 0
            queue_wait_ms = 0.0
            try:
                job = db.scalar(
                    select(Outbox)
                    .where(
                        Outbox.id == job_id,
                        Outbox.status.in_({'processing'} if leased else {'pending', 'retry'}),
                    )
                    .with_for_update(skip_locked=True)
                )
                if job is None:
                    db.rollback()
                    return summary
                rfq_id = (job.payload or {}).get('rfq_id')
                # Serialize each page with revision publication. Without the RFQ
                # row lock, an older worker can pass the revision check and then
                # commit its match/invitation writes after a newer revision.
                rfq = db.scalar(select(RFQ).where(RFQ.id == rfq_id).with_for_update()) if rfq_id else None
                if rfq is None or rfq.status not in {'published', 'collecting_quotes'}:
                    db.rollback()
                    return summary
                rfq_rev = int(getattr(rfq, 'revision', 0) or 0)
                job_rev = (job.payload or {}).get('revision')
                if job.created_at:
                    queue_wait_ms = max(0.0, (datetime.now(timezone.utc) - as_utc(job.created_at)).total_seconds() * 1000.0)

                # Stale-revision protection: never let an older job overwrite a newer RFQ revision.
                if job_rev is not None and int(job_rev) < rfq_rev:
                    job.status = 'skipped'
                    job.delivery_status = 'stale_rfq_revision'
                    job.next_attempt_at = None
                    job.lock_until = None
                    db.commit()
                    _emit_matching_log(
                        rfq_id=rfq.id, rfq_revision=rfq_rev, job_id=job.id, queue_wait_ms=queue_wait_ms,
                        candidate_query_ms=0.0, candidates_found=0, filtered_suppliers=0, rejection_counts={},
                        db_write_ms=0.0, total_matching_ms=0.0, quotation_jobs_queued=0, notification_jobs_queued=0,
                        status='skipped_stale_revision',
                    )
                    return summary

                # Revision-based idempotency: if another job already completed matching for this exact revision & fingerprint,
                # skip redundant re-evaluation unless an explicit admin rerun/restart was requested.
                active_items = db.scalars(
                    select(RFQItem).where(RFQItem.rfq_id == rfq.id, RFQItem.is_active.is_(True))
                    .order_by(RFQItem.created_at, RFQItem.id)
                ).all()
                fp = rfq_fingerprint(rfq, active_items)
                is_explicit_rerun = bool(
                    restart or (job.payload or {}).get('requested_by') or (job.payload or {}).get('repaired')
                )
                if not is_explicit_rerun and not (job.payload or {}).get('cursor'):
                    already_done = db.scalar(select(Outbox.id).where(
                        Outbox.id != job.id,
                        Outbox.event == 'rfq.published',
                        Outbox.status == 'processed',
                        Outbox.payload['rfq_id'].as_string() == rfq.id,
                        Outbox.payload['fingerprint'].as_string() == fp,
                    ).limit(1))
                    if already_done:
                        job.status = 'skipped'
                        job.delivery_status = 'duplicate_revision_match'
                        job.next_attempt_at = None
                        job.lock_until = None
                        db.commit()
                        matched_cnt = int(db.scalar(select(func.count()).select_from(RFQMatch).where(
                            RFQMatch.rfq_id == rfq.id, RFQMatch.status.in_(MATCHED_STATUSES)
                        )) or 0)
                        return {
                            'eligible_suppliers': matched_cnt, 'new_invitations': 0, 'invitations': [],
                            'evaluated_suppliers': 0, 'ineligible_suppliers': 0, 'next_cursor': None,
                        }

                page_started = time.monotonic()
                fresh = restart and first
                changed = (
                    None if job.payload.get('full_rematch') or 'matching_item_ids' not in job.payload
                    else set(job.payload['matching_item_ids'])
                )
                saved_cursor = None if fresh else job.payload.get('cursor')
                skip_rev = (
                    rfq_rev
                    if (not fresh and not is_explicit_rerun and changed is None and rfq_rev > 0 and saved_cursor is not None)
                    else None
                )
                page = match_and_invite(
                    db,
                    rfq,
                    bounded=True,
                    single_batch=True,
                    limit=get_settings().matching_batch_size,
                    after_supplier_id=saved_cursor,
                    changed_item_ids=changed,
                    expected_revision=int(job_rev) if job_rev is not None else rfq_rev,
                    skip_evaluated_revision=skip_rev,
                )
                if page.get('stale_revision'):
                    db.rollback()
                    with sessions() as s2:
                        j2 = s2.get(Outbox, job_id)
                        if j2:
                            j2.status = 'skipped'
                            j2.delivery_status = 'stale_rfq_revision'
                            j2.lock_until = None
                            s2.commit()
                    return summary

                cursor = page.get('next_cursor')
                payload = {key: value for key, value in job.payload.items() if key != 'cursor'}
                previous_ms = 0.0 if fresh else float(job.payload.get('matching_duration_ms') or 0.0)
                previous_evaluated = 0 if fresh else int(job.payload.get('evaluated_suppliers') or 0)
                previous_cand_ms = 0.0 if fresh else float(job.payload.get('candidate_query_ms') or 0.0)
                previous_write_ms = 0.0 if fresh else float(job.payload.get('db_write_ms') or 0.0)
                previous_quote_jobs = 0 if fresh else int(job.payload.get('quotation_jobs_queued') or 0)
                previous_notif_jobs = 0 if fresh else int(job.payload.get('notification_jobs_queued') or 0)
                merged_rejections = {} if fresh else dict(job.payload.get('rejection_counts') or {})
                for rk, rv in (page.get('rejection_counts') or {}).items():
                    merged_rejections[rk] = merged_rejections.get(rk, 0) + rv

                page_ms = (time.monotonic() - page_started) * 1000.0
                total_ms = round(previous_ms + page_ms, 1)
                total_eval = previous_evaluated + page.get('evaluated_suppliers', 0)
                total_cand_ms = round(previous_cand_ms + float(page.get('candidate_query_ms', 0.0)), 1)
                total_write_ms = round(previous_write_ms + float(page.get('db_write_ms', 0.0)), 1)
                total_quote_jobs = previous_quote_jobs + page.get('quotation_jobs_queued', 0)
                total_notif_jobs = previous_notif_jobs + page.get('notification_jobs_queued', 0)

                payload.update(
                    progressed_at=datetime.now(timezone.utc).isoformat(),
                    matching_duration_ms=total_ms,
                    evaluated_suppliers=total_eval,
                    candidate_query_ms=total_cand_ms,
                    db_write_ms=total_write_ms,
                    queue_wait_ms=round(queue_wait_ms, 1),
                    quotation_jobs_queued=total_quote_jobs,
                    notification_jobs_queued=total_notif_jobs,
                    rejection_counts=merged_rejections,
                    rfq_revision=rfq_rev,
                    fingerprint=fp,
                )
                if cursor:
                    payload['cursor'] = cursor
                else:
                    payload['completed_at'] = payload['progressed_at']
                job.payload = payload
                over_budget = time_budget is not None and time.monotonic() - started >= time_budget
                eligible_total = int(db.scalar(select(func.count()).select_from(RFQMatch).where(
                    RFQMatch.rfq_id == rfq.id, RFQMatch.status.in_({'eligible', 'partial'})
                )) or 0)
                if not cursor and job.payload.get('revision'):
                    from app.models import RFQRevision
                    revision = db.scalar(select(RFQRevision).where(
                        RFQRevision.rfq_id == rfq.id, RFQRevision.number == job.payload['revision']
                    ))
                    if revision:
                        revision.matching_summary = {
                            'evaluated_suppliers': total_eval,
                            'eligible_suppliers': eligible_total,
                            'active_invitations': int(db.scalar(select(func.count()).select_from(Invitation).where(
                                Invitation.rfq_id == rfq.id, Invitation.status != 'cancelled'
                            )) or 0),
                            'completed_at': payload['completed_at'],
                        }
                job.status = 'processed' if not cursor else ('processing' if leased and not over_budget else 'pending')
                if job.status != 'processing':
                    job.lock_until = None
                job.next_attempt_at = None
                job.last_error = None
                db.commit()

                if not cursor:
                    _emit_matching_log(
                        rfq_id=rfq.id,
                        rfq_revision=rfq_rev,
                        job_id=job.id,
                        queue_wait_ms=queue_wait_ms,
                        candidate_query_ms=total_cand_ms,
                        candidates_found=total_eval,
                        filtered_suppliers=eligible_total,
                        rejection_counts=merged_rejections,
                        db_write_ms=total_write_ms,
                        total_matching_ms=total_ms,
                        quotation_jobs_queued=total_quote_jobs,
                        notification_jobs_queued=total_notif_jobs,
                        status='completed',
                    )
            except Exception as exc:
                db.rollback()
                _emit_matching_log(
                    rfq_id=rfq_id,
                    rfq_revision=rfq_rev,
                    job_id=job_id,
                    queue_wait_ms=queue_wait_ms,
                    candidate_query_ms=0.0,
                    candidates_found=0,
                    filtered_suppliers=0,
                    rejection_counts={},
                    db_write_ms=0.0,
                    total_matching_ms=(time.monotonic() - started) * 1000.0,
                    quotation_jobs_queued=0,
                    notification_jobs_queued=0,
                    status='error',
                    worker_error=type(exc).__name__,
                )
                raise
        first = False
        if summary is None:
            summary = {
                'eligible_suppliers': 0, 'new_invitations': 0, 'invitations': [],
                'evaluated_suppliers': 0, 'ineligible_suppliers': 0,
            }
        for key in ('eligible_suppliers', 'new_invitations', 'evaluated_suppliers', 'ineligible_suppliers'):
            summary[key] += page.get(key, 0)
        summary['invitations'].extend(page.get('invitations', []))
        summary['next_cursor'] = cursor
        if not cursor or (time_budget is not None and time.monotonic() - started >= time_budget):
            return summary
