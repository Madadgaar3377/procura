"""Deduplicate RFQ creates and record explicit private rate availability."""
from alembic import op
import sqlalchemy as sa

revision = 'zi52e945d330'
down_revision = 'zh51d834c229'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('rfqs', sa.Column('client_request_id', sa.String(64), nullable=True))
    op.create_index('uq_rfq_buyer_request', 'rfqs', ['buyer_org_id', 'client_request_id'],
                    unique=True, postgresql_where=sa.text('client_request_id IS NOT NULL'),
                    sqlite_where=sa.text('client_request_id IS NOT NULL'))
    op.add_column('price_book_items', sa.Column('availability', sa.String(20),
                                               nullable=False, server_default='unknown'))


def downgrade():
    op.drop_column('price_book_items', 'availability')
    op.drop_index('uq_rfq_buyer_request', table_name='rfqs')
    op.drop_column('rfqs', 'client_request_id')
