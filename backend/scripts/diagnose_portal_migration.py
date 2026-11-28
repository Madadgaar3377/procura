"""Read-only PostgreSQL lock snapshot for the supplier portal migration.

Run from backend: python scripts/diagnose_portal_migration.py
Prints no connection URL, SQL text, or supplier data.
"""
from pathlib import Path
import sys

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import engine  # noqa: E402


with engine.connect() as connection:
    if connection.dialect.name != 'postgresql':
        raise SystemExit('The configured database is not PostgreSQL')
    connection.execute(text('SET TRANSACTION READ ONLY'))
    revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    print('revision:', revision)
    rows = connection.execute(text("""
        SELECT activity.pid, activity.application_name, activity.state,
               now() - activity.xact_start AS transaction_age,
               activity.wait_event_type, activity.wait_event,
               locks.mode, locks.granted, pg_blocking_pids(activity.pid) AS blocked_by
        FROM pg_stat_activity AS activity
        LEFT JOIN pg_locks AS locks ON locks.pid = activity.pid
             AND locks.relation = 'rfq_invitations'::regclass
        WHERE activity.datname = current_database()
          AND activity.pid <> pg_backend_pid()
          AND (locks.pid IS NOT NULL OR activity.state LIKE 'idle in transaction%')
        ORDER BY activity.xact_start NULLS LAST, activity.pid
    """)).mappings().all()
    for row in rows:
        print(dict(row))
    if not rows:
        print('No current table-lock holders or idle transactions found.')
