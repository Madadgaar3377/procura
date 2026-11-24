"""Material Master hybrid matching endpoints (exact -> PG search -> Gemini fallback)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, User
from app.modules.rfqs.material_matching import classify_material_description
from app.security import current_user

router = APIRouter(prefix='/materials', tags=['Material Matching'])


class MaterialMatchRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    specifications: str = Field(default='', max_length=1000)
    unit: str | None = Field(default=None, max_length=40)
    discipline: str | None = Field(default=None, max_length=60)
    main_category: str | None = Field(default=None, max_length=180)
    subcategory: str | None = Field(default=None, max_length=180)
    ai_enabled: bool = True


class BulkMaterialMatchRequest(BaseModel):
    items: list[MaterialMatchRequest] = Field(..., min_length=1, max_length=200)
    ai_enabled: bool = True


@router.post('/match')
def match_material(
    payload: MaterialMatchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    specs = ' '.join(
        part for part in (
            payload.specifications,
            payload.discipline or '',
            payload.main_category or '',
            payload.subcategory or '',
        )
        if part
    ).strip()
    return classify_material_description(
        db,
        payload.description,
        specifications=specs,
        unit=payload.unit,
        ai_enabled=payload.ai_enabled,
    )


@router.post('/match/bulk')
def match_materials_bulk(
    payload: BulkMaterialMatchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    categories = {c.id: c for c in db.scalars(select(Category).where(Category.active.is_(True)))}
    results = []
    for item in payload.items:
        specs = ' '.join(
            part for part in (
                item.specifications,
                item.discipline or '',
                item.main_category or '',
                item.subcategory or '',
            )
            if part
        ).strip()
        results.append(
            classify_material_description(
                db,
                item.description,
                specifications=specs,
                unit=item.unit,
                ai_enabled=payload.ai_enabled and item.ai_enabled,
                categories=categories,
            )
        )
    return {'total': len(results), 'results': results}
