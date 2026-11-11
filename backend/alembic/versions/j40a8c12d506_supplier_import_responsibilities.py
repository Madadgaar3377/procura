"""Preserve employee source responsibilities and allow fresh completed-file imports."""
from alembic import op
import sqlalchemy as sa

revision = 'j40a8c12d506'
down_revision = 'g39d7e5b2c84'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index('uq_organization_verified_cr', table_name='organizations')
    active_cr = sa.text("verified_status = 'verified' AND cr_number IS NOT NULL AND (is_active = true OR is_supplier = false)")
    op.create_index('uq_organization_verified_cr', 'organizations', ['cr_number'], unique=True,
                    postgresql_where=active_cr, sqlite_where=active_cr)
    op.add_column('supplier_contact_capabilities', sa.Column('material_ids', sa.JSON(), nullable=True))
    op.create_table('contact_supplier_trades',
        sa.Column('contact_id', sa.String(36), sa.ForeignKey('organization_contacts.id'), primary_key=True),
        sa.Column('trade_id', sa.String(36), sa.ForeignKey('imported_supplier_trades.id'), primary_key=True))
    op.create_index('ix_contact_supplier_trades_trade_id', 'contact_supplier_trades', ['trade_id'])
    op.drop_index('uq_supplier_bulk_file_admin', table_name='import_batches')
    predicate = sa.text("kind = 'supplier_bulk' AND status IN ('queued','processing')")
    op.create_index('uq_supplier_bulk_file_admin', 'import_batches', ['initiated_by', 'source_sha256'],
                    unique=True, postgresql_where=predicate, sqlite_where=predicate)


def downgrade():
    op.drop_index('uq_organization_verified_cr', table_name='organizations')
    old_cr = sa.text("verified_status = 'verified' AND cr_number IS NOT NULL")
    op.create_index('uq_organization_verified_cr', 'organizations', ['cr_number'], unique=True,
                    postgresql_where=old_cr, sqlite_where=old_cr)
    op.drop_column('supplier_contact_capabilities', 'material_ids')
    # Restoring the old uniqueness rule fails safely if completed retries share
    # a digest. Never delete import history to force a downgrade to succeed.
    op.drop_index('uq_supplier_bulk_file_admin', table_name='import_batches')
    predicate = sa.text("kind = 'supplier_bulk'")
    op.create_index('uq_supplier_bulk_file_admin', 'import_batches', ['initiated_by', 'source_sha256'],
                    unique=True, postgresql_where=predicate, sqlite_where=predicate)
    op.drop_index('ix_contact_supplier_trades_trade_id', table_name='contact_supplier_trades')
    op.drop_table('contact_supplier_trades')
