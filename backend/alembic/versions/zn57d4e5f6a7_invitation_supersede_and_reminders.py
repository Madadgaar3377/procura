"""Invitation supersede and manual reminder tracking on rfq_invitations.

Additive and nullable (reminder_count defaults to 0): existing invitations, matches
and quotations are untouched. Apply this migration BEFORE starting the new API and
worker code, which reads these columns.

Revision ID: zn57d4e5f6a7
Revises: zm56c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = 'zn57d4e5f6a7'
down_revision = 'zm56c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('rfq_invitations', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('rfq_invitations', sa.Column('cancellation_reason', sa.String(200), nullable=True))
    op.add_column('rfq_invitations', sa.Column('last_reminded_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('rfq_invitations', sa.Column('reminder_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('rfq_invitations', 'reminder_count')
    op.drop_column('rfq_invitations', 'last_reminded_at')
    op.drop_column('rfq_invitations', 'cancellation_reason')
    op.drop_column('rfq_invitations', 'cancelled_at')
