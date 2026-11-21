"""Category review classification, safe discipline inference, and batched repair service."""
from __future__ import annotations

import difflib
import logging
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app import catalog_cache
from app.database import SessionLocal
from app.models import (
    Capability,
    Category,
    CategoryReviewDecision,
    ImportBatch,
    ImportedRow,
    Material,
    MaterialRevision,
    User,
    utcnow,
)
from app.modules.materials.service import material_identity, material_snapshot, normalize_category_name, normalize_text
from app.modules.taxonomy.duplicates import category_references
from app.security import audit

logger = logging.getLogger(__name__)

SUPPORTED_DISCIPLINES: dict[str, str] = {
    'civil': 'Civil',
    'electrical': 'Electrical',
    'mechanical': 'Mechanical',
}
BATCH_SIZE = 30
JOB_SIZE = 300
CATEGORY_REVIEW_LOCK_KEY = 717341399

SUSPICIOUS_LABEL_RE = re.compile(r'\b(qa|test|sample|bulk)\b', re.IGNORECASE)

DISCIPLINE_ALIASES: dict[str, str] = {
    'elec': 'Electrical',
    'electrical works': 'Electrical',
    'electrical systems': 'Electrical',
    'elv': 'Electrical',
    'low current': 'Electrical',
    'power': 'Electrical',
    'mech': 'Mechanical',
    'mechanical works': 'Mechanical',
    'mechanical systems': 'Mechanical',
    'hvac': 'Mechanical',
    'plumbing': 'Mechanical',
    'firefighting': 'Mechanical',
    'fire fighting': 'Mechanical',
    'civil works': 'Civil',
    'structural': 'Civil',
    'concrete': 'Civil',
    'architectural': 'Civil',
    'arch': 'Civil',
    'finishes': 'Civil',
}

# Categories that genuinely span multiple disciplines or are too generic to infer
# discipline from the category label alone without inspecting each material.
SHARED_OR_GENERIC_PATTERNS = (
    re.compile(r'\bshared\b', re.IGNORECASE),
    re.compile(r'\bconsumables?\b', re.IGNORECASE),
    re.compile(r'\badhesives?\b', re.IGNORECASE),
    re.compile(r'\bresins?\b', re.IGNORECASE),
    re.compile(r'\banchors?\b', re.IGNORECASE),
    re.compile(r'\bfasteners?\b', re.IGNORECASE),
    re.compile(r'\bsealants?\b', re.IGNORECASE),
    re.compile(r'\bchemicals?\b', re.IGNORECASE),
    re.compile(r'\baccessories\b', re.IGNORECASE),
    re.compile(r'\bgeneral\b', re.IGNORECASE),
    re.compile(r'\bmisc(?:ellaneous)?\b', re.IGNORECASE),
    re.compile(r'\btools?\b', re.IGNORECASE),
)

CIVIL_KEYWORDS = re.compile(
    r'\b('
    r'concrete|cement|admixtures?|ready[\s-]?mix|aggregate|rebar|reinforcing\s+steel|'
    r'masonry|blockwork|brickwork|plaster|mortar|screed|waterproofing|bitumen|'
    r'geotextile|asphalt|kerb|curbstone|paver|gypsum|drywall|ceiling\s+(?:board|tile|panel)|'
    r'acoustic\s+(?:board|panel|tile|ceiling|baffle|partition|wall)|'
    r'air\s*(?:&|and)?\s*vapou?r\s+control|vapou?r\s+barrier|air\s+barrier|'
    r'architectural\s+insulation|building\s+envelope|thermal\s+facade|cladding|'
    r'formwork|scaffolding|structural\s+steel|terrazzo|concrete\s+anchor|chemical\s+anchor|'
    r'wedge\s+anchor|rebar\s+epoxy|structural\s+epoxy|concrete\s+fibre|concrete\s+fiber|'
    r'polypropylene\s+fib(?:re|er)|steel\s+fib(?:re|er)|tile\s+adhesive|grout|'
    r'architectural\s+metalwork|handrails?|balustrades?|louvers?|civil'
    r')\b',
    re.IGNORECASE,
)

ELECTRICAL_KEYWORDS = re.compile(
    r'\b('
    r'cable|wire|wiring|switchgear|circuit\s+breaker|mcb|mccb|rccb|rcbo|'
    r'distribution\s+board|panelboard|transformer|busbar|conduit|cable\s+tray|'
    r'trunking|junction\s+box|pull\s+box|socket|switch(?:es)?|luminaire|lighting\s+fixture|'
    r'led\s+(?:light|lamp|panel|flood)|lamp|ups|earthing|grounding|lightning\s+protection|'
    r'patch\s+panel|cat6|cat6a|fib(?:re|er)\s+optic|fire\s+alarm|cctv|access\s+control|'
    r'elv|low\s+current|cable\s+gland|cable\s+lug|cable\s+tie|terminal\s+block|'
    r'contactor|relay|isolator|busduct|electrical|voltage|ampere|kva|kw|xlpe|pvc\s+conduit'
    r')\b',
    re.IGNORECASE,
)

MECHANICAL_KEYWORDS = re.compile(
    r'\b('
    r'hvac|chiller|ahu|fcu|duct(?:work)?|duct\s+insulation|duct\s+acoustic|damper|'
    r'diffuser|grille|exhaust\s+fan|ventilation|pump|valve|gate\s+valve|ball\s+valve|'
    r'butterfly\s+valve|check\s+valve|pipe|piping|hdpe\s+pipe|ppr\s+pipe|upvc\s+pipe|'
    r'cpvc\s+pipe|black\s+steel\s+pipe|ductile\s+iron\s+pipe|pipe\s+fitting|flange|'
    r'pipe\s+clamp|pipe\s+hanger|pipe\s+insulation|chilled\s+water|fire\s+sprinkler|'
    r'hydrant|fire\s+hose|plumbing|sanitary\s+ware|water\s+heater|water\s+tank|'
    r'boiler|compressor|refrigerant|air\s+conditioning|mechanical'
    r')\b',
    re.IGNORECASE,
)

UNAMBIGUOUS_HIERARCHY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'\b(concrete\s+constituents|supplied\s+concrete|admixtures|reinforcing\s+steel|masonry|waterproofing|structural\s+concrete|architectural\s+insulation|air\s*/\s*vapor\s+control|air\s*&\s*vapor\s+control|architectural\s+specialties|architectural\s+metalwork)\b', re.IGNORECASE), 'Civil'),
    (re.compile(r'\b(electrical\s+distribution|wiring\s+devices|lighting\s+fixtures|low\s+current|switchgear|cables?\s*&\s*wires?)\b', re.IGNORECASE), 'Electrical'),
    (re.compile(r'\b(hvac|plumbing|fire\s*fighting|firefighting|chilled\s+water|pumps?\s*&\s*valves?|ductwork|sanitary)\b', re.IGNORECASE), 'Mechanical'),
)

ISSUE_LABELS: dict[str, str] = {
    'missing_parent': 'Missing parent',
    'archived_parent': 'Archived parent',
    'hierarchy_depth_exceeded': 'Exceeds two-level hierarchy',
    'invalid_import_data': 'Invalid import data',
    'duplicate_category': 'Duplicate category',
    'parent_mismatch': 'Parent category mismatch',
    'unsupported_discipline': 'Unsupported discipline',
    'missing_discipline': 'Missing discipline',
    'ambiguous_classification': 'Ambiguous classification',
    'inactive_materials_only': 'Inactive materials only',
    'empty_category': 'Empty category',
}

HIERARCHY_BLOCKING_ISSUES: frozenset[str] = frozenset({
    'missing_parent',
    'archived_parent',
    'hierarchy_depth_exceeded',
})


@dataclass(frozen=True)
class ReviewActor:
    """Detached scalar actor snapshot safe to pass across short batch sessions."""

    id: str
    username: str = ''


@dataclass
class MaterialAssessment:
    material_id: str
    code: str
    name_en: str
    current_discipline: str | None
    inferred_discipline: str | None
    safe: bool
    evidence: str
    issue: str  # ok | missing_discipline | unsupported_discipline | ambiguous | parent_mismatch


@dataclass
class HierarchyRepairPlan:
    category_id: str
    code: str
    name_en: str
    parent_id: str | None
    parent_name_en: str | None
    grandparent_id: str | None
    grandparent_name_en: str | None
    current_path: list[str]
    proposed_path: list[str]
    repair_action: str  # reparent_to_main | merge_into_canonical_subcategory | promote_parent_to_main | manual_review
    target_parent_id: str | None = None
    target_category_id: str | None = None
    affected_materials_count: int = 0
    affected_capabilities_count: int = 0
    safe: bool = False
    status: str = 'manual_review'  # safe | manual_review
    reason: str = ''


@dataclass
class CategoryAssessment:
    category: Category
    parent: Category | None
    issue_type: str
    reason: str
    category_id: str = ''
    category_code: str = ''
    category_name_en: str = ''
    category_name_ar: str = ''
    parent_id: str | None = None
    parent_name_en: str | None = None
    hierarchy_path: list[str] = field(default_factory=list)
    proposed_path: list[str] = field(default_factory=list)
    hierarchy_repair_action: str | None = None
    hierarchy_repair_safe: bool = False
    affected_capabilities_count: int = 0
    secondary_issues: list[str] = field(default_factory=list)
    active_material_count: int = 0
    inactive_material_count: int = 0
    missing_discipline_count: int = 0
    unsupported_discipline_count: int = 0
    duplicate_ids: list[str] = field(default_factory=list)
    duplicate_canonical_id: str | None = None
    duplicate_safe: bool = False
    duplicate_blockers: list[str] = field(default_factory=list)
    proposed_action: str = 'manual_review'  # assign_discipline | repair_parent | merge_duplicate | retain_empty | approve | manual_review
    proposed_discipline: str | None = None
    safe_to_auto_fix: bool = False
    can_approve: bool = False
    can_retain_empty: bool = False
    can_merge: bool = False
    can_archive: bool = False
    requires_manual_review: bool = False
    evidence: list[str] = field(default_factory=list)
    current_decision: CategoryReviewDecision | None = None
    current_decision_value: str | None = None
    material_assessments: list[MaterialAssessment] = field(default_factory=list)
    safe_material_fixes: dict[str, str] = field(default_factory=dict)  # material_id -> discipline
    parent_fix_material_ids: list[str] = field(default_factory=list)


def normalize_supported_discipline(value: str | None) -> str | None:
    if not value:
        return None
    return SUPPORTED_DISCIPLINES.get(value.strip().casefold())


def _is_shared_or_generic(name: str) -> bool:
    return any(pattern.search(name or '') for pattern in SHARED_OR_GENERIC_PATTERNS)


def _text_disciplines(text_value: str) -> set[str]:
    found: set[str] = set()
    if not text_value:
        return found
    if CIVIL_KEYWORDS.search(text_value):
        found.add('Civil')
    if ELECTRICAL_KEYWORDS.search(text_value):
        found.add('Electrical')
    if MECHANICAL_KEYWORDS.search(text_value):
        found.add('Mechanical')
    return found


def _hierarchy_discipline(category: Category, parent: Category | None) -> tuple[str | None, bool]:
    """Return (inferred_discipline, is_shared_or_generic)."""
    combined = f'{parent.name_en if parent else ""} {category.name_en}'.strip()
    is_shared = _is_shared_or_generic(category.name_en) or (parent is not None and _is_shared_or_generic(parent.name_en))
    if parent and parent.name_en.strip().casefold() in SUPPORTED_DISCIPLINES:
        return SUPPORTED_DISCIPLINES[parent.name_en.strip().casefold()], is_shared
    if not parent and category.name_en.strip().casefold() in SUPPORTED_DISCIPLINES:
        return SUPPORTED_DISCIPLINES[category.name_en.strip().casefold()], is_shared
    if is_shared:
        return None, True
    matches: set[str] = set()
    for pattern, disc in UNAMBIGUOUS_HIERARCHY_PATTERNS:
        if pattern.search(combined):
            matches.add(disc)
    if len(matches) == 1:
        return next(iter(matches)), False
    return None, is_shared


def _source_discipline_from_metadata(
    material: Material,
    batches_by_id: dict[str, ImportBatch],
    rows_by_key: dict[tuple[str, int], ImportedRow],
) -> str | None:
    if material.import_batch_id and material.import_row_number is not None:
        row = rows_by_key.get((material.import_batch_id, material.import_row_number))
        if row and isinstance(row.raw, dict):
            for key in ('_discipline', 'discipline', 'Discipline', 'trade', 'Trade'):
                val = row.raw.get(key)
                if isinstance(val, str):
                    resolved = normalize_supported_discipline(val)
                    if resolved:
                        return resolved
    if material.import_batch_id:
        batch = batches_by_id.get(material.import_batch_id)
        if batch:
            meta = batch.preview_metadata or {}
            if isinstance(meta, dict):
                resolved = normalize_supported_discipline(str(meta.get('discipline') or ''))
                if resolved:
                    return resolved
            evidence = re.sub(r'[^a-z]+', ' ', (batch.filename or '').casefold())
            hits = [label for key, label in SUPPORTED_DISCIPLINES.items() if re.search(rf'\b{key}\b', evidence)]
            if len(hits) == 1:
                return hits[0]
    return None


