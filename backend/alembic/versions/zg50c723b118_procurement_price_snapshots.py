"""Retain original private rates and configurable commercial terms; preserve all rows."""
from alembic import op
import sqlalchemy as sa

revision = 'zg50c723b118'
down_revision = 'zf49b612a007'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('price_book_items', sa.Column('vat_rate', sa.Numeric(5,4), nullable=True))
    op.add_column('quotation_lines', sa.Column('vat_rate', sa.Numeric(5,4), nullable=True))
    op.add_column('quotation_lines', sa.Column('source_rate_snapshot', sa.JSON(), nullable=True))
    op.add_column('quotations', sa.Column('commercial_terms', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column('quotations', 'commercial_terms')
    op.drop_column('quotation_lines', 'source_rate_snapshot')
    op.drop_column('quotation_lines', 'vat_rate')
    op.drop_column('price_book_items', 'vat_rate')
