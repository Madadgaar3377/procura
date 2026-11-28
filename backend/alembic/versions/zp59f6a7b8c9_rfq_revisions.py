"""Add versioned RFQ revisions and retain removed quotation-linked materials.

Revision ID: zp59f6a7b8c9
Revises: zo58e5f6a7b8
"""
from alembic import op
import sqlalchemy as sa

revision = 'zp59f6a7b8c9'
down_revision = 'zo58e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('rfqs', sa.Column('delivery_country', sa.String(100), nullable=False, server_default=''))
    op.add_column('rfqs', sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('rfqs', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    # SQLite cannot ALTER an existing table to add a foreign-key constraint.
    actor_column = (sa.Column('updated_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True)
                    if op.get_bind().dialect.name == 'postgresql'
                    else sa.Column('updated_by_user_id', sa.String(36), nullable=True))
    op.add_column('rfqs', actor_column)
    op.add_column('rfq_items', sa.Column('discipline', sa.String(50), nullable=False, server_default=''))
    op.add_column('rfq_items', sa.Column('required_brand', sa.String(150), nullable=False, server_default=''))
    op.add_column('rfq_items', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('rfq_items', sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('quotations', sa.Column('rfq_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('quotation_lines', sa.Column('requested_snapshot', sa.JSON(), nullable=True))
    op.create_table(
        'rfq_revisions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('rfq_id', sa.String(36), sa.ForeignKey('rfqs.id'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('before_snapshot', sa.JSON(), nullable=False),
        sa.Column('after_snapshot', sa.JSON(), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=False),
        sa.Column('matching_summary', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint('rfq_id', 'number', name='uq_rfq_revision_number'),
    )
    op.create_index('ix_rfq_revisions_rfq_status', 'rfq_revisions', ['rfq_id', 'status'])
    op.create_index('ix_rfq_revisions_rfq_id', 'rfq_revisions', ['rfq_id'])
    op.create_index('ix_rfq_items_rfq_active', 'rfq_items', ['rfq_id', 'is_active'])
    op.create_table(
        'quotation_revisions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('quotation_id', sa.String(36), sa.ForeignKey('quotations.id'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('rfq_revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('created_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.UniqueConstraint('quotation_id', 'number', name='uq_quotation_revision_number'),
    )
    op.create_index('ix_quotation_revisions_quotation_id', 'quotation_revisions', ['quotation_id'])


def downgrade():
    op.drop_index('ix_quotation_revisions_quotation_id', table_name='quotation_revisions')
    op.drop_table('quotation_revisions')
    op.drop_index('ix_rfq_items_rfq_active', table_name='rfq_items')
    op.drop_index('ix_rfq_revisions_rfq_id', table_name='rfq_revisions')
    op.drop_index('ix_rfq_revisions_rfq_status', table_name='rfq_revisions')
    op.drop_table('rfq_revisions')
    op.drop_column('quotation_lines', 'requested_snapshot')
    op.drop_column('quotations', 'rfq_revision')
    for name in ('removed_at', 'is_active', 'required_brand', 'discipline'):
        op.drop_column('rfq_items', name)
    for name in ('updated_by_user_id', 'updated_at', 'revision', 'delivery_country'):
        op.drop_column('rfqs', name)
