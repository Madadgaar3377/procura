"""Track the provenance of automatically mapped supplier capabilities."""
from alembic import op
import sqlalchemy as sa

revision = 'd5a8c2f19b30'
down_revision = 'c3d11a9b72e4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('supplier_capabilities', sa.Column('mapping_source', sa.String(length=30), nullable=True))


def downgrade():
    op.drop_column('supplier_capabilities', 'mapping_source')
