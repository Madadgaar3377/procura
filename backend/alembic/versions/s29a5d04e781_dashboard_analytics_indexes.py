"""Index dashboard time windows and recent-record lookups without data changes."""
from alembic import op

revision = 's29a5d04e781'
down_revision = 'r18f4a932c70'
branch_labels = None
depends_on = None

INDEXES = [
    ('ix_dashboard_org_created', 'organizations', ['created_at']),
    ('ix_dashboard_rfq_created', 'rfqs', ['created_at']),
    ('ix_dashboard_rfq_published', 'rfqs', ['published_at']),
    ('ix_dashboard_quote_submitted', 'quotations', ['submitted_at']),
    ('ix_dashboard_verification_reviewed', 'verifications', ['reviewed_at']),
    ('ix_dashboard_verification_org_status', 'verifications', ['org_id', 'status', 'reviewed_at']),
    ('ix_dashboard_import_created', 'import_batches', ['created_at']),
    ('ix_dashboard_outbox_event_status', 'outbox_events', ['event', 'status']),
]


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            for name, table, columns in INDEXES:
                op.create_index(name, table, columns, postgresql_concurrently=True, if_not_exists=True)
    else:
        for name, table, columns in INDEXES:
            op.create_index(name, table, columns)


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            for name, table, _ in reversed(INDEXES):
                op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
    else:
        for name, table, _ in reversed(INDEXES):
            op.drop_index(name, table_name=table)
