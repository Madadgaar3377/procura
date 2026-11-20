"""Supplier private price book bulk import schema and performance indexes.

Merges the two existing Alembic heads (ze42c871a903, ze44e093cb25), adds
optional min_order_quantity to price_book_items, and adds composite indexes
supporting fast tenant-scoped price book upserts and chunked import batching.
"""
from alembic import op
import sqlalchemy as sa

revision = 'ze45f104dc36'
down_revision = ('ze42c871a903', 'ze44e093cb25')
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('price_book_items', sa.Column('min_order_quantity', sa.Numeric(14, 3), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute(
            'ALTER TABLE price_book_items '
            'ADD CONSTRAINT ck_private_rate_moq_positive '
            'CHECK (min_order_quantity IS NULL OR min_order_quantity > 0)'
        )
    op.create_index('ix_private_rate_org_material', 'price_book_items', ['org_id', 'material_id', 'price_basis'])
    op.create_index('ix_private_rate_org_status_updated', 'price_book_items', ['org_id', 'active', 'updated_at'])
    op.create_index('ix_import_rows_batch_status_row', 'import_rows', ['batch_id', 'status', 'row_number'])


def downgrade():
    op.drop_index('ix_import_rows_batch_status_row', table_name='import_rows')
    op.drop_index('ix_private_rate_org_status_updated', table_name='price_book_items')
    op.drop_index('ix_private_rate_org_material', table_name='price_book_items')
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('ALTER TABLE price_book_items DROP CONSTRAINT IF EXISTS ck_private_rate_moq_positive')
    op.drop_column('price_book_items', 'min_order_quantity')
