"""Durable destination opt-outs and bounce/complaint suppression."""
from alembic import op
import sqlalchemy as sa

revision = 'za07d326b458'
down_revision = 'z96c2d15a347'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('notification_delivery_restrictions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('channel', sa.String(10), nullable=False),
        sa.Column('destination', sa.String(220), nullable=False),
        sa.Column('reason', sa.String(30), nullable=False),
        sa.Column('detail', sa.String(500)),
        sa.UniqueConstraint('channel', 'destination', name='uq_delivery_restriction'))


def downgrade():
    op.drop_table('notification_delivery_restrictions')
