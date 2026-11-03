"""Guarded, non-destructive upgrade to the current reviewed Alembic head.

Run from backend: python scripts/upgrade_database.py
Refuses missing/unknown histories, preserves existing table row counts, and rolls
back on lock timeout, revision mismatch or unexpected count changes.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine,inspect,text,table,select,func
from sqlalchemy.exc import SQLAlchemyError
from app.config import get_settings,normalize_database_url

ROOT=Path(__file__).resolve().parents[1]
TARGET='f8c12a4d9b60'

def upgrade(connection):
    config=Config(str(ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ROOT/'alembic'))
    scripts=ScriptDirectory.from_config(config)
    known={r.revision for r in scripts.walk_revisions()}
    inspector=inspect(connection)
    if not inspector.has_table('alembic_version'):
        raise RuntimeError('No migration history found. Refusing an unknown database.')
    revisions=list(connection.scalars(text('SELECT version_num FROM alembic_version')))
    if len(revisions)!=1 or revisions[0] not in known:
        raise RuntimeError('Unknown or multiple database revisions. No upgrade was applied.')
    if revisions==[TARGET]:
        print('Database is already at the target revision.')
        return
    # All migrations run inside the supplied transaction. No reset or stamp.
    names=[name for name in inspector.get_table_names() if name!='alembic_version']
    def count(name):
        return connection.scalar(select(func.count()).select_from(table(name)))
    before={name:count(name) for name in names}
    config.attributes['connection']=connection
    command.upgrade(config,TARGET)
    if any(count(name)!=total for name,total in before.items()):
        raise RuntimeError('An existing table row count changed unexpectedly. Upgrade rolled back.')
    if list(connection.scalars(text('SELECT version_num FROM alembic_version')))!=[TARGET]:
        raise RuntimeError('Migration did not reach the expected target. Upgrade rolled back.')
    print('Database upgraded to '+TARGET+'; existing record counts preserved.')

def main():
    settings=get_settings()
    url=normalize_database_url(settings.database_url)
    if not url.startswith('postgresql'):
        print('This command requires the configured PostgreSQL database.')
        return 1
    engine=create_engine(url,connect_args={'connect_timeout':6,'options':'-c lock_timeout=5000 -c statement_timeout=60000'})
    try:
        with engine.connect().execution_options(isolation_level='SERIALIZABLE') as connection:
            with connection.begin():
                if not connection.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext('procura-database-upgrade'))")):
                    raise RuntimeError('Another database upgrade is active. Try again later.')
                upgrade(connection)
        return 0
    except (SQLAlchemyError,RuntimeError) as error:
        print('Database upgrade was not applied: '+type(error).__name__)
        if isinstance(error,RuntimeError):print(str(error))
        else:print('Database connection, lock or migration failed; any open transaction was rolled back.')
        return 1
    finally:engine.dispose()

if __name__=='__main__':raise SystemExit(main())
