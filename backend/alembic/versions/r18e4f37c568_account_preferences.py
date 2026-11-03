"""Persistent account notification preferences; additive only."""
import sqlalchemy as sa

from alembic import op

revision = 'r18e4f37c568'
down_revision = 'q07d3e26b457'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('account_preferences',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('events', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('in_app_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))

def downgrade():
    op.drop_table('account_preferences')
