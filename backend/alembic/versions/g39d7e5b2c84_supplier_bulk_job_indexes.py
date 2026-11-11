"""Index durable supplier upload identity and queue polling; preserves business data."""
from alembic import op
import sqlalchemy as sa

revision = 'g39d7e5b2c84'
down_revision = 'f28c6d4a1b73'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('uq_supplier_bulk_file_admin', 'import_batches',
        ['initiated_by', 'source_sha256'], unique=True,
        postgresql_where=sa.text("kind = 'supplier_bulk'"), sqlite_where=sa.text("kind = 'supplier_bulk'"))
    op.create_index('ix_import_job_queue', 'import_batches', ['kind', 'status', 'created_at'])
    op.create_index('ix_import_row_job_status', 'import_rows', ['batch_id', 'status', 'created_at'])


def downgrade():
    op.drop_index('ix_import_row_job_status', table_name='import_rows')
    op.drop_index('ix_import_job_queue', table_name='import_batches')
    op.drop_index('uq_supplier_bulk_file_admin', table_name='import_batches')
