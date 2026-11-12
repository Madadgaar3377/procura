"""Invalidate public projections once a relevant database transaction commits.

Session hooks cover API handlers, workers, and import batch commits, including
SQLAlchemy bulk DML. Never invalidate on flush or a rolled-back transaction.
"""
from sqlalchemy import event
from sqlalchemy.orm import Session

from app import catalog_cache
from app.config import get_settings

PUBLIC_TABLES = frozenset({
    'organizations', 'organization_public_consent', 'supplier_capabilities',
    'categories', 'materials', 'measurement_units', 'verifications',
})
_FLAG = 'public_cache_changed'
_OUTBOX = 'public_cache_invalidation_event'


def mark_changed(session):
    session.info[_FLAG] = True
    if _OUTBOX in session.info and session.info[_OUTBOX] not in session:
        # A savepoint rollback may expunge the event while the outer
        # transaction still contains relevant committed-to-be changes.
        session.info.pop(_OUTBOX)
    if get_settings().redis_url and _OUTBOX not in session.info:
        # Persist a retry in the SAME transaction, so a writer crash or Redis
        # outage cannot permanently lose the invalidation. Reuse the outbox.
        from app.models import Outbox
        event_row = Outbox(event='cache.public.invalidate', payload={})
        session.add(event_row)
        session.info[_OUTBOX] = event_row


@event.listens_for(Session, 'before_commit')
def ensure_retry_event(session):
    if session.info.get(_FLAG):
        mark_changed(session)


@event.listens_for(Session, 'before_flush')
def track_changes(session, flush_context, instances):
    for obj in session.new | session.dirty | session.deleted:
        if getattr(obj, '__tablename__', None) in PUBLIC_TABLES:
            mark_changed(session)
            break


@event.listens_for(Session, 'do_orm_execute')
def track_bulk_changes(state):
    if state.is_insert or state.is_update or state.is_delete:
        table = getattr(state.statement, 'table', None)
        if getattr(table, 'name', None) in PUBLIC_TABLES:
            mark_changed(state.session)


@event.listens_for(Session, 'after_commit')
def invalidate_committed(session):
    if not session.in_nested_transaction() and session.info.pop(_FLAG, False):
        session.info.pop(_OUTBOX, None)
        catalog_cache.invalidate()


@event.listens_for(Session, 'after_soft_rollback')
def discard_rollback(session, previous_transaction):
    if previous_transaction.parent is None:
        session.info.pop(_FLAG, None)
        session.info.pop(_OUTBOX, None)