def _assess_material_discipline(
    material: Material,
    category: Category,
    parent: Category | None,
    sibling_disciplines: set[str],
    batches_by_id: dict[str, ImportBatch],
    rows_by_key: dict[tuple[str, int], ImportedRow],
) -> MaterialAssessment:
    current = (material.discipline or '').strip() or None
    supported = normalize_supported_discipline(current)
    if supported:
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=supported,
            safe=True,
            evidence='existing_supported_discipline',
            issue='ok',
        )

    base_issue = 'unsupported_discipline' if current else 'missing_discipline'
    spec_text = ' '.join(
        str(v) for v in (material.specs_template or {}).values() if isinstance(v, (str, int, float))
    )
    material_text = f'{material.name_en or ""} {material.description or ""} {spec_text}'.strip()
    text_hits = _text_disciplines(material_text)

    if len(text_hits) > 1:
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=None,
            safe=False,
            evidence=f'Conflicting description signals ({", ".join(sorted(text_hits))})',
            issue='ambiguous',
        )

    source_disc = _source_discipline_from_metadata(material, batches_by_id, rows_by_key)
    if source_disc:
        if not text_hits or text_hits == {source_disc}:
            return MaterialAssessment(
                material_id=material.id,
                code=material.code,
                name_en=material.name_en,
                current_discipline=current,
                inferred_discipline=source_disc,
                safe=True,
                evidence=f'Source import metadata ({source_disc})',
                issue=base_issue,
            )
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=None,
            safe=False,
            evidence=f'Source import ({source_disc}) conflicts with description ({", ".join(sorted(text_hits))})',
            issue='ambiguous',
        )

    if current:
        alias_disc = DISCIPLINE_ALIASES.get(current.casefold())
        if alias_disc:
            if not text_hits or text_hits == {alias_disc}:
                return MaterialAssessment(
                    material_id=material.id,
                    code=material.code,
                    name_en=material.name_en,
                    current_discipline=current,
                    inferred_discipline=alias_disc,
                    safe=True,
                    evidence=f'Normalized discipline alias "{current}" -> {alias_disc}',
                    issue=base_issue,
                )
            return MaterialAssessment(
                material_id=material.id,
                code=material.code,
                name_en=material.name_en,
                current_discipline=current,
                inferred_discipline=None,
                safe=False,
                evidence=f'Discipline value "{current}" conflicts with description ({", ".join(sorted(text_hits))})',
                issue='ambiguous',
            )
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=None,
            safe=False,
            evidence=f'Unsupported discipline "{current}" requires manual review',
            issue='unsupported_discipline',
        )

    hier_disc, is_shared = _hierarchy_discipline(category, parent)

    if is_shared:
        # Shared / generic categories (e.g., Shared Chemicals, Anchors & Consumables,
        # or Electrical -> Accessories) require material-level evidence rather than
        # blindly inheriting one discipline.
        if len(text_hits) == 1:
            inferred = next(iter(text_hits))
            if hier_disc and inferred != hier_disc:
                return MaterialAssessment(
                    material_id=material.id,
                    code=material.code,
                    name_en=material.name_en,
                    current_discipline=current,
                    inferred_discipline=None,
                    safe=False,
                    evidence=f'Description ({inferred}) conflicts with parent hierarchy ({hier_disc})',
                    issue='ambiguous',
                )
            return MaterialAssessment(
                material_id=material.id,
                code=material.code,
                name_en=material.name_en,
                current_discipline=current,
                inferred_discipline=inferred,
                safe=True,
                evidence=f'Material description and specifications ({inferred})',
                issue=base_issue,
            )
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=None,
            safe=False,
            evidence='Shared/generic category material lacks single-discipline evidence',
            issue='ambiguous',
        )

    if hier_disc:
        if text_hits and text_hits != {hier_disc}:
            return MaterialAssessment(
                material_id=material.id,
                code=material.code,
                name_en=material.name_en,
                current_discipline=current,
                inferred_discipline=None,
                safe=False,
                evidence=f'Description ({", ".join(sorted(text_hits))}) conflicts with category hierarchy ({hier_disc})',
                issue='ambiguous',
            )
        if sibling_disciplines and sibling_disciplines != {hier_disc}:
            return MaterialAssessment(
                material_id=material.id,
                code=material.code,
                name_en=material.name_en,
                current_discipline=current,
                inferred_discipline=None,
                safe=False,
                evidence=f'Existing sibling materials span multiple disciplines ({", ".join(sorted(sibling_disciplines))})',
                issue='ambiguous',
            )
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=hier_disc,
            safe=True,
            evidence=f'Category hierarchy ({hier_disc})' + (' confirmed by description' if text_hits else ''),
            issue=base_issue,
        )

    if len(sibling_disciplines) == 1:
        sib_disc = next(iter(sibling_disciplines))
        if not text_hits or text_hits == {sib_disc}:
            return MaterialAssessment(
                material_id=material.id,
                code=material.code,
                name_en=material.name_en,
                current_discipline=current,
                inferred_discipline=sib_disc,
                safe=True,
                evidence=f'Unanimous sibling materials in category ({sib_disc})',
                issue=base_issue,
            )
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=None,
            safe=False,
            evidence=f'Description ({", ".join(sorted(text_hits))}) conflicts with sibling materials ({sib_disc})',
            issue='ambiguous',
        )

    if len(text_hits) == 1 and not sibling_disciplines:
        inferred = next(iter(text_hits))
        return MaterialAssessment(
            material_id=material.id,
            code=material.code,
            name_en=material.name_en,
            current_discipline=current,
            inferred_discipline=inferred,
            safe=True,
            evidence=f'Material description and specifications ({inferred})',
            issue=base_issue,
        )

    return MaterialAssessment(
        material_id=material.id,
        code=material.code,
        name_en=material.name_en,
        current_discipline=current,
        inferred_discipline=None,
        safe=False,
        evidence='Insufficient evidence to assign a supported discipline automatically',
        issue='ambiguous',
    )


def _load_current_decisions(db: Session) -> dict[str, CategoryReviewDecision]:
    try:
        rows = db.scalars(
            select(CategoryReviewDecision).where(CategoryReviewDecision.is_current.is_(True))
        ).all()
        return {row.category_id: row for row in rows}
    except ProgrammingError:
        db.rollback()
        return {}


def _duplicate_group_safety(
    db: Session,
    group: list[Category],
    all_categories: list[Category],
    active_mat_counts: dict[str, int],
    materials_by_cat: dict[str, list[Material]] | None = None,
    disciplines_by_cat: dict[str, set[str]] | None = None,
) -> tuple[Category, bool, list[str]]:
    """Determine canonical category and whether a same-parent duplicate group is safe to merge."""
    ids = {c.id for c in group}
    reasons: list[str] = []

    # Never merge across different parents (caller already groups by parent_id, double-check)
    if len({c.parent_id for c in group}) > 1:
        reasons.append('Categories belong to different parent categories.')

    # Choose canonical: prefer the one with the most active materials, then earliest created_at, then id
    ordered = sorted(
        group,
        key=lambda c: (-active_mat_counts.get(c.id, 0), c.created_at, c.id),
    )
    canonical = ordered[0]
    source_ids = {c.id for c in ordered[1:]}

    # Do not merge if any duplicate alias being archived has active children
    if any(c.parent_id in source_ids and c.active for c in all_categories):
        reasons.append('Duplicate alias has active subcategories; merge leaf categories first.')

    # Check Arabic label disagreement
    arabic = {normalize_category_name(c.name_ar) for c in group if c.name_ar and c.name_ar.strip()}
    if len(arabic) > 1:
        reasons.append('Arabic category names disagree.')

    # Check conflicting disciplines across active materials in the duplicate group
    if disciplines_by_cat is not None:
        group_disciplines = {d for cid in ids for d in disciplines_by_cat.get(cid, set())}
    else:
        group_disciplines = {
            normalize_supported_discipline(m.discipline)
            for cid in ids
            for m in (materials_by_cat or {}).get(cid, [])
            if m.active and normalize_supported_discipline(m.discipline)
        }
    if len(group_disciplines) > 1:
        reasons.append(f'Active materials span conflicting disciplines ({", ".join(sorted(group_disciplines))}).')

    # Check supplier capability overlap
    caps = list(db.scalars(select(Capability).where(Capability.category_id.in_(ids))).all())
    cap_keys = [(c.org_id, c.city) for c in caps]
    if len(set(cap_keys)) != len(cap_keys):
        reasons.append('Supplier capability areas overlap; review capability approvals before merging.')

    # Check material identity overlap across the duplicate categories
    mats = list(db.scalars(select(Material).where(Material.category_id.in_(ids))).all())
    mat_keys = [(normalize_category_name(m.name_en), m.unit) for m in mats]
    if len(set(mat_keys)) != len(mat_keys):
        reasons.append('Material identities overlap across duplicate categories; review before merging.')

    return canonical, not reasons, reasons


def _build_hierarchy_path(category: Category, by_id: dict[str, Category]) -> tuple[list[str], int]:
    """Return (root-to-leaf category name chain, hierarchy depth) with cycle protection."""
    chain: list[str] = [category.name_en]
    visited: set[str] = {category.id}
    cur = category
    while cur.parent_id:
        if cur.parent_id in visited:
            chain.append('(cycle)')
            break
        visited.add(cur.parent_id)
        ancestor = by_id.get(cur.parent_id)
        if ancestor is None:
            break
        chain.append(ancestor.name_en)
        cur = ancestor
    chain.reverse()
    return chain, len(chain)


def _find_near_duplicate_root(
    normalized_name: str,
    root_categories: list[Category],
    *,
    exclude_id: str | None = None,
    threshold: float = 0.85,
) -> Category | None:
    """Detect probable spelling typos against existing active root categories (e.g. Netowrk vs Network)."""
    if not normalized_name or len(normalized_name) < 5:
        return None
    best: Category | None = None
    best_ratio = 0.0
    for root in root_categories:
        if exclude_id and root.id == exclude_id:
            continue
        r_norm = normalize_category_name(root.name_en)
        if not r_norm or r_norm == normalized_name:
            continue
        ratio = difflib.SequenceMatcher(None, normalized_name, r_norm).ratio()
        if ratio >= threshold and ratio > best_ratio:
            best_ratio = ratio
            best = root
    return best


