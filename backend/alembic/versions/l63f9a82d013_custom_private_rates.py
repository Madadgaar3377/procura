"""Add private custom descriptions and explicit capability eligibility states.

Existing rates, confirmation flags and quotation snapshots are preserved.
"""
from alembic import op
import sqlalchemy as sa

revision='l63f9a82d013'
down_revision='k52e8a71c902'
branch_labels=None
depends_on=None


def upgrade():
    with op.batch_alter_table('supplier_capabilities') as batch:
        batch.add_column(sa.Column('eligibility_status',sa.String(20),nullable=False,server_default='enabled'))
        batch.create_check_constraint('ck_capability_eligibility',"eligibility_status IN ('enabled','suspended','revoked','rejected')")
        batch.create_index('ix_capability_eligible_scope',['category_id','city','eligibility_status','confirmed','accepts_rfqs'])
    with op.batch_alter_table('price_book_items') as batch:
        batch.alter_column('material_id',existing_type=sa.String(36),nullable=True)
        batch.add_column(sa.Column('main_category_id',sa.String(36),nullable=True))
        batch.add_column(sa.Column('subcategory_id',sa.String(36),nullable=True))
        batch.add_column(sa.Column('supplier_description',sa.Text(),nullable=False,server_default=''))
        batch.add_column(sa.Column('custom_key',sa.String(64),nullable=True))
        batch.create_foreign_key('fk_private_rate_main_category','categories',['main_category_id'],['id'])
        batch.create_foreign_key('fk_private_rate_subcategory','categories',['subcategory_id'],['id'])
        batch.create_index('ix_private_rate_org_categories',['org_id','main_category_id','subcategory_id'])
        batch.create_index('uq_private_custom_rate_active',['org_id','custom_key','price_basis'],unique=True,postgresql_where=sa.text('active = true AND material_id IS NULL'),sqlite_where=sa.text('active = 1 AND material_id IS NULL'))
        batch.create_check_constraint('ck_private_custom_rate_details',"material_id IS NOT NULL OR (main_category_id IS NOT NULL AND length(trim(supplier_description)) >= 3 AND length(trim(unit)) > 0 AND custom_key IS NOT NULL)")
    # Snapshot supplier-facing descriptions and existing material category IDs;
    # no master material or quotation row is updated.
    op.execute(sa.text("""UPDATE price_book_items SET
        supplier_description = (SELECT CASE WHEN length(trim(price_book_items.offered_description)) > 0 THEN price_book_items.offered_description ELSE materials.name_en END FROM materials WHERE materials.id=price_book_items.material_id),
        main_category_id = (SELECT COALESCE(materials.main_category_id,categories.parent_id,categories.id) FROM materials JOIN categories ON categories.id=materials.category_id WHERE materials.id=price_book_items.material_id),
        subcategory_id = (SELECT COALESCE(materials.subcategory_id,CASE WHEN categories.parent_id IS NOT NULL THEN categories.id ELSE NULL END) FROM materials JOIN categories ON categories.id=materials.category_id WHERE materials.id=price_book_items.material_id)
        WHERE material_id IS NOT NULL"""))


def downgrade():
    raise RuntimeError('Custom private rates require a reviewed data migration before downgrade; no records were removed.')
