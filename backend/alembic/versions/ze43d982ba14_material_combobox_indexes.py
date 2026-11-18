"""Indexes for scoped, multilingual RFQ material autocomplete."""
from alembic import op

revision = 'ze43d982ba14'
down_revision = 'zd30a659e781'
branch_labels = None
depends_on = None

# Name, description and normalized_description trigram indexes already exist.
COLUMNS = ('code', 'source_material_code', 'expenditure_code', 'name_ar', 'arabic_description')

def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.execute('CREATE INDEX ix_materials_rfq_scope_sort ON materials (main_category_id, subcategory_id, code, id) WHERE active IS TRUE')
    for column in COLUMNS:
        op.execute(f'CREATE INDEX ix_materials_rfq_{column}_trgm ON materials USING gin ({column} gin_trgm_ops)')

def downgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    for column in COLUMNS:
        op.execute(f'DROP INDEX ix_materials_rfq_{column}_trgm')
    op.execute('DROP INDEX ix_materials_rfq_scope_sort')
