"""Small, durable queue runner for background imports and isolated outbox lanes.

Run separately from the API:
  python -m app.material_import_worker --lane matching --interval 0.5
  python -m app.material_import_worker --lane quotations --interval 1
  python -m app.material_import_worker --lane events --interval 1
  python -m app.material_import_worker --lane email --interval 2
  python -m app.material_import_worker --lane sms --interval 2
  python -m app.material_import_worker --lane imports --interval 2
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal, get_pool_status
from app.import_worker_lock import import_worker_lock
from app.modules.imports.materials import process_queued_material_imports
from app.modules.imports.router import process_queued_supplier_imports
from app.modules.imports.supplier_jobs import process_supplier_jobs
from app.modules.pricebooks.worker import process_pricing_jobs
from app.modules.rfqs.imports import process_rfq_import_jobs
from app.notification_worker import process_batch as process_outbox_batch


OUTBOX_LANES = ('matching', 'email', 'sms', 'quotations', 'events')


def _pool_has_import_headroom() -> bool:
    """Apply backpressure to bulk imports when the DB connection pool is heavily utilized."""
    status = get_pool_status()
    if 'pool_size' not in status:
        return True
    settings = get_settings()
    capacity = max(1, int(settings.db_pool_size) + int(settings.db_max_overflow))
    if capacity <= 2:
        return True
    checked_out = int(status.get('checked_out', 0))
    return checked_out < max(2, int(capacity * 0.75))


def _run_step(process, logger: logging.Logger) -> tuple[bool, bool]:
    """Execute one queue step in an isolated session. Returns (ok, progressed)."""
    db = SessionLocal()
    try:
        started = time.monotonic()
        result = process(db)
        progressed = bool(result)
        if progressed:
            logger.info(
                'Worker poll queue=%s progressed elapsed_ms=%.1f',
                process.__name__,
                (time.monotonic() - started) * 1000.0,
            )
        return True, progressed
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error('Worker poll failed queue=%s type=%s', process.__name__, type(exc).__name__)
        return False, False
    finally:
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            pass
        db.close()


def _matching_only(db):
    return process_outbox_batch(db, lane='matching')


_matching_only.__name__ = 'outbox_matching'


def _reserved_matching_loop(stop: threading.Event, logger: logging.Logger, idle_seconds: float = 0.5) -> None:
    """Reserved RFQ matching concurrency inside the legacy ``all`` lane process.

    The main loop preempts imports only *between* queue steps; a single step
    (for example parsing a large Excel workbook) can run for minutes. This
    thread polls only ``rfq.published`` jobs so a new RFQ never waits behind
    it. Claims use FOR UPDATE SKIP LOCKED plus a lease, so running alongside
    the main loop or a dedicated ``--lane matching`` service cannot double-run
    a job. It holds at most one pooled connection at a time.
    """
    while not stop.is_set():
        _, progressed = _run_step(_matching_only, logger)
        stop.wait(0.05 if progressed else idle_seconds)


def process_once(*, outbox_only: bool = False, lane: str = 'all', concurrency: int = 1) -> bool:
    """Process one bounded chunk without allowing bulk imports to starve RFQ matching.

    Every queue executes in its own session that is always rolled back and
    closed on exit so no idle-in-transaction connection can ever leak.
    """
    success = True
    logger = logging.getLogger(__name__)
    bounded_workers = max(1, min(int(concurrency or 1), 8))

    if lane in OUTBOX_LANES:
        def process_lane(db, _lane=lane):
            return process_outbox_batch(db, lane=_lane)
        process_lane.__name__ = f'outbox_{lane}'
        if bounded_workers > 1 and lane in {'matching', 'quotations'}:
            with ThreadPoolExecutor(max_workers=bounded_workers) as pool:
                outcomes = list(pool.map(lambda _: _run_step(process_lane, logger), range(bounded_workers)))
            return all(ok for ok, _ in outcomes)
        ok, _ = _run_step(process_lane, logger)
        return ok

    if outbox_only:
        ok, _ = _run_step(process_outbox_batch, logger)
        return ok

    import_queues = (
        process_pricing_jobs,
        process_rfq_import_jobs,
        process_queued_material_imports,
        process_queued_supplier_imports,
        process_supplier_jobs,
    )

    if lane == 'imports':
        if not _pool_has_import_headroom():
            logger.warning('Import worker backpressure: DB pool above 75%% utilization; deferring bulk import poll')
            return True
        for process in import_queues:
            ok, _ = _run_step(process, logger)
            success = success and ok
        return success

    # lane == 'all': drain priority outbox first, and preempt between every import queue
    # so a newly published RFQ never waits behind multi-step Excel imports.
    ok, _ = _run_step(process_outbox_batch, logger)
    success = success and ok
    for process in import_queues:
        _run_step(_matching_only, logger)
        if not _pool_has_import_headroom():
            logger.warning('Import worker backpressure: deferring remaining bulk import steps for live traffic')
            break
        ok, _ = _run_step(process, logger)
        success = success and ok
    _run_step(_matching_only, logger)
    return success


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process one bounded chunk and exit')
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--concurrency', type=int, default=0,
                        help='Bounded parallel workers for matching/quotations lanes (0 = configured default)')
    parser.add_argument('--outbox-only', action='store_true',
                        help='Only process outbox events (matching, invitations, quotations, notifications)')
    parser.add_argument('--lane', default='all', choices=('all', 'imports', *OUTBOX_LANES),
                        help='Run one workload lane. all = legacy single process; '
                             'imports = bulk import queues only; matching/email/sms/quotations/events = one outbox lane')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    settings = get_settings()
    concurrency = args.concurrency or (
        settings.matching_worker_concurrency if args.lane == 'matching'
        else settings.quotation_worker_concurrency if args.lane == 'quotations'
        else 1
    )
    lock_path = (
        Path(__file__).resolve().parents[1] / 'data' / 'worker-outbox.lock'
        if args.outbox_only and args.lane == 'all'
        else None if args.lane in {'all', 'imports'}
        else Path(__file__).resolve().parents[1] / 'data' / f'worker-{args.lane}.lock'
    )
    with import_worker_lock(lock_path) as acquired:
        if not acquired:
            logger.error('Another worker owns the %s lane lock; exiting', args.lane)
            raise SystemExit(75)
        logger.info(
            'Worker started lane=%s concurrency=%s (outbox_only=%s); queues commit independently with short transactions',
            args.lane, concurrency, args.outbox_only,
        )
        last_heartbeat = time.monotonic()
        stop = threading.Event()
        if args.lane == 'all' and not args.once and settings.matching_reserved_worker:
            threading.Thread(
                target=_reserved_matching_loop, args=(stop, logger),
                name='reserved-rfq-matching', daemon=True,
            ).start()
            logger.info('Reserved RFQ matching thread started in lane=all')
        try:
            while True:
                success = process_once(outbox_only=args.outbox_only, lane=args.lane, concurrency=concurrency)
                if args.once:
                    if not success:
                        raise SystemExit(1)
                    return
                now = time.monotonic()
                if now - last_heartbeat >= 60.0:
                    logger.info('Worker heartbeat lane=%s status=alive', args.lane)
                    last_heartbeat = now
                time.sleep(max(0.2, args.interval))
        finally:
            stop.set()


if __name__ == '__main__':
    main()
