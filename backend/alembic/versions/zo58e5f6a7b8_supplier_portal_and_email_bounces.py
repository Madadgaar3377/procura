"""Supplier portal sessions, email bounce tracking and provider send accounting.

Additive only: new nullable columns, two new tables and their indexes, one outbox index. No existing
row is changed or deleted; existing suppressions keep blocking delivery (cleared_at
is NULL). Bounce correlation reuses ix_outbox_provider_message_id from zk54a1b2c3d4.
Apply BEFORE starting the new API and worker code, which reads these columns.

Revision ID: zo58e5f6a7b8
Revises: zn57d4e5f6a7
"""
from alembic import op
import sqlalchemy as sa

revision = 'zo58e5f6a7b8'
down_revision = 'zn57d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    # Alembic uses its own connection, so the app engine's lock timeout does not apply.
    # Fail promptly rather than queue an ACCESS EXCLUSIVE table lock indefinitely.
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("SET LOCAL lock_timeout = '5s'")

    op.add_column('rfq_invitations', sa.Column('match_evidence', sa.JSON(), nullable=True))

    op.add_column('outbox_events', sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_outbox_event_submitted_at', 'outbox_events', ['event', 'submitted_at'])

    op.add_column('notification_delivery_restrictions',
                  sa.Column('bounce_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('notification_delivery_restrictions',
                  sa.Column('last_event_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('notification_delivery_restrictions',
                  sa.Column('cleared_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('notification_delivery_restrictions',
                  sa.Column('cleared_by_user_id', sa.String(36), nullable=True))

    op.create_table(
        'email_delivery_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('external_id', sa.String(128), nullable=False),
        sa.Column('outbox_id', sa.String(36), sa.ForeignKey('outbox_events.id', ondelete='SET NULL'), nullable=True),
        sa.Column('provider_message_id', sa.String(200), nullable=True),
        sa.Column('event_type', sa.String(30), nullable=False),
        sa.Column('destination_hash', sa.String(64), nullable=True),
        sa.Column('status_code', sa.String(20), nullable=True),
        sa.Column('diagnostic', sa.String(500), nullable=True),
        sa.Column('applied', sa.String(40), nullable=True),
        sa.UniqueConstraint('source', 'external_id', name='uq_email_delivery_event_source'),
    )
    op.create_index('ix_email_delivery_events_outbox', 'email_delivery_events', ['outbox_id'])
    op.create_index('ix_email_delivery_events_destination', 'email_delivery_events', ['destination_hash'])

    op.create_table(
        'supplier_portal_sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('access_token_id', sa.String(36),
                  sa.ForeignKey('supplier_access_tokens.id', ondelete='CASCADE'), nullable=False),
        sa.Column('invitation_id', sa.String(36), sa.ForeignKey('rfq_invitations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rfq_id', sa.String(36), sa.ForeignKey('rfqs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('supplier_org_id', sa.String(36), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verification_code_hash', sa.String(64), nullable=True),
        sa.Column('verification_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verification_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('verification_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_supplier_portal_sessions_token_hash', 'supplier_portal_sessions', ['token_hash'], unique=True)
    op.create_index('ix_supplier_portal_sessions_access_token_id', 'supplier_portal_sessions', ['access_token_id'])
    op.create_index('ix_supplier_portal_sessions_invitation', 'supplier_portal_sessions', ['invitation_id', 'revoked_at'])


def downgrade():
    op.drop_index('ix_supplier_portal_sessions_invitation', table_name='supplier_portal_sessions')
    op.drop_index('ix_supplier_portal_sessions_access_token_id', table_name='supplier_portal_sessions')
    op.drop_index('ix_supplier_portal_sessions_token_hash', table_name='supplier_portal_sessions')
    op.drop_table('supplier_portal_sessions')
    op.drop_index('ix_email_delivery_events_destination', table_name='email_delivery_events')
    op.drop_index('ix_email_delivery_events_outbox', table_name='email_delivery_events')
    op.drop_table('email_delivery_events')
    op.drop_column('notification_delivery_restrictions', 'cleared_by_user_id')
    op.drop_column('notification_delivery_restrictions', 'cleared_at')
    op.drop_column('notification_delivery_restrictions', 'last_event_at')
    op.drop_column('notification_delivery_restrictions', 'bounce_count')
    op.drop_index('ix_outbox_event_submitted_at', table_name='outbox_events')
    op.drop_column('outbox_events', 'submitted_at')
    op.drop_column('rfq_invitations', 'match_evidence')
