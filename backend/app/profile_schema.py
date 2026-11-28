"""Read compatibility while additive schema migrations are pending.

Never migrate a configured database implicitly. Missing optional fields read as
NULL; writes to unavailable fields must fail explicitly rather than lose data.
Column introspection is cached per database engine so normal requests never pay
repeated pg_catalog round-trips.
"""
import time
import weakref

from sqlalchemy import inspect, literal
from sqlalchemy.orm import with_expression

PROFILE_COLUMNS = frozenset({
    'trading_name', 'company_type', 'description', 'contact_name', 'designation',
    'business_email', 'business_phone', 'country', 'region', 'profile_status',
})

_BIND_COLUMN_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_CACHE_TTL_SECONDS = 300.0


def missing_profile_columns(session):
    key = 'procura.missing_profile_columns'
    if key in session.info:
        return session.info[key]
    bind = session.get_bind()
    now = time.monotonic()
    try:
        cached = _BIND_COLUMN_CACHE.get(bind)
    except TypeError:
        cached = None
    if cached is not None and (not cached[1] or now - cached[0] < _CACHE_TTL_SECONDS):
        session.info[key] = cached[1]
        return cached[1]
    columns = {column['name'] for column in inspect(session.connection()).get_columns('organizations')}
    missing = PROFILE_COLUMNS - columns
    try:
        _BIND_COLUMN_CACHE[bind] = (now, missing)
    except TypeError:
        pass
    session.info[key] = missing
    return missing


def compatible_organization_reads(state):
    if not state.is_select:
        return
    from app.models import Organization
    # Do not attach entity-loading options to scalar/count/projection queries.
    descriptions = getattr(state.statement, 'column_descriptions', ())
    if not any(item.get('expr') is Organization for item in descriptions):
        return
    missing = missing_profile_columns(state.session)
    if missing:
        state.statement = state.statement.options(*(
            with_expression(getattr(Organization, name), literal(None))
            for name in sorted(missing)
        ))
