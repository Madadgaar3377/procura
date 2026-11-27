"""Additive supplier profile details and physical company locations.

New tables only: no existing column is altered, so the running API keeps working
before this migration is applied (the admin endpoints report it as pending).

Revision ID: zm56c3d4e5f6
Revises: zl55b2c3d4e5
"""
from alembic import op
import sqlalchemy as sa

revision = 'zm56c3d4e5f6'
down_revision = 'zl55b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'supplier_profile_details',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('org_id', sa.String(36), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('business_types', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('business_services', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('imported_scope_text', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_supplier_profile_details_org_id', 'supplier_profile_details', ['org_id'], unique=True)
    op.create_table(
        'organization_locations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('org_id', sa.String(36), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('address', sa.String(300), nullable=True),
        sa.Column('notes', sa.String(300), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint("kind IN ('head_office','office','branch','factory','warehouse','showroom')",
                           name='ck_organization_location_kind'),
    )
    op.create_index('ix_organization_locations_org_id', 'organization_locations', ['org_id'])
    op.create_index('ix_organization_locations_org_kind', 'organization_locations', ['org_id', 'kind'])


def downgrade():
    op.drop_index('ix_organization_locations_org_kind', table_name='organization_locations')
    op.drop_index('ix_organization_locations_org_id', table_name='organization_locations')
    op.drop_table('organization_locations')
    op.drop_index('ix_supplier_profile_details_org_id', table_name='supplier_profile_details')
    op.drop_table('supplier_profile_details')
