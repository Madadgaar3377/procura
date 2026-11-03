"""Explicit supplier publication choices; imported contacts are never a fallback."""
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Organization, PublicProfileConsent, User
from app.security import audit, current_user, org_member

PublicField = Literal['trading_name', 'company_type', 'description', 'website',
                      'business_phone', 'business_email', 'address', 'region', 'country']

PUBLIC_FIELDS = frozenset(get_args(PublicField))


class PublicationPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fields: list[PublicField] = Field(default_factory=list, max_length=9)


def consent_available(db: Session) -> bool:
    # Per-session only: a newly applied migration must be visible on the next read.
    if 'public_consent_available' not in db.info:
        db.info['public_consent_available'] = inspect(db.connection()).has_table('organization_public_consent')
    return db.info['public_consent_available']


router = APIRouter(tags=['Organizations & Verification'])


@router.get('/{org_id}/public-profile', response_model=PublicationPreferences)
def preferences(org_id: str, db: Annotated[Session, Depends(get_db)],
                user: Annotated[User, Depends(current_user)]):
    org_member(db, user, org_id, {'owner', 'manager'})
    if not db.get(Organization, org_id):
        raise HTTPException(404, 'Organization not found')
    if not consent_available(db):
        raise HTTPException(503, 'Public profile preferences require the pending database migration')
    consent = db.get(PublicProfileConsent, org_id)
    return {'fields': consent.fields if consent else []}


@router.patch('/{org_id}/public-profile', response_model=PublicationPreferences)
def save_preferences(org_id: str, payload: PublicationPreferences, db: Annotated[Session, Depends(get_db)],
                     user: Annotated[User, Depends(current_user)]):
    org_member(db, user, org_id, {'owner', 'manager'})
    # Serialize first-time consent writes on the existing organization row.
    org = db.scalar(select(Organization).where(Organization.id == org_id).with_for_update())
    if not org or not org.is_supplier:
        raise HTTPException(404, 'Supplier not found')
    if not consent_available(db):
        raise HTTPException(503, 'Public profile preferences require the pending database migration')
    consent = db.get(PublicProfileConsent, org_id)
    if consent is None:
        consent = PublicProfileConsent(org_id=org_id)
        db.add(consent)
    consent.fields = sorted(set(payload.fields))
    audit(db, user, 'organization.public_visibility_updated', org_id, org_id)
    db.commit()
    return {'fields': consent.fields}
