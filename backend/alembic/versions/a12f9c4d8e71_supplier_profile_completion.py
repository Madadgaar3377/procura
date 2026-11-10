"""Track supplier profile completion separately from verification."""
from alembic import op
import sqlalchemy as sa

revision = 'a12f9c4d8e71'
down_revision = 'z96c2d15a347'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('profile_status', sa.String(length=30), nullable=False, server_default='incomplete'))
    op.create_index('ix_organizations_profile_status', 'organizations', ['profile_status'])


def downgrade():
    op.drop_index('ix_organizations_profile_status', table_name='organizations')
    op.drop_column('organizations', 'profile_status')
