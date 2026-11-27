"""Read-only matching diagnostic; never creates invitations or notifications."""
from pathlib import Path
import argparse
import sys
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from app.config import get_settings, normalize_database_url
from app.models import Contact, Member, Organization, RFQ, User
from app.modules.matching.service import _match_batch
from app.modules.matching.router import rfq_notifications_status

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('rfq_id')
rfq_id = parser.parse_args().rfq_id
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
    if rfq is None:
        raise SystemExit('RFQ not found')
    start = perf_counter()
    result = _match_batch(db, rfq, evaluate_only=True, bounded=True, limit=10)
    elapsed = perf_counter() - start
    ids = result.get('eligible_ids', [])
    names = dict(db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(ids))).all())
    print('read_only_match_seconds', round(elapsed, 3))
    print('eligible_suppliers', len(ids))
    contacts = dict(db.execute(select(Contact.org_id, func.count()).where(
        Contact.org_id.in_(ids), Contact.permission_to_notify.is_(True),
        Contact.deleted_at.is_(None)).group_by(Contact.org_id)).all()) if ids else {}
    print('first_30', [(org_id, names.get(org_id), result.get('match_levels', {}).get(org_id),
                        'consenting_contacts', contacts.get(org_id, 0)) for org_id in ids[:30]])
    buyer = db.scalar(select(User).join(Member, Member.user_id == User.id).where(
        Member.organization_id == rfq.buyer_org_id,
        Member.role.in_({'owner', 'manager', 'buyer'})).limit(1))
    if buyer:
        print('notification_status', rfq_notifications_status(rfq.id, buyer, db))
    db.rollback()
