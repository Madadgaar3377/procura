"""Opt-in publication settings, without publishing or rewriting existing profiles."""
import sqlalchemy as sa

from alembic import op

revision = 'p96c2d15a346'
down_revision = 'n85b1c04f235'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('organization_public_consent',
        sa.Column('org_id', sa.String(36), sa.ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('fields', sa.JSON(), nullable=False))


def downgrade():
    op.drop_table('organization_public_consent')
