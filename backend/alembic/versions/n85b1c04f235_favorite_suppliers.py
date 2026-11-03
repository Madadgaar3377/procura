"""Organization scoped buyer favorites; no existing records change."""
from alembic import op
import sqlalchemy as sa
revision = 'n85b1c04f235'
down_revision = 'm74a0b93e124'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('favorite_suppliers',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('buyer_org_id',sa.String(36),sa.ForeignKey('organizations.id'),nullable=False),
        sa.Column('supplier_org_id',sa.String(36),sa.ForeignKey('organizations.id'),nullable=False),
        sa.UniqueConstraint('buyer_org_id','supplier_org_id',name='uq_favorite_supplier'))
    op.create_index('ix_favorite_suppliers_buyer_org_id','favorite_suppliers',['buyer_org_id'])
    op.create_index('ix_favorite_suppliers_supplier_org_id','favorite_suppliers',['supplier_org_id'])


def downgrade():
    op.drop_table('favorite_suppliers')
