"""matching_indexes_and_auto_approval

Revision ID: ze41b760f892
Revises: zd30a659e781
Create Date: 2026-11-17 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'ze41b760f892'
down_revision = 'zd30a659e781'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('approval_status', sa.String(length=30), server_default='approved', nullable=False))
    op.add_column('users', sa.Column('pending_approval_since', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('approval_method', sa.String(length=30), nullable=True))
    op.add_column('users', sa.Column('approval_reason', sa.String(length=100), nullable=True))
    op.add_column('users', sa.Column('manual_review_required', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('users', sa.Column('manual_review_reason', sa.String(length=300), nullable=True))
    op.create_index('ix_users_approval_status', 'users', ['approval_status'])
    op.create_index('ix_users_pending_approval_since', 'users', ['pending_approval_since'])

    op.add_column('organizations', sa.Column('pending_approval_since', sa.DateTime(timezone=True), nullable=True))
    op.add_column('organizations', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('organizations', sa.Column('approval_method', sa.String(length=30), nullable=True))
    op.add_column('organizations', sa.Column('manual_review_required', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('organizations', sa.Column('manual_review_reason', sa.String(length=300), nullable=True))
    op.create_index('ix_organizations_supplier_active_verified', 'organizations', ['is_supplier', 'is_active', 'verified_status'])
    op.create_index('ix_rfq_matches_rfq_status', 'rfq_matches', ['rfq_id', 'status'])

    op.create_table(
        'platform_settings',
        sa.Column('id', sa.String(length=20), nullable=False),
        sa.Column('auto_approval_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('auto_approval_hours', sa.Integer(), server_default='24', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('platform_settings')
    op.drop_index('ix_rfq_matches_rfq_status', table_name='rfq_matches')
    op.drop_index('ix_organizations_supplier_active_verified', table_name='organizations')
    op.drop_column('organizations', 'manual_review_reason')
    op.drop_column('organizations', 'manual_review_required')
    op.drop_column('organizations', 'approval_method')
    op.drop_column('organizations', 'approved_at')
    op.drop_column('organizations', 'pending_approval_since')

    op.drop_index('ix_users_pending_approval_since', table_name='users')
    op.drop_index('ix_users_approval_status', table_name='users')
    op.drop_column('users', 'manual_review_reason')
    op.drop_column('users', 'manual_review_required')
    op.drop_column('users', 'approval_reason')
    op.drop_column('users', 'approval_method')
    op.drop_column('users', 'approved_at')
    op.drop_column('users', 'pending_approval_since')
    op.drop_column('users', 'approval_status')