def _plan_hierarchy_repairs(
    db: Session,
    *,
    by_id: dict[str, Category],
    active_categories: list[Category],
    active_children_by_parent: dict[str, list[Category]],
    total_mat_counts: dict[str, int],
    sibling_supported_by_cat: dict[str, set[str]],
    cap_keys_by_cat: dict[str, set[tuple[str, str]]],
    cap_counts_by_cat: dict[str, int],
) -> dict[str, HierarchyRepairPlan]:
    """Compute safe, deterministic two-level hierarchy repair plans for all depth > 2 categories."""
    roots = [c for c in active_categories if c.parent_id is None]
    roots_by_norm: dict[str, list[Category]] = defaultdict(list)
    for r in roots:
        roots_by_norm[normalize_category_name(r.name_en)].append(r)

    children_by_parent_norm: dict[tuple[str, str], list[Category]] = defaultdict(list)
    for c in active_categories:
        if c.parent_id:
            children_by_parent_norm[(c.parent_id, normalize_category_name(c.name_en))].append(c)

    # Pre-identify candidate merge pairs that have materials so we can batch-check material identity overlap
    plans: dict[str, HierarchyRepairPlan] = {}
    depth3_items: list[Category] = [
        c
        for c in active_categories
        if c.parent_id
        and (parent := by_id.get(c.parent_id)) is not None
        and parent.active
        and parent.parent_id is not None
    ]
    if not depth3_items:
        return plans

    # Pre-check material identity keys only for categories in depth3_items that actually have materials
    mat_ids_to_check: set[str] = set()
    for c in depth3_items:
        if total_mat_counts.get(c.id, 0) > 0:
            mat_ids_to_check.add(c.id)

    mat_keys_by_cat: dict[str, set[tuple[str, str]]] = defaultdict(set)
    if mat_ids_to_check:
        # Also include potential target subcategories that might receive merges from categories with materials
        potential_targets: set[str] = set(mat_ids_to_check)
        for c in depth3_items:
            if c.id not in mat_ids_to_check:
                continue
            parent = by_id.get(c.parent_id) if c.parent_id else None
            gp = by_id.get(parent.parent_id) if (parent and parent.parent_id) else None
            if not parent or not gp:
                continue
            p_norm = normalize_category_name(parent.name_en)
            gp_norm = normalize_category_name(gp.name_en)
            target_mains = [gp] if gp_norm == p_norm else [r for r in roots_by_norm.get(p_norm, []) if r.id != parent.id]
            for tm in target_mains:
                for m in children_by_parent_norm.get((tm.id, normalize_category_name(c.name_en)), []):
                    potential_targets.add(m.id)
        for cid, name_en, unit in db.execute(
            select(Material.category_id, Material.name_en, Material.unit).where(
                Material.category_id.in_(potential_targets)
            )
        ).all():
            if cid:
                mat_keys_by_cat[cid].add((normalize_category_name(name_en or ''), unit or ''))

    for c in depth3_items:
        parent = by_id.get(c.parent_id) if c.parent_id else None
        gp = by_id.get(parent.parent_id) if (parent and parent.parent_id) else None
        current_path, depth = _build_hierarchy_path(c, by_id)
        aff_mats = total_mat_counts.get(c.id, 0)
        aff_caps = cap_counts_by_cat.get(c.id, 0)

        if depth > 3 or parent is None or gp is None or not gp.active:
            plans[c.id] = HierarchyRepairPlan(
                category_id=c.id,
                code=c.code,
                name_en=c.name_en,
                parent_id=c.parent_id,
                parent_name_en=parent.name_en if parent else None,
                grandparent_id=gp.id if gp else None,
                grandparent_name_en=gp.name_en if gp else None,
                current_path=current_path,
                proposed_path=[],
                repair_action='manual_review',
                affected_materials_count=aff_mats,
                affected_capabilities_count=aff_caps,
                safe=False,
                status='manual_review',
                reason='Hierarchy exceeds 3 levels or has an inactive/missing grandparent; resolve manually.',
            )
            continue

        if active_children_by_parent.get(c.id):
            plans[c.id] = HierarchyRepairPlan(
                category_id=c.id,
                code=c.code,
                name_en=c.name_en,
                parent_id=c.parent_id,
                parent_name_en=parent.name_en,
                grandparent_id=gp.id,
                grandparent_name_en=gp.name_en,
                current_path=current_path,
                proposed_path=[],
                repair_action='manual_review',
                affected_materials_count=aff_mats,
                affected_capabilities_count=aff_caps,
                safe=False,
                status='manual_review',
                reason='Category has active child categories; repair leaf categories first.',
            )
            continue

        gp_norm = normalize_category_name(gp.name_en)
        p_norm = normalize_category_name(parent.name_en)
        cat_norm = normalize_category_name(c.name_en)

        def _evaluate_target_main(target_main: Category, context_note: str) -> HierarchyRepairPlan:
            matches = [
                x for x in children_by_parent_norm.get((target_main.id, cat_norm), []) if x.id != c.id
            ]
            if len(matches) == 0:
                return HierarchyRepairPlan(
                    category_id=c.id,
                    code=c.code,
                    name_en=c.name_en,
                    parent_id=c.parent_id,
                    parent_name_en=parent.name_en,
                    grandparent_id=gp.id,
                    grandparent_name_en=gp.name_en,
                    current_path=current_path,
                    proposed_path=[target_main.name_en, c.name_en],
                    repair_action='reparent_to_main',
                    target_parent_id=target_main.id,
                    affected_materials_count=aff_mats,
                    affected_capabilities_count=aff_caps,
                    safe=True,
                    status='safe',
                    reason=f'Reparent under "{target_main.name_en}" ({context_note}); preserves category ID, materials, and capabilities.',
                )
            if len(matches) > 1:
                return HierarchyRepairPlan(
                    category_id=c.id,
                    code=c.code,
                    name_en=c.name_en,
                    parent_id=c.parent_id,
                    parent_name_en=parent.name_en,
                    grandparent_id=gp.id,
                    grandparent_name_en=gp.name_en,
                    current_path=current_path,
                    proposed_path=[target_main.name_en, c.name_en],
                    repair_action='manual_review',
                    target_parent_id=target_main.id,
                    affected_materials_count=aff_mats,
                    affected_capabilities_count=aff_caps,
                    safe=False,
                    status='manual_review',
                    reason=f'Multiple active "{c.name_en}" subcategories already exist under "{target_main.name_en}"; deduplicate first.',
                )
            target_sub = matches[0]
            blockers: list[str] = []
            if cap_keys_by_cat.get(c.id, set()) & cap_keys_by_cat.get(target_sub.id, set()):
                blockers.append('Supplier capability areas overlap with canonical subcategory.')
            combined_disc = sibling_supported_by_cat.get(c.id, set()) | sibling_supported_by_cat.get(target_sub.id, set())
            if len(combined_disc) > 1:
                blockers.append(f'Active materials span conflicting disciplines ({", ".join(sorted(combined_disc))}).')
            if aff_mats > 0 and (mat_keys_by_cat.get(c.id, set()) & mat_keys_by_cat.get(target_sub.id, set())):
                blockers.append('Material identities overlap with canonical subcategory.')
            if blockers:
                return HierarchyRepairPlan(
                    category_id=c.id,
                    code=c.code,
                    name_en=c.name_en,
                    parent_id=c.parent_id,
                    parent_name_en=parent.name_en,
                    grandparent_id=gp.id,
                    grandparent_name_en=gp.name_en,
                    current_path=current_path,
                    proposed_path=[target_main.name_en, target_sub.name_en],
                    repair_action='manual_review',
                    target_parent_id=target_main.id,
                    target_category_id=target_sub.id,
                    affected_materials_count=aff_mats,
                    affected_capabilities_count=aff_caps,
                    safe=False,
                    status='manual_review',
                    reason=' '.join(blockers),
                )
            return HierarchyRepairPlan(
                category_id=c.id,
                code=c.code,
                name_en=c.name_en,
                parent_id=c.parent_id,
                parent_name_en=parent.name_en,
                grandparent_id=gp.id,
                grandparent_name_en=gp.name_en,
                current_path=current_path,
                proposed_path=[target_main.name_en, target_sub.name_en],
                repair_action='merge_into_canonical_subcategory',
                target_parent_id=target_main.id,
                target_category_id=target_sub.id,
                affected_materials_count=aff_mats,
                affected_capabilities_count=aff_caps,
                safe=True,
                status='safe',
                reason=f'Merge duplicate level-3 record into canonical "{target_main.name_en} → {target_sub.name_en}" ({context_note}).',
            )

        if gp_norm == p_norm:
            # Case 1: Duplicate parent level (e.g. Electrical -> Electrical -> Accessories)
            plans[c.id] = _evaluate_target_main(gp, f'removes duplicate "{parent.name_en}" level')
        elif gp_norm in SUPPORTED_DISCIPLINES:
            # Case 2: Discipline stored as root category (e.g. Civil -> Architectural Insulation -> Acoustic Products)
            existing_roots = [r for r in roots_by_norm.get(p_norm, []) if r.id != parent.id]
            if len(existing_roots) == 1:
                plans[c.id] = _evaluate_target_main(
                    existing_roots[0],
                    f'"{gp.name_en}" is a discipline, not a category level',
                )
            elif len(existing_roots) > 1:
                plans[c.id] = HierarchyRepairPlan(
                    category_id=c.id,
                    code=c.code,
                    name_en=c.name_en,
                    parent_id=c.parent_id,
                    parent_name_en=parent.name_en,
                    grandparent_id=gp.id,
                    grandparent_name_en=gp.name_en,
                    current_path=current_path,
                    proposed_path=[parent.name_en, c.name_en],
                    repair_action='manual_review',
                    affected_materials_count=aff_mats,
                    affected_capabilities_count=aff_caps,
                    safe=False,
                    status='manual_review',
                    reason=f'Multiple root categories named "{parent.name_en}" exist; resolve duplicate main category first.',
                )
            else:
                near_root = _find_near_duplicate_root(p_norm, roots, exclude_id=parent.id)
                if near_root is not None:
                    plans[c.id] = HierarchyRepairPlan(
                        category_id=c.id,
                        code=c.code,
                        name_en=c.name_en,
                        parent_id=c.parent_id,
                        parent_name_en=parent.name_en,
                        grandparent_id=gp.id,
                        grandparent_name_en=gp.name_en,
                        current_path=current_path,
                        proposed_path=[near_root.name_en, c.name_en],
                        repair_action='manual_review',
                        target_parent_id=near_root.id,
                        affected_materials_count=aff_mats,
                        affected_capabilities_count=aff_caps,
                        safe=False,
                        status='manual_review',
                        reason=f'Parent "{parent.name_en}" closely resembles existing main category "{near_root.name_en}"; review spelling/merge manually.',
                    )
                else:
                    plans[c.id] = HierarchyRepairPlan(
                        category_id=c.id,
                        code=c.code,
                        name_en=c.name_en,
                        parent_id=c.parent_id,
                        parent_name_en=parent.name_en,
                        grandparent_id=gp.id,
                        grandparent_name_en=gp.name_en,
                        current_path=current_path,
                        proposed_path=[parent.name_en, c.name_en],
                        repair_action='promote_parent_to_main',
                        target_parent_id=parent.id,
                        affected_materials_count=aff_mats,
                        affected_capabilities_count=aff_caps,
                        safe=True,
                        status='safe',
                        reason=f'Promote "{parent.name_en}" to top-level Main Category (removes "{gp.name_en}" discipline wrapper and preserves all IDs).',
                    )
        else:
            plans[c.id] = HierarchyRepairPlan(
                category_id=c.id,
                code=c.code,
                name_en=c.name_en,
                parent_id=c.parent_id,
                parent_name_en=parent.name_en,
                grandparent_id=gp.id,
                grandparent_name_en=gp.name_en,
                current_path=current_path,
                proposed_path=[],
                repair_action='manual_review',
                affected_materials_count=aff_mats,
                affected_capabilities_count=aff_caps,
                safe=False,
                status='manual_review',
                reason=f'Top-level ancestor "{gp.name_en}" is not a discipline root; review manually.',
            )

    return plans


