"""Keep source products separate from the source trade and brands."""
from alembic import op
import sqlalchemy as sa

revision = 'zh51d834c229'
down_revision = 'zg50c723b118'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('imported_supplier_trades', sa.Column('raw_products', sa.Text(), nullable=False, server_default=''))


def downgrade():
    op.drop_column('imported_supplier_trades', 'raw_products')
