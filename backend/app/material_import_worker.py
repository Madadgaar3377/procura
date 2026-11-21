"""Small, durable queue runner for large Material Master imports.

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


def process_once() -> bool:
    """An unavailable queue must not starve the other import queues."""
    success = True
    for process in (process_queued_material_imports, process_queued_supplier_imports,
                    process_supplier_jobs, process_pricing_jobs, process_rfq_import_jobs):
        try:
            with SessionLocal() as db:
                result = process(db)
            if result:
                logging.getLogger(__name__).info('Import poll queue=%s progressed', process.__name__)
        except Exception as exc:
            success = False
            logging.getLogger(__name__).error('Import poll failed queue=%s type=%s', process.__name__, type(exc).__name__)
    return success


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process one bounded chunk and exit')
    parser.add_argument('--interval', type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    with import_worker_lock() as acquired:
        if not acquired:
            logger.error('Another import worker owns the local queue lock; exiting')
            raise SystemExit(75)
        logger.info('Import worker started; material jobs use 30-row batches and at most 300 rows per poll')
        while True:
            success = process_once()
            if args.once:
                if not success:
                    raise SystemExit(1)
                return
            time.sleep(max(0.25, args.interval))


if __name__ == '__main__':
    main()
