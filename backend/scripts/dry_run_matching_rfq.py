"""Read-only matching diagnostic; never creates invitations or notifications."""
from pathlib import Path
import sys
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from app.config import get_settings, normalize_database_url
from app.models import Organization, RFQ
from app.modules.matching.service import _match_batch

rfq_id = '57eeb074-91d4-44da-94fc-89deb94e3527'
engine = create_engine(normalize_database_url(get_settings().database_url),
                       connect_args={'connect_timeout': 5, 'options': '-c statement_timeout=10000'}, hide_parameters=True)
@event.listens_for(engine, 'before_cursor_execute')
def before_query(conn, cursor, statement, parameters, context, executemany):
    context._match_started = perf_counter()

@event.listens_for(engine, 'after_cursor_execute')
def after_query(conn, cursor, statement, parameters, context, executemany):
    elapsed = perf_counter() - context._match_started
    print('query_seconds', round(elapsed, 3), statement.splitlines()[0][:90], flush=True)
with Session(engine, autoflush=False) as db:
    rfq = db.get(RFQ, rfq_id)
    start = perf_counter()
    result = _match_batch(db, rfq, evaluate_only=True, bounded=True, limit=10)
    elapsed = perf_counter() - start
    ids = result.get('eligible_ids', [])
    names = dict(db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(ids))).all())
    print('read_only_match_seconds', round(elapsed, 3))
    print('eligible_suppliers', len(ids))
    print('first_30', [(names.get(org_id), result.get('match_levels', {}).get(org_id)) for org_id in ids[:30]])
    db.rollback()
