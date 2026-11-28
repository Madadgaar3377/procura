"""Safe public projection: publication never falls back to account/import contacts."""
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity import normalize_email, normalize_phone
from app.models import Organization, PublicProfileConsent
from app.modules.organizations.public_profile import PUBLIC_FIELDS, consent_available
from app.modules.organizations.supplier_admin import assess_description
from app.profile_schema import missing_profile_columns


def _project_public_fields(org: Organization, selected: list[str], vocabulary: set[str] | None = None) -> dict:
    result = {
        key: value.strip()
        for key in selected
        if isinstance((value := getattr(org, key, None)), str) and value.strip()
    }
    if 'description' in result:
        result['description'] = assess_description(result['description'], vocabulary)['description']
        if not result['description']:
            result.pop('description')
    for key, normalize in [('business_phone', normalize_phone), ('business_email', normalize_email)]:
        if key in result:
            try:
                result[key] = normalize(result[key])
            except ValueError:
                result.pop(key)
    if 'website' in result:
        try:
            value = result['website']
            url = urlsplit(value)
            if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password or any(ord(c) < 33 for c in value):
                raise ValueError('Invalid website')
            _ = url.port
        except ValueError:
            result.pop('website')
    return result


def published_details_batch(db: Session, org_ids: list[str], orgs_by_id: dict[str, Organization] | None = None) -> dict[str, dict]:
    """Batch-load safe public organization projections without N+1 queries."""
    if not org_ids:
        return {}
    consents: dict[str, list[str]] = {}
    if consent_available(db):
        for oid, fields in db.execute(
            select(PublicProfileConsent.org_id, PublicProfileConsent.fields).where(PublicProfileConsent.org_id.in_(org_ids))
        ).all():
            consents[oid] = list(fields or [])
    missing = missing_profile_columns(db)
    allowed = PUBLIC_FIELDS
    if orgs_by_id is None:
        orgs_by_id = {
            org.id: org
            for org in db.scalars(select(Organization).where(Organization.id.in_(org_ids))).all()
        }
    output: dict[str, dict] = {}
    for oid in org_ids:
        org = orgs_by_id.get(oid)
        if not org:
            output[oid] = {}
            continue
        selected = sorted((set(consents.get(oid, ())) | {'website', 'description'}) & allowed - missing)
        output[oid] = _project_public_fields(org, selected) if selected else {}
    return output


def published_details(db: Session, org_id: str, vocabulary: set[str] | None = None) -> dict:
    fields = db.scalar(select(PublicProfileConsent.fields).where(PublicProfileConsent.org_id == org_id)) or [] if consent_available(db) else []
    allowed = PUBLIC_FIELDS
    # Website and company description are organization-level profile fields.
    # Contact channels and address remain hidden unless the supplier opted in.
    selected = sorted((set(fields) | {'website','description'}) & allowed - missing_profile_columns(db))
    if not selected:
        return {}
    org = db.get(Organization, org_id)
    return _project_public_fields(org, selected, vocabulary) if org else {}
