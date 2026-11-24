"""Add brands column to supplier_capabilities and supporting indexes for supplier import and matching."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'ze48c437gf69'
down_revision = 'ze47b326fe58'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    cap_cols = {col['name'] for col in inspector.get_columns('supplier_capabilities')}
    if 'brands' not in cap_cols:
        op.add_column(
            'supplier_capabilities',
            sa.Column('brands', sa.JSON(), nullable=False, server_default='[]'),
        )

    op.create_index(
        'ix_organizations_supplier_name_normalized',
        'organizations',
        ['name_normalized'],
        if_not_exists=True,
    )
    op.create_index(
        'ix_organizations_vat_number',
        'organizations',
        ['vat_number'],
        if_not_exists=True,
    )
    op.create_index(
        'ix_imported_supplier_trades_org_status',
        'imported_supplier_trades',
        ['org_id', 'mapping_status'],
        if_not_exists=True,
    )


def downgrade():
    op.drop_index('ix_imported_supplier_trades_org_status', table_name='imported_supplier_trades', if_exists=True)
    op.drop_index('ix_organizations_vat_number', table_name='organizations', if_exists=True)
    op.drop_index('ix_organizations_supplier_name_normalized', table_name='organizations', if_exists=True)

    bind = op.get_bind()
    inspector = inspect(bind)
    cap_cols = {col['name'] for col in inspector.get_columns('supplier_capabilities')}
    if 'brands' in cap_cols:
        op.drop_column('supplier_capabilities', 'brands')
