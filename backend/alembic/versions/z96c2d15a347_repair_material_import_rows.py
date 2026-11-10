"""Restore the staged import row table when migration history is ahead of schema.

Revision ID: z96c2d15a347
Revises: y85b20d17c3e
"""
from alembic import op
import sqlalchemy as sa

revision = 'z96c2d15a347'
down_revision = 'y85b20d17c3e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('import_rows'):
        op.create_table(
            'import_rows',
            sa.Column('batch_id', sa.String(length=36), nullable=False),
            sa.Column('row_number', sa.Integer(), nullable=False),
            sa.Column('raw', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=30), nullable=False),
            sa.Column('issue', sa.String(length=300), nullable=True),
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        inspector = sa.inspect(bind)

    indexes = {index['name'] for index in inspector.get_indexes('import_rows')}
    if 'ix_import_rows_batch_id' not in indexes:
        op.create_index('ix_import_rows_batch_id', 'import_rows', ['batch_id'], unique=False)


def downgrade() -> None:
    # Keep import history and staged row data if this recovery migration is rolled back.
    pass
