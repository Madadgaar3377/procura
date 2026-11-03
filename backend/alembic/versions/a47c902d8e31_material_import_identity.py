"""add durable material import identity and source classification fields

Revision ID: a47c902d8e31
Revises: f2a8c14d6e90
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a47c902d8e31'
down_revision: str | Sequence[str] | None = 'f2a8c14d6e90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.add_column('materials', sa.Column('discipline', sa.String(length=30), nullable=True))
    op.add_column('materials', sa.Column('source_material_code', sa.String(length=120), nullable=True))
    op.add_column('materials', sa.Column('expenditure_code', sa.String(length=120), nullable=True))
    op.add_column('materials', sa.Column('source_identity', sa.String(length=64), nullable=True))
    op.add_column('import_batches', sa.Column('updated_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('unchanged_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('categories_created', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_materials_discipline', 'materials', ['discipline'], unique=False)
    op.create_index('ix_materials_source_material_code', 'materials', ['source_material_code'], unique=False)
    op.create_index('ix_materials_expenditure_code', 'materials', ['expenditure_code'], unique=False)
    op.create_index('ix_materials_source_identity', 'materials', ['source_identity'], unique=True)
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        op.execute('CREATE INDEX IF NOT EXISTS ix_materials_name_en_trgm ON materials USING gin (name_en gin_trgm_ops)')
        op.execute('CREATE INDEX IF NOT EXISTS ix_materials_description_trgm ON materials USING gin (description gin_trgm_ops)')

def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS ix_materials_description_trgm')
        op.execute('DROP INDEX IF EXISTS ix_materials_name_en_trgm')
    op.drop_index('ix_materials_source_identity', table_name='materials')
    op.drop_index('ix_materials_expenditure_code', table_name='materials')
    op.drop_index('ix_materials_source_material_code', table_name='materials')
    op.drop_index('ix_materials_discipline', table_name='materials')
    op.drop_column('materials', 'source_identity')
    op.drop_column('materials', 'expenditure_code')
    op.drop_column('materials', 'source_material_code')
    op.drop_column('materials', 'discipline')
    op.drop_column('import_batches', 'updated_at')
    op.drop_column('import_batches', 'categories_created')
    op.drop_column('import_batches', 'unchanged_count')
    op.drop_column('import_batches', 'updated_count')
