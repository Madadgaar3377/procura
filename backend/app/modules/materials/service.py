from __future__ import annotations

import re
import unicodedata
from hashlib import sha256
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Material, MaterialRevision, MeasurementUnit, User, uid

UNIT_NAMES = {
    'PCS': 'Pieces', 'EA': 'Each', 'KG': 'Kilogram', 'G': 'Gram', 'M': 'Meter',
    'M2': 'Square meter', 'M3': 'Cubic meter', 'SET': 'Set', 'NO.': 'Number',
    'L': 'Liter', 'LTR': 'Liter', 'TON': 'Metric ton', 'MT': 'Metric ton',
    'BOX': 'Box', 'ROLL': 'Roll', 'BAG': 'Bag', 'PAIR': 'Pair', 'LOT': 'Lot',
    'DAY': 'Day', 'HR': 'Hour', 'FT': 'Foot', 'IN': 'Inch',
}
UNIT_ALIASES = {'EA.': 'EA', 'EACH': 'EA', 'PC': 'PCS', 'PIECE': 'PCS', 'PIECES': 'PCS',
                'KGS': 'KG', 'M²': 'M2', 'SQM': 'M2', 'M³': 'M3', 'CUM': 'M3',
                'LITRE': 'L', 'LITRES': 'L', 'LITER': 'L', 'LITERS': 'L', 'METRE': 'M', 'METER': 'M'}


def normalize_text(value: str | None) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def normalize_category_name(value: str) -> str:
    return normalize_text(value)


def normalize_unit(value: str) -> str:
    code = unicodedata.normalize('NFKC', value).strip().upper().replace('²', '2').replace('³', '3')
    code = re.sub(r'\s+', '', code)
    return UNIT_ALIASES.get(code, code)


def unit_display_name(code: str) -> str:
    return UNIT_NAMES.get(code, code)


def ensure_unit(db: Session, raw_unit: str, *, create_unknown: bool = False,
                cache: dict[str, str] | None = None) -> tuple[str, str]:
    code = normalize_unit(raw_unit)
    if not code or len(code) > 30:
        raise HTTPException(422, 'Enter a supported unit value up to 30 characters')
    if cache is not None and code in cache:
        return code, raw_unit.strip()
    unit = db.scalar(select(MeasurementUnit).where(MeasurementUnit.code == code, MeasurementUnit.active.is_(True)))
    if not unit and code not in UNIT_NAMES and not create_unknown:
        raise HTTPException(422, f'Unit {code} is not configured. Ask an administrator to add it first.')
    if not unit and (code in UNIT_NAMES or create_unknown):
        unit = MeasurementUnit(code=code, name_en=unit_display_name(code), active=True)
        db.add(unit)
        db.flush()
    if cache is not None:cache[code]=code
    return code, raw_unit.strip()


def category_code(parent_id: str | None, normalized_name: str) -> str:
    digest = sha256(f'{parent_id or "root"}\x1f{normalized_name}'.encode('utf-8')).hexdigest()[:28].upper()
    return f'CAT-{digest}'


def resolve_category(db: Session, name: str, parent: Category | None, *, create: bool,
                     cache: dict[tuple[str | None, str], Category] | None = None,
                     name_ar: str = '', sort_order: int = 0) -> tuple[Category, bool]:
    label = ' '.join(unicodedata.normalize('NFKC', name).split())
    normalized = normalize_category_name(label)
    if not normalized or len(label) > 180:
        raise HTTPException(422, 'Category name must contain 1 to 180 characters')
    parent_id = parent.id if parent else None
    key = (parent_id, normalized)
    if cache is not None and key in cache:
        return cache[key], False
    candidates = db.scalars(select(Category).where(
        Category.parent_id == parent_id, Category.active.is_(True),
    )).all()
    matches = [item for item in candidates if normalize_category_name(item.normalized_name or item.name_en) == normalized]
    if len(matches) > 1:
        raise HTTPException(409, f'Multiple active categories named {label} exist under this parent; resolve the category records first')
    if matches:
        if cache is not None:
            cache[key] = matches[0]
        return matches[0], False
    code = category_code(parent_id, normalized)
    existing = db.scalar(select(Category).where(Category.code == code))
    if existing:
        if existing.parent_id != parent_id or normalize_category_name(existing.name_en) != normalized:
            raise HTTPException(409, f'Generated category code conflicts with an existing category: {label}')
        if not existing.active:
            raise HTTPException(409, f'Category {label} is archived. Restore it before importing materials')
        if cache is not None:
            cache[key] = existing
        return existing, False
    if not create:
        raise HTTPException(404, f'Category {label} was not found')
    category = Category(code=code, name_en=label, normalized_name=normalized, name_ar=name_ar[:180],
                        parent_id=parent_id, active=True, sort_order=sort_order)
    db.add(category)
    db.flush()
    if cache is not None:
        cache[key] = category
    return category, True