def analyze_categories(db: Session, *, include_resolved: bool = False) -> list[CategoryAssessment]:
    """Analyze all active categories and return structured assessments using lightweight projections."""
    all_categories = list(db.scalars(select(Category).order_by(Category.name_en, Category.id)).all())
    by_id = {c.id: c for c in all_categories}
    active_categories = [c for c in all_categories if c.active]
    active_ids = {c.id for c in active_categories}

    # SQL-aggregated material summary (returns ~450 grouped rows instead of 10,000+ raw material rows)
    grouped_mat_rows = db.execute(
        select(
            Material.active,
            Material.category_id,
            Material.main_category_id,
            Material.subcategory_id,
            Material.discipline,
            func.count(Material.id),
        ).group_by(
            Material.active,
            Material.category_id,
            Material.main_category_id,
            Material.subcategory_id,
            Material.discipline,
        )
    ).all()

    active_mat_counts: dict[str, int] = defaultdict(int)
    inactive_mat_counts: dict[str, int] = defaultdict(int)
    total_mat_counts: dict[str, int] = defaultdict(int)
    sibling_supported_by_cat: dict[str, set[str]] = defaultdict(set)
    cats_with_mismatch: set[str] = set()
    has_bad_disc = False

    for mat_active, cat_id, main_cat_id, sub_cat_id, disc_raw, row_cnt in grouped_mat_rows:
        cnt = int(row_cnt or 0)
        linked_cats = {cid for cid in (cat_id, main_cat_id, sub_cat_id) if cid and cid in active_ids}
        if not linked_cats:
            continue
        supported_disc = normalize_supported_discipline(disc_raw) if mat_active else None
        for cid in linked_cats:
            total_mat_counts[cid] += cnt
            if mat_active:
                active_mat_counts[cid] += cnt
                if supported_disc:
                    sibling_supported_by_cat[cid].add(supported_disc)
                else:
                    has_bad_disc = True
                is_direct = (sub_cat_id == cid) or (sub_cat_id is None and cat_id == cid)
                if is_direct:
                    cat_obj = by_id[cid]
                    expected_main_id = cat_obj.parent_id or cat_obj.id
                    if main_cat_id is not None and main_cat_id != expected_main_id:
                        cats_with_mismatch.add(cid)
            else:
                inactive_mat_counts[cid] += cnt

    mismatched_ids_by_cat: dict[str, list[str]] = defaultdict(list)
    if cats_with_mismatch:
        for mat_id, cat_id, main_cat_id, sub_cat_id in db.execute(
            select(
                Material.id,
                Material.category_id,
                Material.main_category_id,
                Material.subcategory_id,
            ).where(
                Material.active.is_(True),
                or_(
                    Material.subcategory_id.in_(cats_with_mismatch),
                    and_(Material.subcategory_id.is_(None), Material.category_id.in_(cats_with_mismatch)),
                ),
            )
        ).all():
            direct_cid = sub_cat_id if sub_cat_id in cats_with_mismatch else cat_id
            if direct_cid in cats_with_mismatch:
                cat_obj = by_id[direct_cid]
                expected_main_id = cat_obj.parent_id or cat_obj.id
                if main_cat_id is not None and main_cat_id != expected_main_id:
                    mismatched_ids_by_cat[direct_cid].append(mat_id)

    # Fetch full Material ORM objects ONLY when active materials have missing/unsupported discipline
    bad_materials_by_id: dict[str, Material] = {}
    bad_disc_by_cat: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    batches_by_id: dict[str, ImportBatch] = {}
    rows_by_key: dict[tuple[str, int], ImportedRow] = {}
    if has_bad_disc:
        bad_mats = list(
            db.scalars(
                select(Material).where(
                    Material.active.is_(True),
                    ~func.lower(func.coalesce(Material.discipline, '')).in_(list(SUPPORTED_DISCIPLINES.keys())),
                )
            ).all()
        )
        bad_materials_by_id = {m.id: m for m in bad_mats}
        for m in bad_mats:
            for cid in {m.category_id, m.main_category_id, m.subcategory_id}:
                if cid and cid in active_ids:
                    is_direct = (m.subcategory_id == cid) or (m.subcategory_id is None and m.category_id == cid)
                    bad_disc_by_cat[cid].append((m.id, is_direct))
        needing_meta = [m for m in bad_mats if m.import_batch_id]
        batch_ids = {m.import_batch_id for m in needing_meta if m.import_batch_id}
        if batch_ids:
            batches_by_id = {
                b.id: b for b in db.scalars(select(ImportBatch).where(ImportBatch.id.in_(batch_ids))).all()
            }
            row_nums = {m.import_row_number for m in needing_meta if m.import_row_number is not None}
            if row_nums:
                for row in db.scalars(
                    select(ImportedRow).where(
                        ImportedRow.batch_id.in_(batch_ids),
                        ImportedRow.row_number.in_(row_nums),
                    )
                ).all():
                    rows_by_key[(row.batch_id, row.row_number)] = row

    # Lightweight capability projection for hierarchy repair safety & counts
    cap_keys_by_cat: dict[str, set[tuple[str, str]]] = defaultdict(set)
    cap_counts_by_cat: dict[str, int] = defaultdict(int)
    for cap_cat_id, cap_org_id, cap_city in db.execute(
        select(Capability.category_id, Capability.org_id, Capability.city)
    ).all():
        if cap_cat_id and cap_cat_id in active_ids:
            cap_counts_by_cat[cap_cat_id] += 1
            cap_keys_by_cat[cap_cat_id].add((cap_org_id, cap_city))

    # Detect duplicate active categories under the exact same parent_id + normalized name
    dup_groups_raw: dict[tuple[str | None, str], list[Category]] = defaultdict(list)
    for c in active_categories:
        dup_groups_raw[(c.parent_id, normalize_category_name(c.name_en))].append(c)

    dup_info: dict[str, tuple[Category, bool, list[str], list[str]]] = {}
    for group in dup_groups_raw.values():
        if len(group) > 1:
            canonical, safe, blockers = _duplicate_group_safety(
                db,
                group,
                all_categories,
                active_mat_counts,
                disciplines_by_cat=sibling_supported_by_cat,
            )
            group_ids = [c.id for c in group]
            for c in group:
                dup_info[c.id] = (canonical, safe, blockers, group_ids)

    decisions = _load_current_decisions(db)
    active_children_by_parent: dict[str, list[Category]] = defaultdict(list)
    for c in active_categories:
        if c.parent_id:
            active_children_by_parent[c.parent_id].append(c)

    hierarchy_plans = _plan_hierarchy_repairs(
        db,
        by_id=by_id,
        active_categories=active_categories,
        active_children_by_parent=active_children_by_parent,
        total_mat_counts=total_mat_counts,
        sibling_supported_by_cat=sibling_supported_by_cat,
        cap_keys_by_cat=cap_keys_by_cat,
        cap_counts_by_cat=cap_counts_by_cat,
    )

    assessments: list[CategoryAssessment] = []

    for category in active_categories:
        parent = by_id.get(category.parent_id) if category.parent_id else None
        active_count = active_mat_counts.get(category.id, 0)
        inactive_count = inactive_mat_counts.get(category.id, 0)
        current_decision = decisions.get(category.id)
        mismatched_mat_ids = mismatched_ids_by_cat.get(category.id, [])
        sibling_supported = sibling_supported_by_cat.get(category.id, set())

        mat_assessments: list[MaterialAssessment] = []
        safe_material_fixes: dict[str, str] = {}
        missing_count = 0
        unsupported_count = 0
        ambiguous_count = 0

        for mat_id, is_direct in bad_disc_by_cat.get(category.id, []):
            m = bad_materials_by_id.get(mat_id)
            if m is None:
                continue
            leaf_cat = by_id.get(m.subcategory_id or m.category_id) or category
            leaf_parent = by_id.get(leaf_cat.parent_id) if leaf_cat.parent_id else None
            ma = _assess_material_discipline(
                m, leaf_cat, leaf_parent, sibling_supported, batches_by_id, rows_by_key
            )
            mat_assessments.append(ma)
            if ma.issue == 'ok':
                continue
            if not (m.discipline or '').strip():
                missing_count += 1
            else:
                unsupported_count += 1
            if ma.safe and ma.inferred_discipline and is_direct:
                safe_material_fixes[m.id] = ma.inferred_discipline
            else:
                ambiguous_count += 1

        # Determine issues
        secondary: list[str] = []
        evidence: list[str] = []
        hierarchy_path, hierarchy_depth = _build_hierarchy_path(category, by_id)
        h_plan = hierarchy_plans.get(category.id)
        proposed_path: list[str] = list(h_plan.proposed_path) if h_plan else []
        hierarchy_repair_action: str | None = h_plan.repair_action if h_plan else None
        hierarchy_repair_safe: bool = bool(h_plan and h_plan.safe)
        affected_capabilities_count = cap_counts_by_cat.get(category.id, 0)

        label_text = f'{category.name_en} {parent.name_en if parent else ""}'
        is_suspicious_label = bool(SUSPICIOUS_LABEL_RE.search(label_text))
        is_missing_parent = bool(category.parent_id and parent is None)
        is_archived_parent = bool(category.parent_id and parent is not None and not parent.active)
        is_hierarchy_depth_exceeded = bool(
            category.parent_id and parent is not None and parent.active and parent.parent_id is not None
        )
        is_broken_parent = is_missing_parent or is_archived_parent or is_hierarchy_depth_exceeded
        is_duplicate = category.id in dup_info
        has_active_children = bool(active_children_by_parent.get(category.id))
        can_archive = (active_count == 0) and (not has_active_children)

        # Determine primary issue_type & action
        issue_type: str | None = None
        reason = ''
        proposed_action = 'manual_review'
        proposed_discipline: str | None = None
        safe_to_auto_fix = False
        can_approve = False
        can_retain_empty = False
        can_merge = False
        duplicate_ids: list[str] = []
        duplicate_canonical_id: str | None = None
        duplicate_safe = False
        duplicate_blockers: list[str] = []
        parent_fix_ids: list[str] = []

        if is_duplicate:
            canonical, d_safe, blockers, group_ids = dup_info[category.id]
            duplicate_ids = group_ids
            duplicate_canonical_id = canonical.id
            duplicate_safe = d_safe
            duplicate_blockers = blockers
            can_merge = d_safe

        if is_missing_parent:
            issue_type = 'missing_parent'
            reason = f'Category references missing parent ID ({category.parent_id}).'
            if is_duplicate:
                secondary.append('duplicate_category')
            if mismatched_mat_ids:
                secondary.append('parent_mismatch')
            if missing_count:
                secondary.append('missing_discipline')
            if unsupported_count:
                secondary.append('unsupported_discipline')
            proposed_action = 'manual_review'
            evidence.append(reason)

        elif is_archived_parent:
            issue_type = 'archived_parent'
            reason = (
                f'Parent category "{parent.name_en}" ({parent.code}) is archived while this subcategory remains active.'
                if parent
                else 'Parent category is archived while this subcategory remains active.'
            )
            if is_duplicate:
                secondary.append('duplicate_category')
            if mismatched_mat_ids:
                secondary.append('parent_mismatch')
            if missing_count:
                secondary.append('missing_discipline')
            if unsupported_count:
                secondary.append('unsupported_discipline')
            proposed_action = 'manual_review'
            evidence.append(reason)

        elif is_hierarchy_depth_exceeded:
            issue_type = 'hierarchy_depth_exceeded'
            path_str = ' → '.join(hierarchy_path)
            if h_plan:
                reason = h_plan.reason
                proposed_action = h_plan.repair_action
            else:
                reason = (
                    f'Category sits at hierarchy level {hierarchy_depth} ({path_str}), '
                    'exceeding the two-level hierarchy (Main Category → Subcategory).'
                )
                proposed_action = 'manual_review'
            if is_duplicate:
                secondary.append('duplicate_category')
            if mismatched_mat_ids:
                secondary.append('parent_mismatch')
            if missing_count:
                secondary.append('missing_discipline')
            if unsupported_count:
                secondary.append('unsupported_discipline')
            evidence.append(f'Current path: {path_str}')
            if proposed_path:
                evidence.append(f'Proposed path: {" → ".join(proposed_path)}')

        elif is_suspicious_label:
            issue_type = 'invalid_import_data'
            reason = 'Category label resembles test, QA, sample, or bulk data; review before public use.'
            if is_duplicate:
                secondary.append('duplicate_category')
            if mismatched_mat_ids:
                secondary.append('parent_mismatch')
            # Admin can explicitly approve a false-positive suspicious label ONLY if no broken parent or bad materials
            can_approve = (
                not mismatched_mat_ids
                and missing_count == 0
                and unsupported_count == 0
                and not is_duplicate
                and active_count > 0
            )
            proposed_action = 'manual_review'
            evidence.append(reason)

        elif is_duplicate:
            issue_type = 'duplicate_category'
            if duplicate_safe:
                is_canonical = category.id == duplicate_canonical_id
                reason = (
                    f'Duplicate active category ({len(duplicate_ids)} records under same parent). '
                    + ('Selected as canonical target; safe to merge aliases into this category.'
                       if is_canonical
                       else 'Safe to merge into canonical category and archive alias.')
                )
                proposed_action = 'merge_duplicate'
                safe_to_auto_fix = True
                evidence.append(f'Canonical category ID: {duplicate_canonical_id}')
            else:
                reason = 'Duplicate active category with merge conflicts: ' + ' '.join(duplicate_blockers)
                proposed_action = 'manual_review'
                evidence.extend(duplicate_blockers)
            if missing_count or unsupported_count:
                secondary.append('missing_discipline' if missing_count else 'unsupported_discipline')

        elif mismatched_mat_ids:
            issue_type = 'parent_mismatch'
            reason = f'{len(mismatched_mat_ids)} active material(s) reference a main category that disagrees with this subcategory parent.'
            if parent and parent.active and not parent.parent_id:
                parent_fix_ids = list(mismatched_mat_ids)
                proposed_action = 'repair_parent'
                safe_to_auto_fix = True
                evidence.append(f'Align main_category_id to parent "{parent.name_en}" ({parent.id})')
            else:
                proposed_action = 'manual_review'
                evidence.append('Parent hierarchy cannot be repaired automatically.')
            if missing_count or unsupported_count:
                secondary.append('missing_discipline' if missing_count else 'unsupported_discipline')

        elif unsupported_count > 0 or missing_count > 0:
            if ambiguous_count > 0 and not safe_material_fixes:
                issue_type = 'ambiguous_classification' if unsupported_count == 0 else 'unsupported_discipline'
                reason = (
                    f'{ambiguous_count} active material(s) have ambiguous or conflicting discipline evidence and require manual review.'
                    if issue_type == 'ambiguous_classification'
                    else f'{unsupported_count} active material(s) have an unsupported discipline value requiring manual review.'
                )
                proposed_action = 'manual_review'
                evidence.extend(
                    sorted({ma.evidence for ma in mat_assessments if not ma.safe})[:5]
                )
            elif ambiguous_count > 0 and safe_material_fixes:
                issue_type = 'ambiguous_classification'
                inferred_set = sorted(set(safe_material_fixes.values()))
                reason = (
                    f'{len(safe_material_fixes)} material(s) can be assigned safely ({", ".join(inferred_set)}), '
                    f'but {ambiguous_count} material(s) remain ambiguous for manual review.'
                )
                proposed_action = 'assign_discipline'
                proposed_discipline = inferred_set[0] if len(inferred_set) == 1 else None
                safe_to_auto_fix = True  # fixes only the safe subset; ambiguous materials remain untouched
                evidence.extend(sorted({ma.evidence for ma in mat_assessments if ma.issue != 'ok'})[:5])
            else:
                issue_type = 'unsupported_discipline' if unsupported_count > 0 else 'missing_discipline'
                inferred_set = sorted(set(safe_material_fixes.values()))
                proposed_discipline = inferred_set[0] if len(inferred_set) == 1 else None
                if issue_type == 'unsupported_discipline':
                    reason = f'{unsupported_count} active material(s) use a non-standard discipline value that maps safely to {", ".join(inferred_set)}.'
                else:
                    reason = f'{missing_count} active material(s) are missing a discipline assignment; safe assignment identified: {", ".join(inferred_set)}.'
                proposed_action = 'assign_discipline'
                safe_to_auto_fix = True
                evidence.extend(sorted({ma.evidence for ma in mat_assessments if ma.issue != 'ok'})[:5])

        elif active_count == 0:
            if inactive_count > 0:
                issue_type = 'inactive_materials_only'
                reason = f'Category has {inactive_count} archived material(s) and no active materials.'
                proposed_action = 'retain_empty'
                can_retain_empty = True
                evidence.append('Retain as valid empty category (hidden from buyer selection) or archive.')
            else:
                issue_type = 'empty_category'
                reason = 'Valid category with no active materials yet. Retain as empty or add materials.'
                proposed_action = 'retain_empty'
                can_retain_empty = True
                evidence.append('Retaining keeps this category out of buyer selection until active materials exist.')

        else:
            # Category has active materials, all with valid supported disciplines, no duplicates, no parent mismatch, no suspicious label!
            # It is completely healthy and does NOT need review.
            if not include_resolved:
                continue
            issue_type = 'valid'
            reason = 'Category and linked active materials are valid.'
            proposed_action = 'approve'
            can_approve = True

        # Check whether an explicit prior admin decision already resolved this exact issue
        resolved_by_decision = False
        if current_decision:
            if current_decision.decision == 'retain_empty' and issue_type in {'empty_category', 'inactive_materials_only'}:
                resolved_by_decision = True
            elif current_decision.decision == 'approve' and issue_type == 'invalid_import_data' and is_suspicious_label and not is_broken_parent and not mismatched_mat_ids and missing_count == 0 and unsupported_count == 0 and not is_duplicate:
                resolved_by_decision = True

        if resolved_by_decision and not include_resolved:
            continue

        requires_manual = (
            (not safe_to_auto_fix)
            and (not hierarchy_repair_safe)
            and issue_type not in {'empty_category', 'inactive_materials_only', 'valid'}
        )

        assessments.append(
            CategoryAssessment(
                category=category,
                parent=parent,
                issue_type=issue_type,
                reason=reason,
                category_id=category.id,
                category_code=category.code,
                category_name_en=category.name_en,
                category_name_ar=category.name_ar or '',
                parent_id=category.parent_id,
                parent_name_en=parent.name_en if parent else None,
                hierarchy_path=hierarchy_path,
                proposed_path=proposed_path,
                hierarchy_repair_action=hierarchy_repair_action,
                hierarchy_repair_safe=hierarchy_repair_safe,
                affected_capabilities_count=affected_capabilities_count,
                secondary_issues=secondary,
                active_material_count=active_count,
                inactive_material_count=inactive_count,
                missing_discipline_count=missing_count,
                unsupported_discipline_count=unsupported_count,
                duplicate_ids=duplicate_ids,
                duplicate_canonical_id=duplicate_canonical_id,
                duplicate_safe=duplicate_safe,
                duplicate_blockers=duplicate_blockers,
                proposed_action=proposed_action,
                proposed_discipline=proposed_discipline,
                safe_to_auto_fix=safe_to_auto_fix,
                can_approve=can_approve,
                can_retain_empty=can_retain_empty,
                can_merge=can_merge,
                can_archive=can_archive,
                requires_manual_review=requires_manual,
                evidence=evidence,
                current_decision=current_decision,
                current_decision_value=current_decision.decision if current_decision else None,
                material_assessments=[ma for ma in mat_assessments if ma.issue != 'ok'][:10],
                safe_material_fixes=safe_material_fixes,
                parent_fix_material_ids=parent_fix_ids,
            )
        )

    return assessments


