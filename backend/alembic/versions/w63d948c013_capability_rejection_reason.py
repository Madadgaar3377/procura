"""Store administrator rejection reasons for supplier capabilities."""
from alembic import op
import sqlalchemy as sa

revision='w63d948c013'
down_revision='v52c837ba902'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('supplier_capabilities',sa.Column('rejection_reason',sa.Text(),nullable=True))

def downgrade():
    op.drop_column('supplier_capabilities','rejection_reason')
