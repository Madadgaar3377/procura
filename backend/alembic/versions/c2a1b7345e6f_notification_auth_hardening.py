"""notification delivery, refresh families and matching indexes

Revision ID: c2a1b7345e6f
Revises: d7e9ea755899
Create Date: 2026-10-20

This revision is additive. It preserves existing records and backfills one
refresh-token family id per existing refresh session.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2a1b7345e6f'
down_revision: Union[str, Sequence[str], None] = 'd7e9ea755899'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('sms_notifications_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('email_notifications_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(
        'uq_users_email_lower',
        'users',
        [sa.text('lower(email)')],
        unique=True,
        sqlite_where=sa.text('email IS NOT NULL'),
        postgresql_where=sa.text('email IS NOT NULL'),
    )

    op.add_column('refresh_sessions', sa.Column('family_id', sa.String(length=36), nullable=True))
    op.execute(sa.text('UPDATE refresh_sessions SET family_id = id WHERE family_id IS NULL'))
    op.create_index(op.f('ix_refresh_sessions_family_id'), 'refresh_sessions', ['family_id'], unique=False)

    op.create_table(
        'email_otps',
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('purpose', sa.String(length=24), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_email_otps_email'), 'email_otps', ['email'], unique=False)

    for name, column in (
        ('dedupe_key', sa.String(length=300)),
        ('next_attempt_at', sa.DateTime(timezone=True)),
        ('lock_until', sa.DateTime(timezone=True)),
        ('last_error', sa.Text()),
        ('provider_message_id', sa.String(length=200)),
        ('delivery_status', sa.String(length=40)),
        ('status_checks', sa.Integer()),
        ('secret_ciphertext', sa.Text()),
    ):
        op.add_column('outbox_events', sa.Column(name, column, nullable=name != 'status_checks', server_default=sa.text('0') if name == 'status_checks' else None))
    op.create_index('uq_outbox_dedupe_key', 'outbox_events', ['dedupe_key'], unique=True)
    op.create_index('ix_outbox_status_next_attempt', 'outbox_events', ['status', 'next_attempt_at'], unique=False)

    with op.batch_alter_table('notifications') as batch:
        batch.add_column(sa.Column('delivery_outbox_id', sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            'fk_notifications_delivery_outbox_id_outbox_events',
            'outbox_events',
            ['delivery_outbox_id'],
            ['id'],
        )
    op.create_index('ix_notifications_delivery_outbox_id', 'notifications', ['delivery_outbox_id'], unique=False)

    op.create_index(
        'uq_organization_verified_cr',
        'organizations',
        ['cr_number'],
        unique=True,
        sqlite_where=sa.text("verified_status = 'verified' AND cr_number IS NOT NULL"),
        postgresql_where=sa.text("verified_status = 'verified' AND cr_number IS NOT NULL"),
    )
    op.create_index(
        'ix_capability_match_area',
        'supplier_capabilities',
        ['category_id', 'city', 'confirmed', 'accepts_rfqs'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_capability_match_area', table_name='supplier_capabilities')
    op.drop_index('uq_organization_verified_cr', table_name='organizations')
    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_index('ix_notifications_delivery_outbox_id', table_name='notifications')
    with op.batch_alter_table('notifications') as batch:
        batch.drop_constraint('fk_notifications_delivery_outbox_id_outbox_events', type_='foreignkey')
        batch.drop_column('delivery_outbox_id')
    op.drop_index('ix_outbox_status_next_attempt', table_name='outbox_events')
    op.drop_index('uq_outbox_dedupe_key', table_name='outbox_events')
    for name in ('secret_ciphertext', 'status_checks', 'delivery_status', 'provider_message_id', 'last_error', 'lock_until', 'next_attempt_at', 'dedupe_key'):
        op.drop_column('outbox_events', name)
    op.drop_index(op.f('ix_email_otps_email'), table_name='email_otps')
    op.drop_table('email_otps')
    op.drop_index(op.f('ix_refresh_sessions_family_id'), table_name='refresh_sessions')
    op.drop_column('refresh_sessions', 'family_id')
    op.drop_column('users', 'email_notifications_enabled')
    op.drop_column('users', 'sms_notifications_enabled')
    op.drop_column('users', 'email_verified')
