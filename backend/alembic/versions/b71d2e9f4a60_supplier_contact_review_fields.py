"""Preserve imported contact text and validated phone/fax metadata."""
from alembic import op
import sqlalchemy as sa

revision = 'b71d2e9f4a60'
down_revision = 'a12f9c4d8e71'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_contacts', sa.Column('phone_normalized', sa.String(length=16), nullable=True))
    op.add_column('organization_contacts', sa.Column('fax', sa.String(length=80), nullable=True))
    op.add_column('organization_contacts', sa.Column('raw_phone', sa.String(length=300), nullable=True))
    op.add_column('organization_contacts', sa.Column('raw_email', sa.String(length=500), nullable=True))
    op.add_column('organization_contacts', sa.Column('needs_review', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_index('ix_organization_contacts_phone_normalized', 'organization_contacts', ['phone_normalized'])


def downgrade():
    op.drop_index('ix_organization_contacts_phone_normalized', table_name='organization_contacts')
    op.drop_column('organization_contacts', 'needs_review')
    op.drop_column('organization_contacts', 'raw_email')
    op.drop_column('organization_contacts', 'raw_phone')
    op.drop_column('organization_contacts', 'fax')
    op.drop_column('organization_contacts', 'phone_normalized')
