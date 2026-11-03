"""Persist the account notification language; existing accounts use English."""
from alembic import op
import sqlalchemy as sa
revision = 'm74a0b93e124'
down_revision = 'l63f9a82d013'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('notification_language', sa.String(2), nullable=False, server_default='en'))


def downgrade():
    op.drop_column('users', 'notification_language')
