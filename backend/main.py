"""Check the configured PostgreSQL connection without changing database data."""

import re

import psycopg
from sqlalchemy.engine import make_url

from app.config import get_settings, normalize_database_url


def main() -> int:
    database_url = make_url(normalize_database_url(get_settings().database_url))
    if database_url.drivername != "postgresql+psycopg":
        print("Connection check requires a PostgreSQL DATABASE_URL.")
        return 1
    psycopg_url = database_url.set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        with psycopg.connect(
            psycopg_url,
            connect_timeout=6,
            options="-c default_transaction_read_only=on",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                print(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT current_database(), current_schema(), "
                    "to_regclass('rfqs'), to_regclass('alembic_version')"
                )
                database, schema, rfqs_table, revision_table = cursor.fetchone()
                cursor.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
                )
                table_count = cursor.fetchone()[0]
                print(f"Database: {database}")
                print(f"Schema: {schema}")
                print(f"Tables in schema: {table_count}")
                print(f"RFQs table present: {bool(rfqs_table)}")
                if revision_table:
                    cursor.execute("SELECT version_num FROM alembic_version")
                    revisions = ", ".join(row[0] for row in cursor.fetchall()) or "none"
                else:
                    revisions = "no Alembic version table"
                print(f"Alembic revision: {revisions}")
        return 0
    except psycopg.Error as exc:
        # Database exceptions can contain connection details; never print them.
        print(f"Connection failed: {type(exc).__name__}")
        if exc.sqlstate:
            print(f"SQLSTATE: {exc.sqlstate}")
        detail = str(exc).lower()
        for phrase in (
            "permission denied",
            "connection refused",
            "connection timed out",
            "network is unreachable",
            "could not translate host name",
            "password authentication failed",
            "invalid connection option",
            "invalid uri",
            "10013",
        ):
            if phrase in detail:
                if phrase == "permission denied" and not exc.sqlstate:
                    print("Reason: local network permission denied")
                else:
                    print(f"Reason: {phrase}")
                break
        if match := re.search(r"WinError (\d+)", str(exc)):
            print(f"Windows network error: {match.group(1)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
