"""Assign supplier capability categories to responsible contacts."""
from alembic import op
import sqlalchemy as sa

revision='f28c6d4a1b73'
down_revision='e17c4b9a2d61'
branch_labels=None
depends_on=None


def upgrade():
    with op.batch_alter_table('organization_contacts') as batch:
        batch.add_column(sa.Column('is_primary',sa.Boolean(),nullable=False,server_default=sa.text('false')))
    op.create_table('supplier_contact_capabilities',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('contact_id',sa.String(36),sa.ForeignKey('organization_contacts.id',ondelete='CASCADE'),nullable=False),
        sa.Column('capability_id',sa.String(36),sa.ForeignKey('supplier_capabilities.id',ondelete='CASCADE'),nullable=False),
        sa.Column('is_primary',sa.Boolean(),nullable=False,server_default=sa.text('false')),
        sa.UniqueConstraint('contact_id','capability_id',name='uq_supplier_contact_capability'))
    op.create_index('ix_supplier_contact_capabilities_contact_id','supplier_contact_capabilities',['contact_id'])
    op.create_index('ix_supplier_contact_capabilities_capability_id','supplier_contact_capabilities',['capability_id'])
    op.create_index('ix_supplier_contact_capabilities_capability_contact','supplier_contact_capabilities',['capability_id','contact_id'])


def downgrade():
    op.drop_index('ix_supplier_contact_capabilities_capability_contact',table_name='supplier_contact_capabilities')
    op.drop_index('ix_supplier_contact_capabilities_capability_id',table_name='supplier_contact_capabilities')
    op.drop_index('ix_supplier_contact_capabilities_contact_id',table_name='supplier_contact_capabilities')
    op.drop_table('supplier_contact_capabilities')
    with op.batch_alter_table('organization_contacts') as batch:
        batch.drop_column('is_primary')
