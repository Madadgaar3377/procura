"""Add category_review_decisions table for explicit category review audit history."""
from alembic import op
import sqlalchemy as sa

revision = 'ze46a215ed47'
down_revision = 'ze45f104dc36'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'category_review_decisions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('category_id', sa.String(36), sa.ForeignKey('categories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('decision', sa.String(30), nullable=False),
        sa.Column('issue_type', sa.String(40), nullable=False),
        sa.Column('discipline', sa.String(30), nullable=True),
        sa.Column('merged_into_category_id', sa.String(36), sa.ForeignKey('categories.id', ondelete='SET NULL'), nullable=True),
        sa.Column('actor_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_category_review_decisions_category_id', 'category_review_decisions', ['category_id'])
    op.create_index('ix_cat_review_category_created', 'category_review_decisions', ['category_id', 'created_at'])
    op.create_index('ix_cat_review_category_current', 'category_review_decisions', ['category_id', 'is_current'])


def downgrade():
    op.drop_index('ix_cat_review_category_current', table_name='category_review_decisions')
    op.drop_index('ix_cat_review_category_created', table_name='category_review_decisions')
    op.drop_index('ix_category_review_decisions_category_id', table_name='category_review_decisions')
    op.drop_table('category_review_decisions')
