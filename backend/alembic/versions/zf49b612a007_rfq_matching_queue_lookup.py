"""Index durable RFQ matching job lookup without changing existing rows.

The supplier capability, match, invitation and contact assignment indexes already
exist in earlier revisions. This index serves results/rerun status lookup.
"""
from alembic import op
import sqlalchemy as sa

revision = 'zf49b612a007'
down_revision = 'ze48c437gf69'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.create_index(
            'ix_outbox_rfq_matching_status', 'outbox_events',
            ['event', sa.text("(payload ->> 'rfq_id')"), 'status'],
            postgresql_where=sa.text("event = 'rfq.published'"),
            if_not_exists=True,
        )


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_index('ix_outbox_rfq_matching_status', table_name='outbox_events', if_exists=True)
