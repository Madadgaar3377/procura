"""Add user avatar_file_id and organization logo_file_id."""
from alembic import op
import sqlalchemy as sa

revision = 'zd30a659e781'
down_revision = 'zc29f548d670'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('avatar_file_id', sa.String(36), nullable=True))
    op.add_column('organizations', sa.Column('logo_file_id', sa.String(36), nullable=True))


def downgrade():
    op.drop_column('organizations', 'logo_file_id')
    op.drop_column('users', 'avatar_file_id')
