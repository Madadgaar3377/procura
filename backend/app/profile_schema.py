"""Read compatibility while the additive company-profile migration is pending.

Never migrate a configured database implicitly. Missing optional fields read as
NULL; writes to unavailable fields must fail explicitly rather than lose data.
"""
from sqlalchemy import inspect, literal
from sqlalchemy.orm import with_expression

PROFILE_COLUMNS = frozenset({
    'trading_name', 'company_type', 'description', 'contact_name', 'designation',
    'business_email', 'business_phone', 'country', 'region',
})


def missing_profile_columns(session):
    key = 'procura.missing_profile_columns'
    if key not in session.info:
        columns = {column['name'] for column in inspect(session.connection()).get_columns('organizations')}
        session.info[key] = PROFILE_COLUMNS - columns
    return session.info[key]


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
