"""EXPLAIN (ANALYZE, BUFFERS) for every SQL statement the matcher runs for one RFQ page.

Read-only: the transaction is SET READ ONLY and rolled back, and matching runs in
evaluate-only mode (no matches, invitations or messages are written). The real
statements are captured with their parameters, then each SELECT is explained.

Usage (PostgreSQL only):
  python scripts/explain_matching_queries.py RFQ-C9B00690ACA7 [--page-size 100] [--top 8]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event, select, text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RFQ  # noqa: E402
from app.modules.matching.service import _match_batch  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('rfq')
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--top', type=int, default=8, help='Show the N slowest statements')
    args = parser.parse_args()
    with SessionLocal() as db:
        if db.bind.dialect.name != 'postgresql':
            raise SystemExit('PostgreSQL only')
        db.execute(text('SET TRANSACTION READ ONLY'))
        rfq = db.get(RFQ, args.rfq) or db.scalar(select(RFQ).where(RFQ.reference == args.rfq))
        if not rfq:
            raise SystemExit(f'{args.rfq}: not found')
        captured = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith(('SELECT', 'WITH')):
                captured.append((statement, parameters))
        event.listen(db.bind, 'before_cursor_execute', capture)
        started = time.monotonic()
        result = _match_batch(db, rfq, evaluate_only=True, limit=args.page_size, bounded=True)
        elapsed = time.monotonic() - started
        event.remove(db.bind, 'before_cursor_execute', capture)
        print(f'{rfq.reference}: one page of {args.page_size} candidates evaluated in {elapsed * 1000:.0f} ms '
              f'with {len(captured)} SELECT statements; final eligible on page={len(result.get("eligible_ids", []))}')
        timings = []
        raw = db.connection().connection.driver_connection
        for statement, parameters in captured:
            with raw.cursor() as cursor:
                cursor.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) ' + statement, parameters)
                plan = [row[0] for row in cursor.fetchall()]
            total = next((float(line.split('Execution Time:')[1].split('ms')[0]) for line in plan
                          if 'Execution Time:' in line), 0.0)
            timings.append((total, statement, plan))
        for total, statement, plan in sorted(timings, key=lambda row: -row[0])[:args.top]:
            print('\n' + '=' * 100)
            print(f'{total:.2f} ms :: {" ".join(statement.split())[:300]}')
            print('\n'.join(plan))
        db.rollback()


if __name__ == '__main__':
    main()
