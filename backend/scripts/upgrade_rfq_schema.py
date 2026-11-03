"""Apply only the tested RFQ snapshot migration after verifying the existing head.

Run from backend: python scripts/upgrade_rfq_schema.py
Never creates, stamps, resets, or downgrades a database.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from app.config import get_settings, normalize_database_url

PREVIOUS='b19c4e7a1d62'
TARGET='a61d9f3208bc'


def upgrade(connection):
    inspector=inspect(connection)
    if not inspector.has_table('alembic_version'):
        raise RuntimeError('No migration history found. Refusing to change an unknown database.')
    revisions=list(connection.scalars(text('SELECT version_num FROM alembic_version')))
    if revisions not in ([PREVIOUS],[TARGET]):
        raise RuntimeError('Unexpected migration head. Review migration history before making any changes.')
    columns={c['name'] for c in inspector.get_columns('rfq_items')}
    if revisions==[TARGET]:
        rfq_columns={c['name'] for c in inspector.get_columns('rfqs')}
        if not {'main_category_id','subcategory_id'}.issubset(columns) or 'required_qualifications' not in rfq_columns:
            raise RuntimeError('Migration history and schema disagree. No changes were made.')
        print('RFQ schema is already up to date. No changes made.')
        return
    if {'main_category_id','subcategory_id'}&columns:
        raise RuntimeError('Partially changed RFQ schema detected. Refusing to guess or stamp a revision.')
    tracked=['rfq_items','rfqs','quotation_lines','materials','categories']
    counts={name:connection.scalar(text(f'SELECT COUNT(*) FROM {name}')) for name in tracked}
    root=Path(__file__).resolve().parents[1]
    config=Config(str(root/'alembic.ini'))
    config.set_main_option('script_location',str(root/'alembic'))
    config.attributes['connection']=connection
    command.upgrade(config,TARGET)
    for name,total in counts.items():
        if connection.scalar(text(f'SELECT COUNT(*) FROM {name}'))!=total:
            raise RuntimeError('Record counts changed unexpectedly. Rolling back the migration.')
    if connection.scalar(text('SELECT COUNT(*) FROM rfq_items WHERE material_id IS NOT NULL AND main_category_id IS NULL')):
        raise RuntimeError('RFQ category backfill did not complete. Rolling back the migration.')
    print('RFQ migration applied. RFQs, quotation lines, materials and category record counts preserved.')


def main():
    url=normalize_database_url(get_settings().database_url)
    if not url.startswith('postgresql'):
        print('This recovery command requires the configured PostgreSQL database.')
        return 1
    engine=create_engine(url,connect_args={'connect_timeout':6,'options':'-c lock_timeout=10000 -c statement_timeout=60000'})
    try:
        with engine.connect().execution_options(isolation_level='SERIALIZABLE') as connection:
            with connection.begin():
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('procura-rfq-schema-upgrade'))"))
                upgrade(connection)
        return 0
    except (SQLAlchemyError,RuntimeError) as error:
        # Do not print connection strings, credentials, SQL parameters or private rows.
        print(f'RFQ migration was not applied: {type(error).__name__}.')
        if isinstance(error,RuntimeError):print(str(error))
        else:print('Check database connectivity and permissions from the backend host. Any open migration transaction was rolled back.')
        return 1
    finally:
        engine.dispose()


if __name__=='__main__':raise SystemExit(main())