def material_snapshot(material: Material) -> dict[str, Any]:
    return {
        'code': material.code, 'name_en': material.name_en, 'name_ar': material.name_ar,
        'description': material.description, 'arabic_description': material.arabic_description,
        'unit': material.unit, 'source_unit': material.source_unit,
        'category_id': material.category_id, 'main_category_id': material.main_category_id,
        'subcategory_id': material.subcategory_id, 'discipline': material.discipline,
        'source_sn': material.source_sn, 'source_material_code': material.source_material_code,
        'expenditure_code': material.expenditure_code, 'specs_template': material.specs_template,
        'active': material.active, 'version': material.version,
    }


def material_identity(*, main_category: str, subcategory: str, description: str, unit: str,
                      specifications: str, discipline: str | None = None) -> str:
    values = (main_category, subcategory, description, unit, discipline) if discipline else (main_category, subcategory, description, unit)
    normalized = '\x1f'.join(map(normalize_text, values))
    return sha256(normalized.encode('utf-8')).hexdigest()


def find_exact_material(db: Session, *, category_id: str, description: str, unit: str,
                        specifications: str) -> Material | None:
    normalized = normalize_text(description)
    matches = db.scalars(select(Material).where(
        Material.category_id == category_id,
        Material.active.is_(True),
        Material.unit == unit,
        (Material.normalized_description == normalized) | (Material.name_en == description),
    ).limit(20)).all()
    return matches[0] if matches else None


def create_material_record(db: Session, *, actor: User | None, main_category: Category,
                           subcategory: Category | None, description: str, unit: str,
                           source_unit: str, specifications: str = '', arabic_description: str = '',
                           source_sn: str | None = None, expenditure_code: str | None = None,
                           discipline: str | None = None, code: str | None = None,
                           import_batch_id: str | None = None, import_row_number: int | None = None,
                           metadata: dict[str, Any] | None = None,
                           unit_cache: dict[str,str] | None = None,
                           create_unknown_unit: bool = False) -> tuple[Material, bool]:
    leaf = subcategory or main_category
    canonical_unit, original_unit = ensure_unit(db, unit or source_unit, create_unknown=create_unknown_unit,cache=unit_cache)
    if source_unit.strip():
        original_unit=source_unit.strip()
    normalized_description = normalize_text(description)
    if len(normalized_description) < 3 or len(description.strip()) > 250:
        raise HTTPException(422, 'Material description must contain 3 to 250 characters')
    specifications = specifications.strip()
    identity = material_identity(main_category=main_category.normalized_name or main_category.name_en,
                                 subcategory=(subcategory.normalized_name or subcategory.name_en) if subcategory else '',
                                 description=description.strip(), unit=canonical_unit,
                                 specifications=specifications, discipline=discipline)
    material_code = (code or 'MAT-' + identity[:28].upper()).strip().upper()
    code_owner = db.scalar(select(Material.id).where(Material.code == material_code))
    if code_owner:
        raise HTTPException(409, f'Material code {material_code} already exists')
    material = Material(
        id=uid(), code=material_code, category_id=leaf.id, main_category_id=main_category.id,
        subcategory_id=subcategory.id if subcategory else None,
        name_en=description, normalized_description=normalized_description,
        name_ar=arabic_description.strip(), arabic_description=arabic_description.strip(),
        description=specifications, unit=canonical_unit, source_unit=original_unit,
        specs_template={**(metadata or {}), **({'technical_specifications': specifications} if specifications else {})},
        discipline=discipline, source_sn=source_sn.strip() if source_sn else None,
        source_material_code=source_sn.strip() if source_sn else None,
        expenditure_code=expenditure_code.strip() if expenditure_code else None,
        source_identity=identity, import_batch_id=import_batch_id,
        import_row_number=import_row_number, version=1, active=True,
    )
    db.add(material)
    db.add(MaterialRevision(material_id=material.id, version=1, actor_user_id=actor.id if actor else None,
                            action='imported' if import_batch_id else 'created', snapshot=material_snapshot(material)))
    return material, True
