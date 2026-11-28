"""Add RFQ matching revision tracking and high-selectivity PostgreSQL indexes.

Revision ID: zq60a7b8c9d0
Revises: zp59f6a7b8c9
"""
from alembic import op
import sqlalchemy as sa

revision = 'zq60a7b8c9d0'
down_revision = 'zp59f6a7b8c9'
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    return any(col['name'] == column for col in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'
    if is_pg:
        op.execute("SET LOCAL lock_timeout = '5s'")

    if not _has_column(bind, 'rfq_matches', 'rfq_revision'):
        op.add_column('rfq_matches', sa.Column('rfq_revision', sa.Integer(), nullable=False, server_default='0'))
    if not _has_column(bind, 'rfq_invitations', 'rfq_revision'):
        op.add_column('rfq_invitations', sa.Column('rfq_revision', sa.Integer(), nullable=False, server_default='0'))

    if is_pg:
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_rfq_matches_rfq_rev_supplier '
            'ON rfq_matches (rfq_id, rfq_revision, supplier_org_id)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_outbox_lane_pending_created '
            "ON outbox_events (event, created_at, id) "
            "WHERE status IN ('pending', 'retry', 'awaiting_status', 'processing')"
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_outbox_delivery_channel_pending '
            "ON outbox_events ((payload->>'channel'), created_at, id) "
            "WHERE event = 'notification.delivery' AND status IN ('pending', 'retry', 'awaiting_status', 'processing')"
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_outbox_delivery_dest_notice '
            "ON outbox_events ((payload->>'channel'), (payload->>'destination'), (payload->'context'->>'notice_key')) "
            "WHERE event = 'notification.delivery' AND provider_message_id IS NOT NULL"
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_imported_trades_mapped_org '
            "ON imported_supplier_trades (org_id) WHERE mapping_status <> 'unreviewed'"
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_capability_brands_gin '
            'ON supplier_capabilities USING gin ((brands::jsonb))'
        )
    else:
        op.create_index('ix_rfq_matches_rfq_rev_supplier', 'rfq_matches', ['rfq_id', 'rfq_revision', 'supplier_org_id'])


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("SET LOCAL lock_timeout = '5s'")
        for name in (
            'ix_capability_brands_gin',
            'ix_imported_trades_mapped_org',
            'ix_outbox_delivery_dest_notice',
            'ix_outbox_delivery_channel_pending',
            'ix_outbox_lane_pending_created',
            'ix_rfq_matches_rfq_rev_supplier',
        ):
            op.execute(f'DROP INDEX IF EXISTS {name}')
    else:
        op.drop_index('ix_rfq_matches_rfq_rev_supplier', table_name='rfq_matches')

    if _has_column(bind, 'rfq_invitations', 'rfq_revision'):
        op.drop_column('rfq_invitations', 'rfq_revision')
    if _has_column(bind, 'rfq_matches', 'rfq_revision'):
        op.drop_column('rfq_matches', 'rfq_revision')
