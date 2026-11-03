"""Safe public projection: publication never falls back to account/import contacts."""
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity import normalize_email, normalize_phone
from app.models import Organization, PublicProfileConsent
from app.modules.organizations.public_profile import PUBLIC_FIELDS, consent_available
from app.profile_schema import missing_profile_columns


def published_details(db: Session, org_id: str) -> dict:
    if not consent_available(db):
        return {}
    fields = db.scalar(select(PublicProfileConsent.fields).where(PublicProfileConsent.org_id == org_id)) or []
    if not fields:
        return {}
    allowed = PUBLIC_FIELDS
    selected = sorted(set(fields) & allowed - missing_profile_columns(db))
    if not selected:
        return {}
    row = db.execute(select(*(getattr(Organization, key) for key in selected)).where(Organization.id == org_id)).one()
    result = {key: value.strip() for key, value in zip(selected, row, strict=True) if isinstance(value, str) and value.strip()}
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
