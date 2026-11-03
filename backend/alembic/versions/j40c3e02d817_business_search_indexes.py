"""PostgreSQL trigram indexes for the bounded business-list searches."""
from alembic import op

revision='j40c3e02d817'
down_revision='h32b7c91a405'
branch_labels=None
depends_on=None

# pg_trgm is already installed by a47c902d8e31. No data changes are required.
SEARCH_COLUMNS={
    'organizations':['name','city','cr_number'],
    'rfqs':['title','reference'],
    'notifications':['message'],
    'audit_events':['action'],
    'import_batches':['filename'],
    'price_book_items':['brand','model'],
}

def upgrade():
    if op.get_bind().dialect.name!='postgresql':return
    for table,columns in SEARCH_COLUMNS.items():
        for column in columns:
            op.execute(f'CREATE INDEX IF NOT EXISTS ix_{table}_{column}_search ON {table} USING gin ({column} gin_trgm_ops)')

def downgrade():
    if op.get_bind().dialect.name!='postgresql':return
    for table,columns in SEARCH_COLUMNS.items():
        for column in columns:
            op.execute(f'DROP INDEX IF EXISTS ix_{table}_{column}_search')
