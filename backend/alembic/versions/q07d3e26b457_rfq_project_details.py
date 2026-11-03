"""Add optional RFQ project, delivery, instructions and private attachment records."""
import sqlalchemy as sa

from alembic import op

revision = 'q07d3e26b457'
down_revision = 'p96c2d15a346'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('rfqs', sa.Column('project_name', sa.String(250), nullable=False, server_default=''))
    op.add_column('rfqs', sa.Column('required_delivery_date', sa.Date(), nullable=True))
    op.add_column('rfqs', sa.Column('additional_instructions', sa.Text(), nullable=False, server_default=''))
    op.add_column('rfqs', sa.Column('attachment_records', sa.JSON(), nullable=False, server_default='[]'))


def downgrade():
    for name in ('attachment_records', 'additional_instructions', 'required_delivery_date', 'project_name'):
        op.drop_column('rfqs', name)
