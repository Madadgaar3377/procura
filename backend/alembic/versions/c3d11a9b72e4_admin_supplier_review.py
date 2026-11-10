"""Add contact roles and timestamped supplier review decisions."""
from alembic import op
import sqlalchemy as sa

revision = 'c3d11a9b72e4'
down_revision = 'b71d2e9f4a60'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_contacts', sa.Column('role', sa.String(length=100), nullable=True))
    op.alter_column('verifications', 'cr_number', existing_type=sa.String(length=80), nullable=True)
    op.add_column('verifications', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('verifications', 'reviewed_at')
    op.alter_column('verifications', 'cr_number', existing_type=sa.String(length=80), nullable=False)
    op.drop_column('organization_contacts', 'role')
