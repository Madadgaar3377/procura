"""Private rate lifecycle, per-unit snapshots and explicit quotation authorization.

No rate is removed or silently deactivated. Legacy duplicates/nonpositive rates
require an explicit review before this migration can proceed.
"""
from alembic import op
import sqlalchemy as sa

revision='k52e8a71c902'
down_revision='j40c3e02d817'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM price_book_items GROUP BY org_id, material_id HAVING count(*) > 1 LIMIT 1')).first():
        raise RuntimeError('Review duplicate legacy supplier/material rates before migration. No rates were removed.')
    if bind.execute(sa.text('SELECT 1 FROM price_book_items WHERE unit_price <= 0 LIMIT 1')).first():
        raise RuntimeError('Review nonpositive legacy rates before migration. No rates were removed.')
    op.add_column('organizations',sa.Column('quote_automation',sa.JSON(),nullable=False,server_default='{}'))
    for col in [sa.Column('unit',sa.String(30),nullable=False,server_default=''),sa.Column('currency',sa.String(5),nullable=False,server_default='SAR'),sa.Column('price_basis',sa.String(30),nullable=False,server_default='supply'),sa.Column('active',sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column('offered_description',sa.Text(),nullable=False,server_default=''),sa.Column('remarks',sa.Text(),nullable=False,server_default=''),sa.Column('technical_confirmed',sa.Boolean(),nullable=False,server_default=sa.false()),sa.Column('material_version',sa.Integer(),nullable=False,server_default='1')]:op.add_column('price_book_items',col)
    if bind.dialect.name=='sqlite':
        op.add_column('price_book_items',sa.Column('updated_at',sa.DateTime(timezone=True),nullable=True))
        bind.execute(sa.text('UPDATE price_book_items SET updated_at = created_at'))
    else:
        op.add_column('price_book_items',sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.current_timestamp()))
    bind.execute(sa.text('UPDATE price_book_items SET unit = (SELECT unit FROM materials WHERE materials.id = price_book_items.material_id), material_version = (SELECT version FROM materials WHERE materials.id = price_book_items.material_id)'))
    with op.batch_alter_table('price_book_items') as batch:
        if bind.dialect.name=='sqlite':batch.alter_column('updated_at',existing_type=sa.DateTime(timezone=True),nullable=False,server_default=sa.func.current_timestamp())
        batch.alter_column('valid_until',existing_type=sa.DateTime(timezone=True),nullable=True)
        batch.create_check_constraint('ck_private_rate_positive','unit_price > 0')
    op.create_index('uq_private_rate_active','price_book_items',['org_id','material_id','price_basis'],unique=True,postgresql_where=sa.text('active = true'),sqlite_where=sa.text('active = 1'))
    op.create_index('ix_private_rate_org_active_material','price_book_items',['org_id','active','material_id','price_basis'])
    op.create_index('ix_private_rate_org_created','price_book_items',['org_id','created_at','id'])
    for col in [sa.Column('submission_source',sa.String(30),nullable=False,server_default='manual'),sa.Column('review_required',sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column('review_reasons',sa.JSON(),nullable=False,server_default='[]'),sa.Column('policy_revision',sa.Integer(),nullable=True)]:op.add_column('quotations',col)
    for col in [sa.Column('offered_description',sa.Text(),nullable=False,server_default=''),sa.Column('source_rate_id',sa.String(36),nullable=True),sa.Column('source_rate_version',sa.Integer(),nullable=True)]:op.add_column('quotation_lines',col)

def downgrade():
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM price_book_items WHERE valid_until IS NULL LIMIT 1')).first():raise RuntimeError('Cannot downgrade unbounded-validity rates without an explicit data migration')
    for name in ['source_rate_version','source_rate_id','offered_description']:op.drop_column('quotation_lines',name)
    for name in ['policy_revision','review_reasons','review_required','submission_source']:op.drop_column('quotations',name)
    for name in ['ix_private_rate_org_created','ix_private_rate_org_active_material','uq_private_rate_active']:op.drop_index(name,table_name='price_book_items')
    with op.batch_alter_table('price_book_items') as batch:
        batch.drop_constraint('ck_private_rate_positive',type_='check')
        batch.alter_column('valid_until',existing_type=sa.DateTime(timezone=True),nullable=False)
        for name in ['updated_at','material_version','technical_confirmed','remarks','offered_description','active','price_basis','currency','unit']:batch.drop_column(name)
    op.drop_column('organizations','quote_automation')
