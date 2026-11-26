"""Read-only check of the configured PostgreSQL schema for revision zj53f056e441."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text
from app.config import get_settings, normalize_database_url


TABLES = ('supplier_capabilities', 'price_book_items', 'rfq_matches',
          'rfq_invitations', 'quotations', 'outbox_events')


def main():
    engine = create_engine(normalize_database_url(get_settings().database_url),
                           connect_args={'connect_timeout': 5})
    with engine.connect() as connection:
        print('revision:', connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all())
        rows = connection.execute(text('''
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ANY(:tables)
            ORDER BY table_name, ordinal_position
        '''), {'tables': list(TABLES)}).all()
        for table in TABLES:
            print(table, [column for name, column in rows if name == table])
        print('invitations table:', connection.execute(text('''
            SELECT EXISTS (SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_name = 'invitations')
        ''')).scalar_one())


if __name__ == '__main__':
    main()
