"""Index approved material links used by supplier matching without changing data."""
from alembic import op
import sqlalchemy as sa

revision = 'r18f4a932c70'
down_revision = 'j40a8c12d506'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            op.create_index('ix_capability_material_links', 'supplier_capabilities',
                            [sa.text('(material_ids::jsonb)')], postgresql_using='gin',
                            postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            op.drop_index('ix_capability_material_links', table_name='supplier_capabilities',
                          postgresql_concurrently=True, if_exists=True)