def record_review_decision(
    db: Session,
    *,
    category_id: str,
    decision: str,
    issue_type: str,
    actor: User | None,
    discipline: str | None = None,
    merged_into_category_id: str | None = None,
    notes: str | None = None,
    details: dict | None = None,
) -> CategoryReviewDecision:
    """Persist an explicit admin/auto-fix review decision and retire prior current flags."""
    db.execute(
        update(CategoryReviewDecision)
        .where(CategoryReviewDecision.category_id == category_id, CategoryReviewDecision.is_current.is_(True))
        .values(is_current=False, updated_at=utcnow())
    )
    entry = CategoryReviewDecision(
        category_id=category_id,
        decision=decision,
        issue_type=issue_type,
        discipline=discipline,
        merged_into_category_id=merged_into_category_id,
        actor_user_id=actor.id if actor else None,
        notes=notes,
        details=details or {},
        is_current=True,
    )
    db.add(entry)
    return entry


def _recompute_material_identity(db: Session, material: Material) -> str | None:
    main = db.get(Category, material.main_category_id or material.category_id)
    sub = db.get(Category, material.subcategory_id) if material.subcategory_id else None
    if not main:
        return material.source_identity
    return material_identity(
        main_category=main.normalized_name or main.name_en,
        subcategory=(sub.normalized_name or sub.name_en) if sub else '',
        description=material.name_en,
        unit=material.unit,
        specifications=material.description,
        discipline=material.discipline,
    )


def _apply_material_discipline_updates(
    db: Session,
    material_fixes: dict[str, str],
    actor: User | None,
    action_label: str = 'review_discipline_assigned',
) -> int:
    """Update Material.discipline in place, preserving IDs and appending revisions."""
    if not material_fixes:
        return 0
    ordered_ids = sorted(material_fixes.keys())
    materials = list(
        db.scalars(
            select(Material).where(Material.id.in_(ordered_ids)).order_by(Material.id).with_for_update()
        ).all()
    )
    updated = 0
    for mat in materials:
        target_disc = normalize_supported_discipline(material_fixes.get(mat.id))
        if not target_disc or mat.discipline == target_disc:
            continue
        mat.discipline = target_disc
        new_identity = _recompute_material_identity(db, mat)
        if new_identity and new_identity != mat.source_identity:
            conflict = db.scalar(
                select(Material.id).where(Material.source_identity == new_identity, Material.id != mat.id)
            )
            if not conflict:
                mat.source_identity = new_identity
        mat.version += 1
        db.add(
            MaterialRevision(
                material_id=mat.id,
                version=mat.version,
                actor_user_id=actor.id if actor else None,
                action=action_label[:30],
                snapshot=material_snapshot(mat),
            )
        )
        updated += 1
    return updated


def _apply_parent_link_repairs(
    db: Session,
    category_id: str,
    material_ids: list[str],
    actor: User | None,
) -> int:
    """Repair mismatched main_category_id on active materials in place without changing IDs."""
    if not material_ids:
        return 0
    cat = db.scalar(select(Category).where(Category.id == category_id).with_for_update())
    if not cat or not cat.active:
        return 0
    expected_main_id = cat.parent_id or cat.id
    ordered_ids = sorted(set(material_ids))
    materials = list(
        db.scalars(
            select(Material).where(Material.id.in_(ordered_ids)).order_by(Material.id).with_for_update()
        ).all()
    )
    repaired = 0
    for mat in materials:
        if mat.main_category_id == expected_main_id:
            continue
        mat.main_category_id = expected_main_id
        if cat.parent_id:
            mat.subcategory_id = cat.id
            mat.category_id = cat.id
        new_identity = _recompute_material_identity(db, mat)
        if new_identity and new_identity != mat.source_identity:
            conflict = db.scalar(
                select(Material.id).where(Material.source_identity == new_identity, Material.id != mat.id)
            )
            if not conflict:
                mat.source_identity = new_identity
        mat.version += 1
        db.add(
            MaterialRevision(
                material_id=mat.id,
                version=mat.version,
                actor_user_id=actor.id if actor else None,
                action='review_parent_repaired',
                snapshot=material_snapshot(mat),
            )
        )
        repaired += 1
    return repaired


def _merge_duplicate_group_in_session(
    db: Session,
    group_ids: list[str],
    actor: User | None,
) -> tuple[str | None, list[str], int]:
    """Safely merge a duplicate category group in a short transaction, preserving all references."""
    ordered_ids = sorted(set(group_ids))
    if len(ordered_ids) < 2:
        return None, [], 0
    locked = list(
        db.scalars(
            select(Category).where(Category.id.in_(ordered_ids)).order_by(Category.id).with_for_update()
        ).all()
    )
    active_group = [c for c in locked if c.active]
    if len(active_group) < 2:
        return None, [], 0
    # Re-verify identical parent and normalized name
    if len({(c.parent_id, normalize_category_name(c.name_en)) for c in active_group}) != 1:
        return None, [], 0

    all_categories = list(db.scalars(select(Category)).all())
    active_mat_counts: dict[str, int] = defaultdict(int)
    materials_by_cat: dict[str, list[Material]] = defaultdict(list)
    for m in db.scalars(
        select(Material).where(
            or_(
                Material.category_id.in_(ordered_ids),
                Material.main_category_id.in_(ordered_ids),
                Material.subcategory_id.in_(ordered_ids),
            )
        )
    ).all():
        for cid in {m.category_id, m.main_category_id, m.subcategory_id}:
            if cid in ordered_ids:
                materials_by_cat[cid].append(m)
                if m.active:
                    active_mat_counts[cid] += 1

    canonical, safe, _ = _duplicate_group_safety(
        db, active_group, all_categories, active_mat_counts, materials_by_cat
    )
    if not safe:
        return None, [], 0

    sources = [c for c in active_group if c.id != canonical.id]
    source_ids = [c.id for c in sources]
    for src in sources:
        src.active = False
    db.flush()

    moved = 0
    for tbl, col in category_references():
        if tbl.name == 'category_review_decisions':
            continue
        res = db.execute(update(tbl).where(col.in_(source_ids)).values({col.name: canonical.id}))
        moved += res.rowcount or 0

    if not canonical.name_ar:
        canonical.name_ar = next((c.name_ar for c in sources if c.name_ar), '')
    canonical.normalized_name = normalize_category_name(canonical.name_en)

    for src in sources:
        record_review_decision(
            db,
            category_id=src.id,
            decision='merge_duplicate',
            issue_type='duplicate_category',
            actor=actor,
            merged_into_category_id=canonical.id,
            notes=f'Merged duplicate alias into canonical category {canonical.name_en} ({canonical.code}).',
            details={'canonical_id': canonical.id, 'references_moved': moved},
        )
    audit(
        db,
        actor,
        'category.duplicates_merged',
        entity_id=canonical.id,
        archived_ids=source_ids,
        references_moved=moved,
    )
    return canonical.id, source_ids, moved


def _preview_item_from_assessment(item: CategoryAssessment, *, proposed_action_override: str | None = None) -> dict:
    """Build a plain scalar preview DTO while the session is active."""
    return {
        'category_id': item.category_id,
        'code': item.category_code,
        'name_en': item.category_name_en,
        'parent_name_en': item.parent_name_en,
        'issue_type': item.issue_type,
        'proposed_action': proposed_action_override or item.proposed_action,
        'proposed_discipline': None if proposed_action_override else item.proposed_discipline,
        'materials_to_update': 0
        if proposed_action_override
        else len(item.safe_material_fixes) + len(item.parent_fix_material_ids),
        'duplicate_count': 0 if proposed_action_override else len(item.duplicate_ids),
        'reason': item.reason,
    }


