"""Small, durable queue runner for background imports.

Run separately from the API: ``python -m app.material_import_worker``.
"""
import argparse
import logging
import time

from app.database import SessionLocal
from app.import_worker_lock import import_worker_lock
from app.modules.imports.materials import process_queued_material_imports
from app.modules.imports.router import process_queued_supplier_imports
from app.modules.imports.supplier_jobs import process_supplier_jobs
from app.modules.pricebooks.worker import process_pricing_jobs
from app.modules.rfqs.imports import process_rfq_import_jobs
from app.notification_worker import process_batch as process_outbox_batch


def process_once(*, outbox_only: bool = False) -> bool:
    """An unavailable queue must not starve the other queues.

    Every queue executes in its own session that is always rolled back and
    closed on exit so no idle-in-transaction connection can ever leak.
    Outbox events (RFQ matching, invitations, auto-quotations, email/SMS)
    run first so real-time procurement workflows are never starved by bulk imports.
    """
    success = True
    logger = logging.getLogger(__name__)
    queues = (
        (process_outbox_batch,)
        if outbox_only
        else (
            process_outbox_batch,
            process_pricing_jobs,
            process_rfq_import_jobs,
            process_queued_material_imports,
            process_queued_supplier_imports,
            process_supplier_jobs,
        )
    )
    for process in queues:
        db = SessionLocal()
        try:
            started = time.monotonic()
            result = process(db)
            if result:
                logger.info(
                    'Import poll queue=%s progressed elapsed_ms=%.1f',
                    process.__name__,
                    (time.monotonic() - started) * 1000.0,
                )
        except Exception as exc:
            success = False
            try:
                db.rollback()
            except Exception:
                pass
            logger.error('Import poll failed queue=%s type=%s', process.__name__, type(exc).__name__)
        finally:
            try:
                if db.in_transaction():
                    db.rollback()
            except Exception:
                pass
            db.close()
    return success


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process one bounded chunk and exit')
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--outbox-only', action='store_true', help='Only process outbox events (matching, invitations, quotations, notifications)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    with import_worker_lock() as acquired:
        if not acquired:
            logger.error('Another import worker owns the local queue lock; exiting')
            raise SystemExit(75)
        logger.info('Worker started (outbox_only=%s); queues commit independently with short transactions', args.outbox_only)
        last_heartbeat = time.monotonic()
        while True:
            success = process_once(outbox_only=args.outbox_only)
            if args.once:
                if not success:
                    raise SystemExit(1)
                return
            now = time.monotonic()
            if now - last_heartbeat >= 60.0:
                logger.info('Import worker heartbeat status=alive')
                last_heartbeat = now
            time.sleep(max(0.25, args.interval))


if __name__ == '__main__':
    main()
