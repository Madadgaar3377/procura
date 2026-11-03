"""Preserve RFQ category selection independently of Material Master.

Revision ID: a61d9f3208bc
Revises: b19c4e7a1d62
"""
from alembic import op
import sqlalchemy as sa

revision = 'a61d9f3208bc'
down_revision = 'b19c4e7a1d62'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('rfqs', sa.Column('required_qualifications', sa.JSON(), nullable=False, server_default='[]'))
    with op.batch_alter_table('rfq_items') as batch:
        batch.add_column(sa.Column('main_category_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('subcategory_id', sa.String(36), nullable=True))
        batch.alter_column('material_id', existing_type=sa.String(36), nullable=True)
        batch.create_foreign_key('fk_rfq_items_main_category', 'categories', ['main_category_id'], ['id'])
        batch.create_foreign_key('fk_rfq_items_subcategory', 'categories', ['subcategory_id'], ['id'])
        batch.create_index('ix_rfq_items_main_category_id', ['main_category_id'])
        batch.create_index('ix_rfq_items_subcategory_id', ['subcategory_id'])
    # Backfill only the new references; descriptions, IDs and quantities stay intact.
    op.execute(sa.text('''UPDATE rfq_items SET
        main_category_id=(SELECT COALESCE(m.main_category_id, c.parent_id, m.category_id)
                          FROM materials m JOIN categories c ON c.id=m.category_id WHERE m.id=rfq_items.material_id),
        subcategory_id=(SELECT COALESCE(m.subcategory_id, CASE WHEN c.parent_id IS NOT NULL THEN c.id END)
                        FROM materials m JOIN categories c ON c.id=m.category_id WHERE m.id=rfq_items.material_id)'''))


def downgrade():
    # Free-text RFQ items cannot be converted back without losing their requests.
    bind = op.get_bind()
    if bind.scalar(sa.text('SELECT COUNT(*) FROM rfq_items WHERE material_id IS NULL')):
        raise RuntimeError('Cannot downgrade while RFQ items without a master material exist')
    with op.batch_alter_table('rfq_items') as batch:
        batch.drop_index('ix_rfq_items_subcategory_id')
        batch.drop_index('ix_rfq_items_main_category_id')
        batch.drop_constraint('fk_rfq_items_subcategory', type_='foreignkey')
        batch.drop_constraint('fk_rfq_items_main_category', type_='foreignkey')
        batch.drop_column('subcategory_id')
        batch.drop_column('main_category_id')
        batch.alter_column('material_id', existing_type=sa.String(36), nullable=False)
    op.drop_column('rfqs', 'required_qualifications')
