"""Read-only RFQ matching and worker queue snapshot. No provider calls or mutations."""
import argparse
import os
import statistics
import time

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal
from app.models import Invitation, Outbox, Quotation, RFQ, RFQMatch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('rfq_id')
    args = parser.parse_args()
    with SessionLocal() as db:
        rfq = db.get(RFQ, args.rfq_id)
        if not rfq:
            print('RFQ not found in configured database')
            return
        print('rfq', {'id': rfq.id, 'reference': rfq.reference, 'status': rfq.status,
                      'revision': int(rfq.revision or 0), 'deadline': str(rfq.response_deadline)})
        for label, model in (('matches', RFQMatch), ('invitations', Invitation), ('quotations', Quotation)):
            rows = db.execute(select(model.status, func.count()).where(model.rfq_id == rfq.id)
                              .group_by(model.status)).all()
            print(label, dict(rows))
        jobs = db.scalars(select(Outbox).where(
            Outbox.event == 'rfq.published', Outbox.payload['rfq_id'].as_string() == rfq.id,
        ).order_by(Outbox.created_at.desc(), Outbox.id.desc())).all()
        for job in jobs:
            payload = job.payload or {}
            print('matching_job', {
                'id': job.id, 'status': job.status, 'revision': payload.get('revision'),
                'attempts': job.attempts, 'created_at': str(job.created_at),
                'next_attempt_at': str(job.next_attempt_at), 'lock_until': str(job.lock_until),
                'progressed_at': payload.get('progressed_at'),
                'evaluated_suppliers': payload.get('evaluated_suppliers'),
                'candidate_query_ms': payload.get('candidate_query_ms'),
                'db_write_ms': payload.get('db_write_ms'),
                'matching_duration_ms': payload.get('matching_duration_ms'),
                'has_cursor': bool(payload.get('cursor')),
                'has_error': bool(job.last_error),
                # Exception class name only (the worker never stores provider text here).
                'error_type': (job.last_error or '')[:80] or None,
            })
        backlog = db.execute(select(Outbox.event, Outbox.status, func.count()).where(
            Outbox.status.in_({'pending', 'retry', 'processing'}),
            Outbox.event.in_({'rfq.published', 'quotation.auto_prepare', 'rfq.invitation', 'notification.delivery'}),
        ).group_by(Outbox.event, Outbox.status)).all()
        print('queue_backlog', {f'{event}:{status}': count for event, status, count in backlog})
        from app.config import get_settings
        settings = get_settings()
        print('matching_config', {
            'env_MATCHING_BATCH_SIZE': os.environ.get('MATCHING_BATCH_SIZE'),
            'effective_page_size': settings.matching_batch_size,
            'worker_concurrency': settings.matching_worker_concurrency,
            'reserved_worker_in_all_lane': settings.matching_reserved_worker,
            'stalled_after_seconds': settings.matching_stalled_after_seconds,
            'db_pool_size': settings.db_pool_size, 'db_max_overflow': settings.db_max_overflow,
        })
        # Every matching page costs ~17 statements; remote latency multiplies it.
        samples = []
        for _ in range(15):
            started = time.perf_counter()
            db.execute(text('SELECT 1'))
            samples.append((time.perf_counter() - started) * 1000.0)
        rtt = statistics.median(samples)
        evaluated = max((int((j.payload or {}).get('evaluated_suppliers') or 0) for j in jobs), default=0)
        pages = max(1, -(-evaluated // max(1, settings.matching_batch_size)))
        print('database_round_trip_ms', {'median': round(rtt, 2), 'max': round(max(samples), 2),
                                         'estimated_pages_for_last_job': pages,
                                         'estimated_round_trip_cost_ms': round(pages * 17 * rtt, 1)})
        try:
            revision = db.execute(text('SELECT version_num FROM alembic_version')).scalar()
            print('alembic_revision', revision)
        except Exception:
            db.rollback()
            print('alembic_revision unavailable')


if __name__ == '__main__':
    try:
        main()
    except OperationalError as exc:
        print('configured_database_unavailable', type(exc.orig).__name__)
        raise SystemExit(2) from None
