"""durable staged material imports and normalized material master fields

Revision ID: e83c1a9d5b24
Revises: a47c902d8e31
Create Date: 2026-10-21
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from uuid import uuid4
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e83c1a9d5b24'
down_revision: str | Sequence[str] | None = 'a47c902d8e31'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normal(value: str | None) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def upgrade() -> None:
    op.add_column('categories', sa.Column('normalized_name', sa.String(180), nullable=True))
    op.add_column('categories', sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('categories', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    op.add_column('materials', sa.Column('main_category_id', sa.String(36), nullable=True))
    op.add_column('materials', sa.Column('subcategory_id', sa.String(36), nullable=True))
    op.add_column('materials', sa.Column('normalized_description', sa.String(250), nullable=True))
    op.add_column('materials', sa.Column('arabic_description', sa.String(250), nullable=False, server_default=''))
    op.add_column('materials', sa.Column('source_unit', sa.String(30), nullable=True))
    op.add_column('materials', sa.Column('source_sn', sa.String(120), nullable=True))
    op.add_column('materials', sa.Column('import_batch_id', sa.String(36), nullable=True))
    op.add_column('materials', sa.Column('import_row_number', sa.Integer(), nullable=True))
    op.add_column('materials', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))

    op.add_column('import_batches', sa.Column('completed_rows', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('stage', sa.String(30), nullable=False, server_default='preview'))
    op.add_column('import_batches', sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('source_storage_key', sa.String(300), nullable=True))
    op.add_column('import_batches', sa.Column('source_bytes', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('source_sha256', sa.String(64), nullable=True))
    op.add_column('import_batches', sa.Column('import_options', sa.JSON(), nullable=True))
    op.add_column('import_batches', sa.Column('preview_metadata', sa.JSON(), nullable=True))
    op.add_column('import_batches', sa.Column('failure_reason', sa.String(500), nullable=True))
    op.add_column('import_batches', sa.Column('upload_duration_ms', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('parse_duration_ms', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('import_batches', sa.Column('apply_duration_ms', sa.Integer(), nullable=False, server_default='0'))

    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        op.create_foreign_key('fk_materials_main_category_id', 'materials', 'categories', ['main_category_id'], ['id'])
        op.create_foreign_key('fk_materials_subcategory_id', 'materials', 'categories', ['subcategory_id'], ['id'])
        op.create_foreign_key('fk_materials_import_batch_id', 'materials', 'import_batches', ['import_batch_id'], ['id'])

    op.create_table(
        'measurement_units',
        sa.Column('code', sa.String(30), nullable=False),
        sa.Column('name_en', sa.String(80), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_measurement_units_code', 'measurement_units', ['code'], unique=True)
    op.create_index('ix_categories_normalized_name', 'categories', ['normalized_name'])
    op.create_index('ix_categories_parent_name_normalized', 'categories', ['parent_id', 'normalized_name'])
    op.create_index('ix_materials_category_sort', 'materials', ['category_id', 'code'])
    op.create_index('ix_materials_subcategory_sort', 'materials', ['subcategory_id', 'code'])
    op.create_index('ix_materials_main_category_id', 'materials', ['main_category_id'])
    op.create_index('ix_materials_subcategory_id', 'materials', ['subcategory_id'])
    op.create_index('ix_materials_source_sn', 'materials', ['source_sn'])
    op.create_index('ix_materials_normalized_description', 'materials', ['normalized_description'])
    op.create_index('ix_materials_import_batch_id', 'materials', ['import_batch_id'])

    op.create_table(
        'material_revisions',
        sa.Column('material_id', sa.String(36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('actor_user_id', sa.String(36), nullable=True),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id']),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('material_id', 'version', name='uq_material_revision'),
    )
    op.create_index('ix_material_revisions_material_id', 'material_revisions', ['material_id'])
    op.create_index('ix_material_revisions_material_created', 'material_revisions', ['material_id', 'created_at'])

    category = sa.table('categories', sa.column('id', sa.String), sa.column('name_en', sa.String),
                        sa.column('normalized_name', sa.String),
                        sa.column('name_ar', sa.String), sa.column('parent_id', sa.String))
    material = sa.table('materials', sa.column('id', sa.String), sa.column('category_id', sa.String),
                        sa.column('name_en', sa.String), sa.column('name_ar', sa.String),
                        sa.column('unit', sa.String), sa.column('description', sa.Text))
    categories = {row.id: row for row in bind.execute(sa.select(category)).all()}
    for row in categories.values():
        bind.execute(category.update().where(category.c.id == row.id).values(normalized_name=_normal(row.name_en)))
    units = sa.table('measurement_units', sa.column('id', sa.String), sa.column('code', sa.String),
                     sa.column('name_en', sa.String), sa.column('active', sa.Boolean),
                     sa.column('sort_order', sa.Integer), sa.column('created_at', sa.DateTime(timezone=True)))
    unit_codes = set()
    for row in bind.execute(sa.select(material)).all():
        leaf = categories.get(row.category_id)
        parent = categories.get(leaf.parent_id) if leaf and leaf.parent_id else None
        main_id = parent.id if parent and parent.parent_id else (leaf.id if leaf else None)
        sub_id = leaf.id if parent and parent.parent_id else None
        code = (row.unit or 'No.').strip().upper()[:30] or 'NO.'
        unit_codes.add(code)
        bind.execute(material.update().where(material.c.id == row.id).values(
            main_category_id=main_id,
            subcategory_id=sub_id,
            normalized_description=_normal(row.name_en),
            arabic_description=row.name_ar or '',
            source_unit=row.unit,
        ))
    for position, code in enumerate(sorted(unit_codes)):
        bind.execute(units.insert().values(id=str(uuid4()), code=code,
                                           name_en=code, active=True, sort_order=position,
                                           created_at=datetime.now(timezone.utc)))
    for position, code in enumerate(('PCS', 'KG', 'M', 'M2', 'M3', 'SET', 'EA', 'NO.', 'L', 'TON'), start=len(unit_codes)):
        if code not in unit_codes:
            bind.execute(units.insert().values(id=str(uuid4()), code=code, name_en=code,
                                               active=True, sort_order=position,
                                               created_at=datetime.now(timezone.utc)))
    if bind.dialect.name == 'postgresql':
        op.execute('CREATE INDEX IF NOT EXISTS ix_materials_normalized_description_trgm ON materials USING gin (normalized_description gin_trgm_ops)')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS ix_materials_normalized_description_trgm')
    op.drop_index('ix_material_revisions_material_created', table_name='material_revisions')
    op.drop_index('ix_material_revisions_material_id', table_name='material_revisions')
    op.drop_table('material_revisions')
    op.drop_index('ix_materials_import_batch_id', table_name='materials')
    op.drop_index('ix_materials_normalized_description', table_name='materials')
    op.drop_index('ix_materials_source_sn', table_name='materials')
    op.drop_index('ix_materials_subcategory_id', table_name='materials')
    op.drop_index('ix_materials_main_category_id', table_name='materials')
    op.drop_index('ix_materials_subcategory_sort', table_name='materials')
    op.drop_index('ix_materials_category_sort', table_name='materials')
    op.drop_index('ix_categories_parent_name_normalized', table_name='categories')
    op.drop_index('ix_categories_normalized_name', table_name='categories')
    op.drop_index('ix_measurement_units_code', table_name='measurement_units')
    op.drop_table('measurement_units')
    if bind.dialect.name != 'sqlite':
        op.drop_constraint('fk_materials_import_batch_id', 'materials', type_='foreignkey')
        op.drop_constraint('fk_materials_subcategory_id', 'materials', type_='foreignkey')
        op.drop_constraint('fk_materials_main_category_id', 'materials', type_='foreignkey')
    for table, column in (
        ('import_batches', 'apply_duration_ms'), ('import_batches', 'parse_duration_ms'),
        ('import_batches', 'upload_duration_ms'), ('import_batches', 'failure_reason'),
        ('import_batches', 'preview_metadata'), ('import_batches', 'import_options'),
        ('import_batches', 'source_sha256'), ('import_batches', 'source_bytes'),
        ('import_batches', 'source_storage_key'), ('import_batches', 'retry_count'),
        ('import_batches', 'stage'), ('import_batches', 'completed_rows'),
        ('materials', 'import_row_number'), ('materials', 'import_batch_id'),
        ('materials', 'updated_at'),
        ('materials', 'source_sn'), ('materials', 'source_unit'),
        ('materials', 'arabic_description'), ('materials', 'normalized_description'),
        ('materials', 'subcategory_id'), ('materials', 'main_category_id'),
        ('categories', 'updated_at'), ('categories', 'sort_order'), ('categories', 'normalized_name'),
    ):
        op.drop_column(table, column)