def run_bulk_auto_fix(
    *,
    actor: User | ReviewActor | None,
    dry_run: bool = False,
    category_ids: list[str] | None = None,
    cursor_after_id: str | None = None,
    job_limit: int = JOB_SIZE,
    batch_size: int = BATCH_SIZE,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    """Execute safe category review fixes in short committed batches of 30, up to 300 per job.

    Opens and closes a fresh session per batch so no PostgreSQL transaction is ever held
    idle between batches and the Material Master is never globally locked.
    All DTOs and scalar work items are materialized while the read session is active.
    """
    job_limit = max(1, min(job_limit, JOB_SIZE))
    batch_size = max(1, min(batch_size, BATCH_SIZE))
    requested_set = set(category_ids) if category_ids else None

    # Step 1: Read-only analysis snapshot in a short session — extract pure scalar DTOs before closing
    safe_candidates: list[dict] = []
    skipped_ambiguous = 0
    seen_dup_groups: set[tuple[str, ...]] = set()

    with session_factory() as read_db:
        all_assessments = analyze_categories(read_db, include_resolved=False)
        for item in sorted(all_assessments, key=lambda a: a.category_id):
            if requested_set is not None and item.category_id not in requested_set:
                continue
            if not item.safe_to_auto_fix:
                if item.requires_manual_review:
                    skipped_ambiguous += 1
                continue
            if item.proposed_action == 'merge_duplicate':
                key = tuple(sorted(item.duplicate_ids))
                if key in seen_dup_groups:
                    continue
                seen_dup_groups.add(key)
            safe_candidates.append(
                {
                    'category_id': item.category_id,
                    'issue_type': item.issue_type,
                    'proposed_action': item.proposed_action,
                    'proposed_discipline': item.proposed_discipline,
                    'duplicate_ids': list(item.duplicate_ids),
                    'safe_material_fixes': dict(item.safe_material_fixes),
                    'parent_fix_material_ids': list(item.parent_fix_material_ids),
                    'preview': _preview_item_from_assessment(item),
                }
            )

    if cursor_after_id:
        eligible = [item for item in safe_candidates if item['category_id'] > cursor_after_id]
    else:
        eligible = safe_candidates

    job_slice = eligible[:job_limit]
    remaining_after_job = max(0, len(eligible) - len(job_slice))
    next_cursor = job_slice[-1]['category_id'] if (remaining_after_job > 0 and job_slice) else None
    preview_items = [item['preview'] for item in job_slice]

    if dry_run or not job_slice:
        return {
            'dry_run': dry_run,
            'job_size': job_limit,
            'batch_size': batch_size,
            'batches_executed': 0,
            'categories_processed': len(job_slice) if dry_run else 0,
            'categories_resolved': 0,
            'materials_updated': sum(len(i['safe_material_fixes']) for i in job_slice) if dry_run else 0,
            'duplicates_merged': sum(
                max(0, len(i['duplicate_ids']) - 1) for i in job_slice if i['proposed_action'] == 'merge_duplicate'
            )
            if dry_run
            else 0,
            'parent_links_repaired': sum(len(i['parent_fix_material_ids']) for i in job_slice) if dry_run else 0,
            'skipped_ambiguous': skipped_ambiguous,
            'remaining_safe': remaining_after_job if dry_run else 0,
            'has_more': remaining_after_job > 0,
            'next_cursor': next_cursor,
            'preview_items': preview_items[:100],
            'errors': [],
        }

    batches_executed = 0
    categories_processed = 0
    categories_resolved = 0
    materials_updated = 0
    duplicates_merged = 0
    parent_links_repaired = 0
    errors: list[dict] = []

    for start in range(0, len(job_slice), batch_size):
        chunk = job_slice[start : start + batch_size]
        batches_executed += 1
        with session_factory() as batch_db:
            try:
                for item in chunk:
                    categories_processed += 1
                    if item['proposed_action'] == 'merge_duplicate' and item['duplicate_ids']:
                        canonical_id, archived_ids, _ = _merge_duplicate_group_in_session(
                            batch_db, item['duplicate_ids'], actor
                        )
                        if canonical_id and archived_ids:
                            duplicates_merged += len(archived_ids)
                            categories_resolved += len(archived_ids)
                    elif item['proposed_action'] == 'repair_parent' and item['parent_fix_material_ids']:
                        repaired = _apply_parent_link_repairs(
                            batch_db, item['category_id'], item['parent_fix_material_ids'], actor
                        )
                        parent_links_repaired += repaired
                        if repaired:
                            record_review_decision(
                                batch_db,
                                category_id=item['category_id'],
                                decision='reclassify',
                                issue_type=item['issue_type'],
                                actor=actor,
                                notes=f'Auto-repaired main_category_id for {repaired} material(s).',
                                details={'materials_repaired': repaired, 'auto_fix': True},
                            )
                            categories_resolved += 1
                    elif item['proposed_action'] == 'assign_discipline' and item['safe_material_fixes']:
                        updated = _apply_material_discipline_updates(
                            batch_db, item['safe_material_fixes'], actor, action_label='auto_fix_discipline'
                        )
                        materials_updated += updated
                        if updated:
                            fully_resolved = item['issue_type'] != 'ambiguous_classification'
                            if fully_resolved:
                                record_review_decision(
                                    batch_db,
                                    category_id=item['category_id'],
                                    decision='reclassify',
                                    issue_type=item['issue_type'],
                                    actor=actor,
                                    discipline=item['proposed_discipline'],
                                    notes=f'Auto-assigned supported discipline to {updated} material(s).',
                                    details={'materials_updated': updated, 'auto_fix': True},
                                )
                                categories_resolved += 1
                batch_db.commit()
            except Exception as exc:
                batch_db.rollback()
                logger.exception('Bulk category review auto-fix batch failed')
                errors.append({'batch': batches_executed, 'error': str(exc)})

    if materials_updated or duplicates_merged or parent_links_repaired:
        catalog_cache.invalidate()

    # Re-check remaining safe count after applying this job
    with session_factory() as verify_db:
        post_assessments = analyze_categories(verify_db, include_resolved=False)
        remaining_safe = sum(1 for a in post_assessments if a.safe_to_auto_fix)

    return {
        'dry_run': False,
        'job_size': job_limit,
        'batch_size': batch_size,
        'batches_executed': batches_executed,
        'categories_processed': categories_processed,
        'categories_resolved': categories_resolved,
        'materials_updated': materials_updated,
        'duplicates_merged': duplicates_merged,
        'parent_links_repaired': parent_links_repaired,
        'skipped_ambiguous': skipped_ambiguous,
        'remaining_safe': remaining_safe,
        'has_more': remaining_after_job > 0 and remaining_safe > 0,
        'next_cursor': next_cursor if remaining_safe > 0 else None,
        'preview_items': preview_items[:100],
        'errors': errors,
    }


def run_bulk_approve_valid(
    *,
    actor: User | ReviewActor | None,
    dry_run: bool = False,
    include_empty: bool = True,
    category_ids: list[str] | None = None,
    cursor_after_id: str | None = None,
    job_limit: int = JOB_SIZE,
    batch_size: int = BATCH_SIZE,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    """Explicitly retain valid empty categories in idempotent, short committed batches of 30.

    All response DTOs and candidate identifiers are extracted while the read session is active
    so no SQLAlchemy ORM instances are ever accessed after session close.
    """
    job_limit = max(1, min(job_limit, JOB_SIZE))
    batch_size = max(1, min(batch_size, BATCH_SIZE))
    requested_set = set(category_ids) if category_ids else None

    valid_candidates: list[dict] = []
    excluded_by_issue: Counter[str] = Counter()
    excluded_preview_items: list[dict] = []
    excluded_count = 0

    with session_factory() as read_db:
        all_assessments = analyze_categories(read_db, include_resolved=False)
        for item in sorted(all_assessments, key=lambda a: a.category_id):
            if requested_set is not None and item.category_id not in requested_set:
                continue
            if include_empty and item.can_retain_empty and item.issue_type in {'empty_category', 'inactive_materials_only'}:
                valid_candidates.append(
                    {
                        'category_id': item.category_id,
                        'issue_type': item.issue_type,
                        'preview': _preview_item_from_assessment(item, proposed_action_override='retain_empty'),
                    }
                )
            else:
                excluded_count += 1
                excluded_by_issue[item.issue_type] += 1
                if len(excluded_preview_items) < 25:
                    excluded_preview_items.append(_preview_item_from_assessment(item))

    if cursor_after_id:
        eligible = [item for item in valid_candidates if item['category_id'] > cursor_after_id]
    else:
        eligible = valid_candidates

    total_eligible = len(eligible)
    job_slice = eligible[:job_limit]
    remaining_after_job = max(0, total_eligible - len(job_slice))
    next_cursor = job_slice[-1]['category_id'] if (remaining_after_job > 0 and job_slice) else None
    preview_items = [item['preview'] for item in job_slice]

    if dry_run or not job_slice:
        return {
            'dry_run': dry_run,
            'job_size': job_limit,
            'batch_size': batch_size,
            'batches_executed': 0,
            'total_eligible': total_eligible,
            'retained_empty': len(job_slice) if dry_run else 0,
            'approved': 0,
            'approved_count': 0,
            'skipped_already_approved': 0,
            'remaining_valid': remaining_after_job if dry_run else 0,
            'excluded_count': excluded_count,
            'excluded_by_issue_type': dict(excluded_by_issue),
            'has_more': remaining_after_job > 0,
            'next_cursor': next_cursor,
            'preview_items': preview_items[:100],
            'excluded_preview_items': excluded_preview_items,
            'errors': [],
        }

    batches_executed = 0
    retained_empty = 0
    skipped_already_approved = 0
    errors: list[dict] = []

    for start in range(0, len(job_slice), batch_size):
        chunk = job_slice[start : start + batch_size]
        chunk_ids = [item['category_id'] for item in chunk]
        batches_executed += 1
        with session_factory() as batch_db:
            try:
                # Lock candidate categories in deterministic order to prevent concurrent double-approval
                locked_cats = {
                    c.id: c
                    for c in batch_db.scalars(
                        select(Category).where(Category.id.in_(chunk_ids)).order_by(Category.id).with_for_update()
                    ).all()
                }
                already_retained_ids = set(
                    batch_db.scalars(
                        select(CategoryReviewDecision.category_id).where(
                            CategoryReviewDecision.category_id.in_(chunk_ids),
                            CategoryReviewDecision.is_current.is_(True),
                            CategoryReviewDecision.decision == 'retain_empty',
                        )
                    ).all()
                )
                cats_with_active_mats = set(
                    batch_db.scalars(
                        select(Material.category_id).where(
                            Material.active.is_(True),
                            Material.category_id.in_(chunk_ids),
                        )
                    ).all()
                ) | set(
                    batch_db.scalars(
                        select(Material.main_category_id).where(
                            Material.active.is_(True),
                            Material.main_category_id.in_(chunk_ids),
                        )
                    ).all()
                ) | set(
                    batch_db.scalars(
                        select(Material.subcategory_id).where(
                            Material.active.is_(True),
                            Material.subcategory_id.in_(chunk_ids),
                        )
                    ).all()
                )

                batch_retained_ids: list[str] = []
                for item in chunk:
                    cid = item['category_id']
                    cat = locked_cats.get(cid)
                    if not cat or not cat.active:
                        continue
                    if cid in already_retained_ids:
                        skipped_already_approved += 1
                        continue
                    if cid in cats_with_active_mats:
                        continue
                    record_review_decision(
                        batch_db,
                        category_id=cid,
                        decision='retain_empty',
                        issue_type=item['issue_type'],
                        actor=actor,
                        notes='Explicitly retained as a valid empty category; hidden from buyer selection until active materials exist.',
                        details={'bulk_approve_valid': True},
                    )
                    batch_retained_ids.append(cid)

                if batch_retained_ids:
                    audit(
                        batch_db,
                        actor,
                        'category.bulk_retained_empty',
                        count=len(batch_retained_ids),
                        category_ids=batch_retained_ids,
                    )
                batch_db.commit()
                retained_empty += len(batch_retained_ids)
            except Exception as exc:
                batch_db.rollback()
                logger.exception('Bulk approve-valid batch failed')
                errors.append({'batch': batches_executed, 'error': str(exc)})

    if retained_empty:
        catalog_cache.invalidate()

    # Re-verify live remaining valid count after commit
    with session_factory() as verify_db:
        post_assessments = analyze_categories(verify_db, include_resolved=False)
        remaining_valid = sum(
            1
            for a in post_assessments
            if include_empty and a.can_retain_empty and a.issue_type in {'empty_category', 'inactive_materials_only'}
        )

    return {
        'dry_run': False,
        'job_size': job_limit,
        'batch_size': batch_size,
        'batches_executed': batches_executed,
        'total_eligible': total_eligible,
        'retained_empty': retained_empty,
        'approved': 0,
        'approved_count': retained_empty,
        'skipped_already_approved': skipped_already_approved,
        'remaining_valid': remaining_valid,
        'excluded_count': excluded_count,
        'excluded_by_issue_type': dict(excluded_by_issue),
        'has_more': remaining_after_job > 0 and remaining_valid > 0,
        'next_cursor': next_cursor if remaining_valid > 0 else None,
        'preview_items': preview_items[:100],
        'excluded_preview_items': excluded_preview_items,
        'errors': errors,
    }


def apply_single_category_decision(
    db: Session,
    *,
    category_id: str,
    decision: str,
    actor: User,
    discipline: str | None = None,
    target_parent_id: str | None = None,
    material_disciplines: dict[str, str] | None = None,
    notes: str | None = None,
) -> dict:
    """Apply an explicit admin decision for one category with strict validation."""
    category = db.scalar(select(Category).where(Category.id == category_id).with_for_update())
    if not category:
        raise HTTPException(404, 'Category not found')

    assessments = {a.category.id: a for a in analyze_categories(db, include_resolved=True)}
    assessment = assessments.get(category_id)
    if not assessment:
        raise HTTPException(404, 'Active category not found')

    materials_updated = 0
    archived_ids: list[str] = []
    merged_into: str | None = None

    if decision == 'approve':
        if assessment.missing_discipline_count > 0 or assessment.unsupported_discipline_count > 0:
            raise HTTPException(
                422,
                'Cannot approve category while active materials have missing or unsupported disciplines. Reclassify materials first.',
            )
        if assessment.issue_type in {'duplicate_category', 'parent_mismatch'} | HIERARCHY_BLOCKING_ISSUES:
            raise HTTPException(422, f'Resolve {assessment.issue_type.replace("_", " ")} before approving.')
        if assessment.active_material_count == 0:
            raise HTTPException(422, 'Empty categories cannot be approved as populated; use Retain Empty Category instead.')
        record_review_decision(
            db,
            category_id=category.id,
            decision='approve',
            issue_type=assessment.issue_type,
            actor=actor,
            notes=notes or 'Explicitly approved by administrator.',
        )
        audit(db, actor, 'category.review_approved', entity_id=category.id, issue_type=assessment.issue_type)

    elif decision == 'retain_empty':
        if assessment.active_material_count > 0:
            raise HTTPException(422, 'Only categories with zero active materials can be retained as empty.')
        if assessment.issue_type in {'duplicate_category'} | HIERARCHY_BLOCKING_ISSUES:
            raise HTTPException(422, f'Resolve {assessment.issue_type.replace("_", " ")} before retaining as empty.')
        record_review_decision(
            db,
            category_id=category.id,
            decision='retain_empty',
            issue_type=assessment.issue_type,
            actor=actor,
            notes=notes or 'Retained as valid empty category; hidden from buyer selection until active materials exist.',
        )
        audit(db, actor, 'category.review_retained_empty', entity_id=category.id)

    elif decision == 'reclassify':
        fixes: dict[str, str] = {}
        if material_disciplines:
            for mid, disc in material_disciplines.items():
                norm = normalize_supported_discipline(disc)
                if not norm:
                    raise HTTPException(422, f'Unsupported discipline: {disc}. Use Civil, Electrical, or Mechanical.')
                fixes[mid] = norm
        elif discipline:
            norm = normalize_supported_discipline(discipline)
            if not norm:
                raise HTTPException(422, 'Discipline must be Civil, Electrical, or Mechanical.')
            # Assign to all active materials in this category that are missing or unsupported
            linked = db.scalars(
                select(Material).where(
                    or_(
                        Material.category_id == category.id,
                        Material.main_category_id == category.id,
                        Material.subcategory_id == category.id,
                    ),
                    Material.active.is_(True),
                )
            ).all()
            for mat in linked:
                if not normalize_supported_discipline(mat.discipline):
                    fixes[mat.id] = norm
        if target_parent_id and target_parent_id != category.parent_id:
            new_parent = db.scalar(select(Category).where(Category.id == target_parent_id).with_for_update())
            if not new_parent or not new_parent.active or new_parent.parent_id is not None:
                raise HTTPException(422, 'Target parent must be an active main category.')
            if not category.parent_id:
                raise HTTPException(422, 'Only subcategories can be moved to a different main category.')
            category.parent_id = new_parent.id
            affected = db.scalars(
                select(Material).where(
                    or_(Material.category_id == category.id, Material.subcategory_id == category.id)
                )
            ).all()
            for mat in affected:
                mat.main_category_id = new_parent.id
                mat.subcategory_id = category.id
                mat.category_id = category.id
        if not fixes and not target_parent_id:
            raise HTTPException(422, 'Provide a supported discipline, per-material disciplines, or a target parent category.')
        materials_updated = _apply_material_discipline_updates(db, fixes, actor, action_label='admin_reclassify')
        record_review_decision(
            db,
            category_id=category.id,
            decision='reclassify',
            issue_type=assessment.issue_type,
            actor=actor,
            discipline=normalize_supported_discipline(discipline) if discipline else None,
            notes=notes or f'Reclassified {materials_updated} material(s).',
            details={'materials_updated': materials_updated, 'target_parent_id': target_parent_id},
        )
        audit(db, actor, 'category.review_reclassified', entity_id=category.id, materials_updated=materials_updated)

    elif decision == 'merge_duplicate':
        if not assessment.duplicate_ids or len(assessment.duplicate_ids) < 2:
            raise HTTPException(422, 'This category has no active duplicates under the same parent.')
        if not assessment.duplicate_safe:
            raise HTTPException(409, 'Duplicate group has conflicts: ' + ' '.join(assessment.duplicate_blockers))
        canonical_id, archived_ids, moved = _merge_duplicate_group_in_session(
            db, assessment.duplicate_ids, actor
        )
        if not canonical_id:
            raise HTTPException(409, 'Duplicate group changed or is no longer safe to merge; refresh and try again.')
        merged_into = canonical_id

    elif decision == 'archive':
        if db.scalar(select(Category.id).where(Category.parent_id == category.id, Category.active.is_(True)).limit(1)):
            raise HTTPException(409, 'Archive or delete active subcategories first.')
        if db.scalar(
            select(Material.id)
            .where(
                or_(
                    Material.category_id == category.id,
                    Material.main_category_id == category.id,
                    Material.subcategory_id == category.id,
                ),
                Material.active.is_(True),
            )
            .limit(1)
        ):
            raise HTTPException(409, 'Move or archive active materials in this category first.')
        category.active = False
        record_review_decision(
            db,
            category_id=category.id,
            decision='archive',
            issue_type=assessment.issue_type,
            actor=actor,
            notes=notes or 'Archived from category review queue.',
        )
        audit(db, actor, 'category.archived', entity_id=category.id)
    else:
        raise HTTPException(422, f'Unsupported review decision: {decision}')

    resolved_category_id = category.id
    db.commit()
    catalog_cache.invalidate()
    return {
        'category_id': resolved_category_id,
        'decision': decision,
        'materials_updated': materials_updated,
        'merged_into_category_id': merged_into,
        'archived_ids': archived_ids,
    }


def serialize_assessment(item: CategoryAssessment) -> dict:
    return {
        'id': item.category_id,
        'code': item.category_code,
        'name_en': item.category_name_en,
        'name_ar': item.category_name_ar,
        'parent_id': item.parent_id,
        'parent_name_en': item.parent_name_en,
        'hierarchy_path': item.hierarchy_path,
        'proposed_path': item.proposed_path,
        'hierarchy_repair_action': item.hierarchy_repair_action,
        'hierarchy_repair_safe': item.hierarchy_repair_safe,
        'affected_capabilities_count': item.affected_capabilities_count,
        'issue_type': item.issue_type,
        'issue_label': ISSUE_LABELS.get(item.issue_type, item.issue_type),
        'reason': item.reason,
        'secondary_issues': item.secondary_issues,
        'active_material_count': item.active_material_count,
        'inactive_material_count': item.inactive_material_count,
        'missing_discipline_count': item.missing_discipline_count,
        'unsupported_discipline_count': item.unsupported_discipline_count,
        'duplicate_ids': item.duplicate_ids,
        'duplicate_canonical_id': item.duplicate_canonical_id,
        'duplicate_safe': item.duplicate_safe,
        'proposed_action': item.proposed_action,
        'proposed_discipline': item.proposed_discipline,
        'safe_to_auto_fix': item.safe_to_auto_fix,
        'can_approve': item.can_approve,
        'can_retain_empty': item.can_retain_empty,
        'can_merge': item.can_merge,
        'can_archive': item.can_archive,
        'requires_manual_review': item.requires_manual_review,
        'evidence': item.evidence,
        'current_decision': item.current_decision_value,
        'materials_preview': [
            {
                'id': ma.material_id,
                'code': ma.code,
                'name_en': ma.name_en,
                'current_discipline': ma.current_discipline,
                'inferred_discipline': ma.inferred_discipline,
                'safe': ma.safe,
                'evidence': ma.evidence,
                'issue': ma.issue,
            }
            for ma in item.material_assessments
        ],
    }


def build_analysis_payload(
    db: Session,
    *,
    issue_type: str | None = None,
    conflicts_only: bool = False,
    include_resolved: bool = False,
    q: str = '',
    limit: int = 100,
    offset: int = 0,
) -> dict:
    all_items = analyze_categories(db, include_resolved=include_resolved)
    counts_by_issue = Counter(item.issue_type for item in all_items if item.issue_type != 'valid')
    counts_by_action = Counter(item.proposed_action for item in all_items if item.issue_type != 'valid')

    total_pending = sum(1 for item in all_items if item.issue_type != 'valid')
    total_safe_auto_fix = sum(1 for item in all_items if item.safe_to_auto_fix)
    total_safe_hierarchy_repairs = sum(
        1 for item in all_items if item.issue_type == 'hierarchy_depth_exceeded' and item.hierarchy_repair_safe
    )
    total_hierarchy_conflicts = sum(
        1 for item in all_items if item.issue_type in HIERARCHY_BLOCKING_ISSUES
    )
    total_valid_approvable = sum(
        1 for item in all_items if item.can_retain_empty and item.issue_type in {'empty_category', 'inactive_materials_only'}
    )
    total_conflicts = sum(1 for item in all_items if item.requires_manual_review)

    filtered = all_items
    if issue_type:
        filtered = [item for item in filtered if item.issue_type == issue_type]
    if conflicts_only:
        filtered = [item for item in filtered if item.requires_manual_review]
    term = normalize_text(q)
    if term:
        filtered = [
            item
            for item in filtered
            if term in normalize_text(
                f'{item.category_name_en} {item.category_code} {item.parent_name_en or ""} {" ".join(item.hierarchy_path)} {" ".join(item.proposed_path)} {item.reason}'
            )
        ]

    token = sha256(
        repr([(i.category_id, i.issue_type, i.proposed_action, i.proposed_discipline, i.active_material_count) for i in all_items]).encode()
    ).hexdigest()[:24]

    page_items = filtered[offset : offset + limit]
    return {
        'total_pending': total_pending,
        'total_filtered': len(filtered),
        'total_safe_auto_fix': total_safe_auto_fix,
        'total_safe_hierarchy_repairs': total_safe_hierarchy_repairs,
        'total_hierarchy_conflicts': total_hierarchy_conflicts,
        'total_valid_approvable': total_valid_approvable,
        'total_conflicts': total_conflicts,
        'counts_by_issue_type': dict(counts_by_issue),
        'counts_by_action': dict(counts_by_action),
        'preview_token': token,
        'batch_size': BATCH_SIZE,
        'job_size': JOB_SIZE,
        'limit': limit,
        'offset': offset,
        'items': [serialize_assessment(item) for item in page_items],
    }


def _serialize_hierarchy_plan(plan: HierarchyRepairPlan) -> dict:
    return {
        'category_id': plan.category_id,
        'code': plan.code,
        'name_en': plan.name_en,
        'parent_id': plan.parent_id,
        'parent_name_en': plan.parent_name_en,
        'grandparent_id': plan.grandparent_id,
        'grandparent_name_en': plan.grandparent_name_en,
        'current_path': list(plan.current_path),
        'proposed_path': list(plan.proposed_path),
        'repair_action': plan.repair_action,
        'target_parent_id': plan.target_parent_id,
        'target_category_id': plan.target_category_id,
        'affected_materials_count': plan.affected_materials_count,
        'affected_capabilities_count': plan.affected_capabilities_count,
        'safe': plan.safe,
        'status': plan.status,
        'reason': plan.reason,
    }


def _collect_hierarchy_plans(db: Session) -> list[HierarchyRepairPlan]:
    """Compute all hierarchy repair plans in a single lightweight read pass."""
    all_categories = list(db.scalars(select(Category).order_by(Category.name_en, Category.id)).all())
    by_id = {c.id: c for c in all_categories}
    active_categories = [c for c in all_categories if c.active]
    active_ids = {c.id for c in active_categories}

    active_children_by_parent: dict[str, list[Category]] = defaultdict(list)
    for c in active_categories:
        if c.parent_id:
            active_children_by_parent[c.parent_id].append(c)

    total_mat_counts: dict[str, int] = defaultdict(int)
    sibling_supported_by_cat: dict[str, set[str]] = defaultdict(set)
    for mat_active, cat_id, main_cat_id, sub_cat_id, disc_raw, row_cnt in db.execute(
        select(
            Material.active,
            Material.category_id,
            Material.main_category_id,
            Material.subcategory_id,
            Material.discipline,
            func.count(Material.id),
        ).group_by(
            Material.active,
            Material.category_id,
            Material.main_category_id,
            Material.subcategory_id,
            Material.discipline,
        )
    ).all():
        cnt = int(row_cnt or 0)
        supported_disc = normalize_supported_discipline(disc_raw) if mat_active else None
        for cid in {cat_id, main_cat_id, sub_cat_id}:
            if cid and cid in active_ids:
                total_mat_counts[cid] += cnt
                if supported_disc:
                    sibling_supported_by_cat[cid].add(supported_disc)

    cap_keys_by_cat: dict[str, set[tuple[str, str]]] = defaultdict(set)
    cap_counts_by_cat: dict[str, int] = defaultdict(int)
    for cap_cat_id, cap_org_id, cap_city in db.execute(
        select(Capability.category_id, Capability.org_id, Capability.city)
    ).all():
        if cap_cat_id and cap_cat_id in active_ids:
            cap_counts_by_cat[cap_cat_id] += 1
            cap_keys_by_cat[cap_cat_id].add((cap_org_id, cap_city))

    plans_map = _plan_hierarchy_repairs(
        db,
        by_id=by_id,
        active_categories=active_categories,
        active_children_by_parent=active_children_by_parent,
        total_mat_counts=total_mat_counts,
        sibling_supported_by_cat=sibling_supported_by_cat,
        cap_keys_by_cat=cap_keys_by_cat,
        cap_counts_by_cat=cap_counts_by_cat,
    )
    return sorted(plans_map.values(), key=lambda p: (not p.safe, ' / '.join(p.current_path), p.category_id))


def build_hierarchy_repair_preview(
    db: Session,
    *,
    category_ids: list[str] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Analyze all depth > 2 hierarchy issues and return a safe vs manual-review preview."""
    plans = _collect_hierarchy_plans(db)
    if category_ids:
        wanted = set(category_ids)
        plans = [p for p in plans if p.category_id in wanted]

    safe_plans = [p for p in plans if p.safe]
    manual_plans = [p for p in plans if not p.safe]
    action_counts = Counter(p.repair_action for p in plans)
    page = plans[offset : offset + limit]
    return {
        'total_hierarchy_issues': len(plans),
        'safe_repairs': len(safe_plans),
        'manual_review_count': len(manual_plans),
        'affected_materials': sum(p.affected_materials_count for p in safe_plans),
        'affected_capabilities': sum(p.affected_capabilities_count for p in safe_plans),
        'counts_by_action': dict(action_counts),
        'batch_size': BATCH_SIZE,
        'job_size': JOB_SIZE,
        'items': [_serialize_hierarchy_plan(p) for p in page],
    }


def _apply_single_hierarchy_repair(
    db: Session,
    plan: dict,
    actor: User | ReviewActor | None,
) -> tuple[str, int, int]:
    """Apply one verified safe hierarchy repair inside a batch transaction.

    Returns (outcome, materials_updated, references_moved) where outcome is 'repaired' or 'skipped'.
    """
    action = plan['repair_action']
    cat_id = plan['category_id']
    target_parent_id = plan.get('target_parent_id')
    target_category_id = plan.get('target_category_id')

    lock_ids = sorted({i for i in (cat_id, target_parent_id, target_category_id) if i})
    locked = {
        c.id: c
        for c in db.scalars(
            select(Category).where(Category.id.in_(lock_ids)).order_by(Category.id).with_for_update()
        ).all()
    }
    cat = locked.get(cat_id)
    if not cat or not cat.active:
        return 'skipped', 0, 0

    if action == 'reparent_to_main':
        target_main = locked.get(target_parent_id) if target_parent_id else None
        if not target_main or not target_main.active or target_main.parent_id is not None:
            return 'skipped', 0, 0
        if cat.parent_id == target_main.id:
            return 'skipped', 0, 0
        # Verify no active sibling with same normalized name appeared concurrently
        cat_norm = normalize_category_name(cat.name_en)
        siblings = db.scalars(
            select(Category).where(Category.parent_id == target_main.id, Category.active.is_(True), Category.id != cat.id)
        ).all()
        if any(normalize_category_name(s.name_en) == cat_norm for s in siblings):
            return 'skipped', 0, 0
        old_parent_id = cat.parent_id
        cat.parent_id = target_main.id
        mat_res = db.execute(
            update(Material)
            .where(or_(Material.category_id == cat.id, Material.subcategory_id == cat.id))
            .values(main_category_id=target_main.id, subcategory_id=cat.id, category_id=cat.id)
        )
        mats_updated = mat_res.rowcount or 0
        record_review_decision(
            db,
            category_id=cat.id,
            decision='reclassify',
            issue_type='hierarchy_depth_exceeded',
            actor=actor,
            notes=f'Reparented subcategory under main category "{target_main.name_en}" to restore two-level hierarchy.',
            details={
                'hierarchy_repair': True,
                'repair_action': action,
                'old_parent_id': old_parent_id,
                'new_parent_id': target_main.id,
                'materials_updated': mats_updated,
            },
        )
        return 'repaired', mats_updated, 0

    if action == 'merge_into_canonical_subcategory':
        target_sub = locked.get(target_category_id) if target_category_id else None
        if not target_sub or not target_sub.active or not target_sub.parent_id:
            return 'skipped', 0, 0
        # Ensure source has no active children
        if db.scalar(select(Category.id).where(Category.parent_id == cat.id, Category.active.is_(True)).limit(1)):
            return 'skipped', 0, 0
        # Re-verify no capability overlap
        src_caps = set(
            db.execute(select(Capability.org_id, Capability.city).where(Capability.category_id == cat.id)).all()
        )
        if src_caps:
            dst_caps = set(
                db.execute(
                    select(Capability.org_id, Capability.city).where(Capability.category_id == target_sub.id)
                ).all()
            )
            if src_caps & dst_caps:
                return 'skipped', 0, 0

        cat.active = False
        db.flush()

        moved = 0
        for tbl, col in category_references():
            if tbl.name == 'category_review_decisions':
                continue
            res = db.execute(update(tbl).where(col == cat.id).values({col.name: target_sub.id}))
            moved += res.rowcount or 0

        mat_res = db.execute(
            update(Material)
            .where(
                or_(Material.category_id == target_sub.id, Material.subcategory_id == target_sub.id),
                Material.main_category_id != target_sub.parent_id,
            )
            .values(main_category_id=target_sub.parent_id)
        )
        mats_updated = mat_res.rowcount or 0
        if not target_sub.name_ar and cat.name_ar:
            target_sub.name_ar = cat.name_ar

        record_review_decision(
            db,
            category_id=cat.id,
            decision='merge_duplicate',
            issue_type='hierarchy_depth_exceeded',
            actor=actor,
            merged_into_category_id=target_sub.id,
            notes=f'Merged level-3 alias into canonical two-level subcategory "{target_sub.name_en}" ({target_sub.code}).',
            details={
                'hierarchy_repair': True,
                'repair_action': action,
                'canonical_id': target_sub.id,
                'references_moved': moved,
                'materials_updated': mats_updated,
            },
        )
        return 'repaired', mats_updated, moved

    if action == 'promote_parent_to_main':
        parent = locked.get(target_parent_id) if target_parent_id else None
        if not parent or not parent.active:
            return 'skipped', 0, 0
        if parent.parent_id is None:
            return 'skipped', 0, 0
        p_norm = normalize_category_name(parent.name_en)
        existing_roots = db.scalars(
            select(Category).where(Category.parent_id.is_(None), Category.active.is_(True), Category.id != parent.id)
        ).all()
        if any(normalize_category_name(r.name_en) == p_norm for r in existing_roots):
            return 'skipped', 0, 0

        old_gp_id = parent.parent_id
        parent.parent_id = None
        child_ids = list(db.scalars(select(Category.id).where(Category.parent_id == parent.id)).all())
        target_ids = [parent.id, *child_ids]
        mat_res = db.execute(
            update(Material)
            .where(
                or_(Material.category_id.in_(target_ids), Material.subcategory_id.in_(target_ids)),
                Material.main_category_id == old_gp_id,
            )
            .values(main_category_id=parent.id)
        )
        mats_updated = mat_res.rowcount or 0
        record_review_decision(
            db,
            category_id=cat.id,
            decision='reclassify',
            issue_type='hierarchy_depth_exceeded',
            actor=actor,
            notes=f'Promoted "{parent.name_en}" to top-level Main Category, restoring two-level hierarchy.',
            details={
                'hierarchy_repair': True,
                'repair_action': action,
                'promoted_parent_id': parent.id,
                'old_grandparent_id': old_gp_id,
                'materials_updated': mats_updated,
            },
        )
        return 'repaired', mats_updated, 0

    return 'skipped', 0, 0


def _serialize_repair_job(batch: ImportBatch) -> dict:
    meta = dict(batch.preview_metadata or {})
    return {
        'job_id': batch.id,
        'status': batch.status,
        'total_records': int(batch.rows or 0),
        'completed_records': int(batch.completed_rows or 0),
        'repaired_count': int(batch.updated_count or 0),
        'skipped_count': int(batch.unchanged_count or 0),
        'failed_count': int(batch.errors or 0),
        'batches_executed': int(meta.get('batches_executed', 0)),
        'materials_updated': int(meta.get('materials_updated', 0)),
        'references_moved': int(meta.get('references_moved', 0)),
        'remaining_safe': int(meta.get('remaining_safe', 0)),
        'has_more': bool(meta.get('has_more', False)),
        'errors': list(meta.get('errors', []))[:50],
        'failure_reason': batch.failure_reason,
    }


def _execute_hierarchy_repair_job(
    job_id: str,
    plans: list[dict],
    actor: User | ReviewActor | None,
    batch_size: int,
    remaining_after_job: int,
    session_factory: Callable[[], Session],
) -> None:
    """Run a persisted hierarchy repair job in independent committed batches of 30."""
    batches_executed = 0
    completed = 0
    repaired = 0
    skipped = 0
    failed = 0
    materials_updated = 0
    references_moved = 0
    errors: list[dict] = []

    with session_factory() as init_db:
        job = init_db.get(ImportBatch, job_id)
        if not job:
            return
        job.status = 'processing'
        job.stage = 'applying'
        init_db.commit()

    for start in range(0, len(plans), batch_size):
        chunk = plans[start : start + batch_size]
        batches_executed += 1
        batch_repaired_ids: list[str] = []
        batch_repaired = 0
        batch_skipped = 0
        batch_mats = 0
        batch_refs = 0
        with session_factory() as batch_db:
            try:
                for plan in chunk:
                    outcome, m_upd, r_mov = _apply_single_hierarchy_repair(batch_db, plan, actor)
                    if outcome == 'repaired':
                        batch_repaired += 1
                        batch_repaired_ids.append(plan['category_id'])
                        batch_mats += m_upd
                        batch_refs += r_mov
                    else:
                        batch_skipped += 1
                if batch_repaired_ids:
                    audit(
                        batch_db,
                        actor,
                        'category.hierarchy_repaired_batch',
                        entity_id=job_id,
                        count=len(batch_repaired_ids),
                        category_ids=batch_repaired_ids,
                        materials_updated=batch_mats,
                        references_moved=batch_refs,
                    )
                batch_db.commit()
                completed += len(chunk)
                repaired += batch_repaired
                skipped += batch_skipped
                materials_updated += batch_mats
                references_moved += batch_refs
            except Exception as exc:
                batch_db.rollback()
                logger.exception('Hierarchy repair batch failed job_id=%s batch=%s', job_id, batches_executed)
                failed += len(chunk)
                completed += len(chunk)
                errors.append({'batch': batches_executed, 'error': str(exc)[:300]})

        # Persist progress after every committed batch
        with session_factory() as prog_db:
            job = prog_db.get(ImportBatch, job_id)
            if job:
                job.completed_rows = completed
                job.updated_count = repaired
                job.unchanged_count = skipped
                job.errors = failed
                job.preview_metadata = {
                    'batches_executed': batches_executed,
                    'materials_updated': materials_updated,
                    'references_moved': references_moved,
                    'errors': errors[:50],
                }
                prog_db.commit()

    if repaired:
        catalog_cache.invalidate()

    with session_factory() as verify_db:
        remaining_plans = _collect_hierarchy_plans(verify_db)
        remaining_safe = sum(1 for p in remaining_plans if p.safe)
        job = verify_db.get(ImportBatch, job_id)
        if job:
            job.completed_rows = completed
            job.updated_count = repaired
            job.unchanged_count = skipped
            job.errors = failed
            job.stage = 'completed'
            job.status = 'completed_with_errors' if failed else 'completed'
            job.preview_metadata = {
                'batches_executed': batches_executed,
                'materials_updated': materials_updated,
                'references_moved': references_moved,
                'remaining_safe': remaining_safe,
                'has_more': remaining_after_job > 0 and remaining_safe > 0,
                'errors': errors[:50],
            }
            verify_db.commit()


def start_hierarchy_repair_job(
    *,
    actor: User | ReviewActor | None,
    category_ids: list[str] | None = None,
    job_limit: int = JOB_SIZE,
    batch_size: int = BATCH_SIZE,
    async_mode: bool = True,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    """Create and run (async or sync) a durable batched hierarchy repair job (30/batch, up to 300/job)."""
    job_limit = max(1, min(job_limit, JOB_SIZE))
    batch_size = max(1, min(batch_size, BATCH_SIZE))
    actor_id = actor.id if actor else None

    with session_factory() as db:
        # Prevent duplicate concurrent hierarchy repair jobs
        cutoff = utcnow() - timedelta(minutes=10)
        active_job = db.scalar(
            select(ImportBatch)
            .where(
                ImportBatch.kind == 'hierarchy_repair',
                ImportBatch.status.in_(['queued', 'processing']),
                ImportBatch.updated_at >= cutoff,
            )
            .order_by(ImportBatch.created_at.desc())
            .limit(1)
        )
        if active_job:
            return _serialize_repair_job(active_job)

        all_plans = _collect_hierarchy_plans(db)
        if category_ids:
            wanted = set(category_ids)
            all_plans = [p for p in all_plans if p.category_id in wanted]
        safe_plans = [_serialize_hierarchy_plan(p) for p in all_plans if p.safe]
        job_slice = safe_plans[:job_limit]
        remaining_after_job = max(0, len(safe_plans) - len(job_slice))

        if not job_slice:
            return {
                'job_id': None,
                'status': 'completed',
                'total_records': 0,
                'completed_records': 0,
                'repaired_count': 0,
                'skipped_count': 0,
                'failed_count': 0,
                'batches_executed': 0,
                'materials_updated': 0,
                'references_moved': 0,
                'remaining_safe': 0,
                'has_more': False,
                'errors': [],
                'failure_reason': None,
            }

        job = ImportBatch(
            kind='hierarchy_repair',
            filename='category_hierarchy_repair',
            status='queued',
            stage='queued',
            rows=len(job_slice),
            completed_rows=0,
            updated_count=0,
            unchanged_count=0,
            errors=0,
            initiated_by=actor_id,
            import_options={'batch_size': batch_size, 'job_limit': job_limit},
            preview_metadata={
                'batches_executed': 0,
                'materials_updated': 0,
                'references_moved': 0,
                'remaining_safe': len(safe_plans),
                'has_more': remaining_after_job > 0,
                'errors': [],
            },
        )
        db.add(job)
        db.commit()
        job_id = job.id
        initial_payload = _serialize_repair_job(job)

    if async_mode:
        worker = threading.Thread(
            target=_execute_hierarchy_repair_job,
            args=(job_id, job_slice, actor, batch_size, remaining_after_job, session_factory),
            daemon=True,
            name=f'hierarchy-repair-{job_id[:8]}',
        )
        worker.start()
        return initial_payload

    _execute_hierarchy_repair_job(job_id, job_slice, actor, batch_size, remaining_after_job, session_factory)
    with session_factory() as verify_db:
        done_job = verify_db.get(ImportBatch, job_id)
        return _serialize_repair_job(done_job) if done_job else initial_payload


def get_hierarchy_repair_job(db: Session, job_id: str) -> dict:
    """Fetch persisted progress of a hierarchy repair job."""
    job = db.scalar(
        select(ImportBatch).where(ImportBatch.id == job_id, ImportBatch.kind == 'hierarchy_repair')
    )
    if not job:
        raise HTTPException(404, 'Hierarchy repair job not found')
    return _serialize_repair_job(job)
