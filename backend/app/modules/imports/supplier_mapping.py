"""Deterministic supplier-trade mapping onto the existing category tree."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models import Capability, Category, ImportBatch, ImportedSupplierTrade, Material, Organization, User, Verification, utcnow
from app.modules.materials.service import category_code, create_material_record, material_identity, normalize_category_name, normalize_text, normalize_unit
from app.security import audit

DISCIPLINES = {"civil": "Civil", "electrical": "Electrical", "mechanical": "Mechanical"}
_LABEL_ALIASES = {"lighting fixture":"lighting fixtures", "lighting fixture lights":"lighting fixtures",
    "wiring device":"wiring devices", "low current":"low current elv"}


def label_key(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().strip()
    normalized=" ".join(re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).split())
    return _LABEL_ALIASES.get(normalized,normalized)


ALIASES = {
    "low current elv": "Low Current (ELV)",
    "low current": "Low Current (ELV)",
    "lighting fixture": "Lighting Fixtures",
    "lighting fixtures": "Lighting Fixtures",
    "wiring devices": "Wiring Devices",
}


def extract_website(raw: str | None) -> tuple[str, str]:
    value = raw or ""
    match = re.search(r"(?:https?://|www\.)[^\s,;]+", value, re.I)
    website = match.group(0).rstrip(".,);]") if match else ""
    if website.casefold().startswith("www."):
        website="https://"+website
    trade = value[:match.start()] + value[match.end():] if match else value
    trade = re.sub(r"\bwebsite\s*:\s*", " ", trade, flags=re.I)
    return " ".join(trade.replace(";", " ").split()).strip(" ,;/"), website


def split_path(value: str | None) -> list[str]:
    return [part.strip(" \t\r\n.;:") for part in re.split(r"\s*(?:/|>|→|::|\\)\s*", value or "") if part.strip(" \t\r\n.;:")]


@dataclass
class MappingResult:
    category: Category | None
    discipline: str | None
    categories_created: int = 0
    reason: str | None = None


def _find_sibling(db: Session, parent_id: str | None, name: str) -> list[Category]:
    candidates = db.scalars(select(Category).where(Category.parent_id == parent_id, Category.active.is_(True))).all()
    wanted = label_key(name)
    return [item for item in candidates if wanted in {label_key(item.name_en), label_key(item.normalized_name)}]


def _get_or_create(db: Session, name: str, parent: Category | None, actor: User | None,
                   *, create: bool, created: list[Category]) -> Category | None:
    name = " ".join(unicodedata.normalize("NFKC", name).split())
    if not name or len(name) > 180:
        return None
    parent_id = parent.id if parent else None
    if parent:
        parent = db.scalar(select(Category).where(Category.id == parent.id).with_for_update())
    matches = _find_sibling(db, parent_id, name)
    if len(matches) > 1:
        return None
    if matches:
        return matches[0]
    if not create:
        return None
    normalized = normalize_category_name(name)
    code = category_code(parent_id, normalized)
    existing = db.scalar(select(Category).where(Category.code == code))
    if existing:
        return existing if existing.active and existing.parent_id == parent_id else None
    try:
        with db.begin_nested():
            category = Category(code=code, name_en=name, normalized_name=normalized,
                                parent_id=parent_id, active=True)
            db.add(category)
            db.flush()
        created.append(category)
        audit(db, actor, "category.created_from_supplier_mapping", entity_id=category.id,
              parent_id=parent_id, name=name)
        return category
    except IntegrityError:
        # Deterministic codes and the existing unique constraint make retries safe.
        return db.scalar(select(Category).where(Category.code == code, Category.active.is_(True)))


def resolve_supplier_path(db: Session, *, raw_path: str, actor: User | None = None,
                          create: bool = True) -> MappingResult:
    """Resolve an explicit source hierarchy; only create beneath a named discipline.

    Category currently supports two levels. Additional source segments are kept in
    trade keywords by the caller rather than being fabricated as deeper categories.
    """
    parts = split_path(raw_path)
    if not parts:
        return MappingResult(None, None, reason="Category path is empty")
    if label_key(parts[0]) in {"low current elv","low current"}:
        parts=["Electrical","Low Current (ELV)",*parts[1:]]
    discipline = DISCIPLINES.get(label_key(parts[0]))
    created: list[Category] = []
    if discipline:
        root = _get_or_create(db, discipline, None, actor, create=create, created=created)
        if not root:
            return MappingResult(None, discipline, len(created), "Discipline category is ambiguous or unavailable")
        if len(parts) == 1:
            return MappingResult(root, discipline, len(created))
        label = ALIASES.get(label_key(parts[1]), parts[1])
        leaf = _get_or_create(db, label, root, actor, create=create, created=created)
        if not leaf:
            return MappingResult(None, discipline, len(created), "Subcategory is ambiguous or unavailable")
        return MappingResult(leaf, discipline, len(created))

    # For non-discipline roots, only reuse a unique, already-existing hierarchy.
    roots = _find_sibling(db, None, parts[0])
    if len(roots) != 1:
        return MappingResult(None, None, reason="Category does not identify one of Civil, Electrical, Mechanical or an existing root")
    root = roots[0]
    if len(parts) == 1:
        return MappingResult(root, None)
    children = _find_sibling(db, root.id, parts[1])
    if len(children) != 1:
        return MappingResult(None, None, reason="Existing category path is ambiguous or incomplete")
    return MappingResult(children[0], None)


def ensure_import_verification(db: Session, org, admin: User | None, *, reason: str = "") -> bool:
    """Verify only organizations created/owned by admin import workflows."""
    if org.source not in {"imported", "admin_created"} or org.verified_status == "rejected":
        return False
    existing = db.scalar(select(Verification.id).where(Verification.org_id == org.id,
        Verification.status == "approved").limit(1))
    if org.verified_status == "verified" and existing:
        return False
    now = utcnow()
    verification = Verification(org_id=org.id, cr_number=org.cr_number, status="approved",
        reviewer_id=admin.id if admin else None,
        reviewer_notes=reason or ("Approved by the authenticated administrator who imported this supplier." if admin else
            "System migration: source is an administrator-managed import; original administrator identity is unavailable."),
        reviewed_at=now)
    db.add(verification)
    org.verified_status = "verified"
    audit(db, admin, "supplier.automatically_verified", org.id, verification.id,
          source=org.source, system_migration=admin is None)
    return True


def ensure_trade_record(db: Session, org: Organization, batch: ImportBatch, *, raw_trade: str,
                        raw_type: str, raw_brands: str = "", raw_location: str = "") -> ImportedSupplierTrade:
    values = (raw_trade.strip(), raw_type.strip(), raw_brands.strip(), raw_location.strip())
    candidates = db.scalars(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id == org.id)).all()
    for item in candidates:
        if (item.raw_trade.strip(), item.raw_type.strip(), item.raw_brands.strip(), item.raw_location.strip()) == values:
            return item
    item = ImportedSupplierTrade(org_id=org.id, import_batch_id=batch.id,
        raw_trade=values[0], raw_type=values[1][:150], raw_brands=values[2],
        raw_location=values[3][:150], mapping_status="unreviewed")
    db.add(item)
    db.flush()
    return item


def apply_supplier_trade_mapping(db: Session, *, org: Organization, trade: ImportedSupplierTrade,
        raw_path: str, raw_trade: str, city: str, description: str = "", unit: str = "",
        actor: User | None = None, batch_id: str | None = None, row_number: int | None = None,
        auto_confirm: bool = True) -> dict:
    """Map a source path to a real capability; never fabricate materials or service cities."""
    raw_trade, extracted_website = extract_website(raw_trade)
    if extracted_website and not org.website:
        org.website=extracted_website[:300]
    resolved = resolve_supplier_path(db, raw_path=raw_path, actor=actor, create=True)
    if not resolved.category:
        trade.mapping_status = "unreviewed"
        return {"status": "pending", "reason": resolved.reason or "Category mapping requires review",
                "categories_created": resolved.categories_created, "materials_created": 0,
                "capability_created": False}
    category = resolved.category
    service_city = " ".join((city or "").split())
    capability = db.scalar(select(Capability).where(Capability.org_id == org.id,
        Capability.category_id == category.id, func.lower(func.trim(Capability.city)) == service_city.casefold()).with_for_update())
    capability_created = capability is None
    if capability is None:
        try:
            with db.begin_nested():
                capability = Capability(org_id=org.id, category_id=category.id, city=service_city,
                    material_ids=[], trade_keywords="", confirmed=auto_confirm,
                    accepts_rfqs=auto_confirm, eligibility_status="enabled", mapping_source="supplier_import")
                db.add(capability);db.flush()
        except IntegrityError:
            capability=db.scalar(select(Capability).where(Capability.org_id==org.id,
                Capability.category_id==category.id,func.lower(func.trim(Capability.city))==service_city.casefold()).with_for_update())
            capability_created=False
    keyword_values=[raw_trade,*split_path(raw_path)[2:]]
    for keyword in keyword_values:
        keyword=keyword.strip()
        if not keyword:continue
        existing_keywords = [part.strip() for part in (capability.trade_keywords or "").split(" | ") if part.strip()]
        if keyword not in existing_keywords:
            capability.trade_keywords = " | ".join([*existing_keywords, keyword])[:5000]
    if auto_confirm and capability.eligibility_status not in {"rejected", "revoked", "suspended"}:
        capability.confirmed = True
        capability.accepts_rfqs = True
        capability.eligibility_status = "enabled"
        capability.rejection_reason = None
    capability.mapping_source = capability.mapping_source or "supplier_import"
    materials_created = 0
    material_warning = None
    if not description:
        material_warning = "Material description is empty; category capability was saved without a material"
    elif len(description.strip()) < 3 or len(description.strip()) > 250:
        material_warning = "Material description must contain 3 to 250 characters; category capability was saved without a material"
    elif not unit:
        material_warning = "Unit is missing; category capability was saved without a material"
    if description and unit and not material_warning:
        normalized_description = normalize_text(description)
        try:
            with db.begin_nested():
                normalized_unit = normalize_unit(unit)
                material=None
                if resolved.discipline:
                    identity = material_identity(main_category=(db.get(Category, category.parent_id).normalized_name or db.get(Category, category.parent_id).name_en) if category.parent_id else category.name_en,
                        subcategory=(category.normalized_name or category.name_en) if category.parent_id else "",
                        description=description.strip(), unit=normalized_unit, specifications="", discipline=resolved.discipline)
                    material = db.scalar(select(Material).where(Material.source_identity == identity))
                material=material or db.scalar(select(Material).where(Material.category_id==category.id,
                    Material.normalized_description==normalized_description,Material.unit==normalized_unit,Material.active.is_(True)).limit(1))
                if material is None and resolved.discipline and len(normalized_description) >= 3 and len(description.strip()) <= 250 and normalized_unit:
                    main_category = db.get(Category, category.parent_id) if category.parent_id else category
                    subcategory = category if category.parent_id else None
                    material, created = create_material_record(db, actor=actor, main_category=main_category,
                        subcategory=subcategory, description=description.strip(), unit=unit.strip(), source_unit=unit.strip(),
                        discipline=resolved.discipline, import_batch_id=batch_id, import_row_number=row_number,
                        create_unknown_unit=True)
                    materials_created += int(created)
                if material and material.id not in (capability.material_ids or []):
                    capability.material_ids = [*(capability.material_ids or []), material.id]
                elif material is None and not resolved.discipline:
                    material_warning="Discipline is unresolved; no material was created"
        except Exception:
            # A bad material row cannot undo a category capability or supplier.
            pass
    trade.mapping_status = "mapped" if auto_confirm else "mapped_pending_approval"
    return {"status": "saved", "reason": None, "material_warning":material_warning,"categories_created": resolved.categories_created,
            "materials_created": materials_created, "capability_created": capability_created,
            "capability_id": capability.id, "discipline": resolved.discipline}
