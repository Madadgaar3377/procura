"""Add PostgreSQL trigram and composite indexes for hybrid classification and supplier matching."""
from alembic import op
import sqlalchemy as sa

revision = 'ze47b326fe58'
down_revision = 'ze46a215ed47'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    op.create_index(
        'ix_rfq_items_rfq_category_group',
        'rfq_items',
        ['rfq_id', 'main_category_id', 'subcategory_id'],
        if_not_exists=True,
    )
    op.create_index(
        'ix_price_book_items_org_active_material',
        'price_book_items',
        ['org_id', 'active', 'material_id'],
        if_not_exists=True,
    )
    op.create_index(
        'ix_price_book_items_org_active_category',
        'price_book_items',
        ['org_id', 'active', 'main_category_id', 'subcategory_id'],
        if_not_exists=True,
    )
    op.create_index(
        'ix_imported_supplier_trades_org',
        'imported_supplier_trades',
        ['org_id'],
        if_not_exists=True,
    )

    if bind.dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_capability_trade_keywords_trgm '
            'ON supplier_capabilities USING gin (trade_keywords gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_price_book_description_trgm '
            'ON price_book_items USING gin (supplier_description gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_imported_trades_raw_trade_trgm '
            'ON imported_supplier_trades USING gin (raw_trade gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_materials_normalized_desc_trgm '
            'ON materials USING gin (normalized_description gin_trgm_ops)'
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS ix_materials_normalized_desc_trgm')
        op.execute('DROP INDEX IF EXISTS ix_imported_trades_raw_trade_trgm')
        op.execute('DROP INDEX IF EXISTS ix_price_book_description_trgm')
        op.execute('DROP INDEX IF EXISTS ix_capability_trade_keywords_trgm')

    op.drop_index('ix_imported_supplier_trades_org', table_name='imported_supplier_trades', if_exists=True)
    op.drop_index('ix_price_book_items_org_active_category', table_name='price_book_items', if_exists=True)
    op.drop_index('ix_price_book_items_org_active_material', table_name='price_book_items', if_exists=True)
    op.drop_index('ix_rfq_items_rfq_category_group', table_name='rfq_items', if_exists=True)
