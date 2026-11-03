"""Plan publication, stable codes, versioned edits and immutable terms snapshots."""
from alembic import op
import sqlalchemy as sa
revision='u41b726af891'
down_revision='t30a6159e780'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('membership_plans',sa.Column('slug',sa.String(100),nullable=True))
    op.execute("UPDATE membership_plans SET slug='plan-' || id")
    for name,typ,default,nullable in [
        ('publication_status',sa.String(20),'published',False),
        ('visible_on_website',sa.Boolean(),'true',False),
        ('available_for_subscription',sa.Boolean(),'true',False),
        ('billing_intervals',sa.JSON(),'["monthly","yearly"]',False),
        ('feature_details',sa.JSON(),'[]',False),
        ('available_from',sa.DateTime(timezone=True),None,True),
        ('available_until',sa.DateTime(timezone=True),None,True),
        ('archived_at',sa.DateTime(timezone=True),None,True),
        ('updated_at',sa.DateTime(timezone=True),None,True),
        ('version',sa.Integer(),'1',False)]:
        op.add_column('membership_plans',sa.Column(name,typ,nullable=nullable,server_default=default))
    op.execute('UPDATE membership_plans SET updated_at=COALESCE(created_at,CURRENT_TIMESTAMP)')
    # Keep the first existing recommendation; never alter agreed subscription prices.
    bind=op.get_bind()
    rows=bind.execute(sa.text('SELECT id FROM membership_plans WHERE recommended=true AND archived=false ORDER BY display_order,created_at,id')).scalars().all()
    for plan_id in rows[1:]:
        bind.execute(sa.text('UPDATE membership_plans SET recommended=false WHERE id=:id'),{'id':plan_id})
    with op.batch_alter_table('membership_plans') as batch:
        batch.alter_column('slug',existing_type=sa.String(100),nullable=False)
        batch.alter_column('updated_at',existing_type=sa.DateTime(timezone=True),nullable=False,server_default=sa.text('CURRENT_TIMESTAMP'))
        batch.alter_column('publication_status',existing_type=sa.String(20),server_default='draft')
        batch.create_unique_constraint('uq_membership_plan_slug',['slug'])
        batch.create_check_constraint('ck_membership_prices','monthly_price >= 0 AND yearly_price >= 0')
        batch.create_check_constraint('ck_membership_publication',"publication_status IN ('draft','published','unpublished')")
        batch.create_check_constraint('ck_membership_dates','available_until IS NULL OR available_from IS NULL OR available_until > available_from')
    op.create_index('uq_membership_recommended','membership_plans',['recommended'],unique=True,
        postgresql_where=sa.text('recommended=true AND archived=false'),sqlite_where=sa.text('recommended=1 AND archived=0'))
    op.create_index('ix_membership_public_order','membership_plans',['publication_status','visible_on_website','display_order'])
    op.create_index('ix_subscriptions_plan_id','subscriptions',['plan_id'])
    op.add_column('subscriptions',sa.Column('plan_snapshot',sa.JSON(),nullable=True))

def downgrade():
    raise RuntimeError('Forward-only: retain plan and subscription history')
