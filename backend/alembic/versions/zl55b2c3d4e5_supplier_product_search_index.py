"""Index imported supplier product wording for RFQ fuzzy candidate discovery.

Revision ID: zl55b2c3d4e5
Revises: zk54a1b2c3d4
"""
from alembic import op
import sqlalchemy as sa

revision = 'zl55b2c3d4e5'
down_revision = 'zk54a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
        op.execute(sa.text(
            'CREATE INDEX IF NOT EXISTS ix_imported_supplier_trades_raw_products_trgm '
            'ON imported_supplier_trades USING gin (raw_products gin_trgm_ops)'
        ))


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute(sa.text('DROP INDEX IF EXISTS ix_imported_supplier_trades_raw_products_trgm'))
