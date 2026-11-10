"""Small, durable queue runner for large Material Master imports.

Run separately from the API: ``python -m app.material_import_worker``.
"""
import argparse
import logging
import time

from app.database import SessionLocal
from app.modules.imports.materials import process_queued_material_imports
from app.modules.imports.router import process_queued_supplier_imports


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process one bounded chunk and exit')
    parser.add_argument('--interval', type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    while True:
        with SessionLocal() as db:
            completed = process_queued_material_imports(db)
            supplier_completed = process_queued_supplier_imports(db)
        if completed:
            logger.info('Completed Material Master import batches: %s', ', '.join(completed))
        if supplier_completed:
            logger.info('Completed supplier import batches: %s', ', '.join(supplier_completed))
        if args.once:
            return
        time.sleep(max(0.25, args.interval))


if __name__ == '__main__':
    main()
