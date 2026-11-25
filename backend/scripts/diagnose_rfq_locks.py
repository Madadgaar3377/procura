"""Read-only RFQ queue and PostgreSQL blocker diagnostics; prints no contact data."""
import argparse
from sqlalchemy import text
from app.database import engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('rfq_id')
    args = parser.parse_args()
    with engine.connect() as conn:
        if conn.dialect.name != 'postgresql':
            raise SystemExit('PostgreSQL is required')
        rfq = conn.execute(text('SELECT id, status FROM rfqs WHERE id = :id'), {'id': args.rfq_id}).mappings().first()
        print('rfq:', dict(rfq) if rfq else 'missing')
        jobs = conn.execute(text("""
            SELECT id, status, attempts, created_at, lock_until,
                   payload->>'cursor' AS cursor, last_error
            FROM outbox_events
            WHERE event = 'rfq.published' AND payload->>'rfq_id' = :id
            ORDER BY created_at DESC LIMIT 10
        """), {'id': args.rfq_id}).mappings().all()
        for job in jobs:
            print('job:', dict(job))
        blockers = conn.execute(text("""
            SELECT blocked.pid AS blocked_pid, blocked.state AS blocked_state,
                   blocked.wait_event_type, blocked.wait_event,
                   blocker.pid AS blocker_pid, blocker.state AS blocker_state,
                   now()-blocker.xact_start AS blocker_transaction_age,
                   blocker.wait_event_type AS blocker_wait_type
            FROM pg_stat_activity AS blocked
            CROSS JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS blocker_pid
            JOIN pg_stat_activity AS blocker ON blocker.pid = blocker_pid
            WHERE blocked.datname = current_database()
            ORDER BY blocker.xact_start NULLS LAST
        """)).mappings().all()
        for blocker in blockers:
            print('blocker:', dict(blocker))


if __name__ == '__main__':
    main()
