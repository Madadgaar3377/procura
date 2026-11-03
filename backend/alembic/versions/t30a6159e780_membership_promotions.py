"""Add membership administration without replacing subscriptions or legacy prices."""
from alembic import op
import sqlalchemy as sa
revision='t30a6159e780'
down_revision='s29f5048d679'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('rfqs',sa.Column('public_display_authorized',sa.Boolean(),nullable=False,server_default='false'))
    for name,typ,default in [
        ('description',sa.Text(),''),('monthly_price',sa.Numeric(12,2),'0'),
        ('yearly_price',sa.Numeric(12,2),'0'),('currency',sa.String(3),'SAR'),
        ('features',sa.JSON(),'[]'),('active',sa.Boolean(),'true'),
        ('archived',sa.Boolean(),'false'),('recommended',sa.Boolean(),'false'),
        ('premium',sa.Boolean(),'false'),('display_order',sa.Integer(),'0')]:
        op.add_column('membership_plans',sa.Column(name,typ,nullable=False,server_default=default))
    for name,typ,default in [('starts_at',sa.DateTime(timezone=True),None),
        ('price_snapshot',sa.Numeric(12,2),None),('currency_snapshot',sa.String(3),None),
        ('billing_interval',sa.String(10),'monthly'),('premium_snapshot',sa.Boolean(),'false')]:
        op.add_column('subscriptions',sa.Column(name,typ,nullable=default is None,server_default=default))
    op.create_table('membership_settings',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('config',sa.JSON(),nullable=False))
    op.create_table('promotions',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('kind',sa.String(30),nullable=False),
        sa.Column('org_id',sa.String(36),sa.ForeignKey('organizations.id')),
        sa.Column('rfq_id',sa.String(36),sa.ForeignKey('rfqs.id')),
        sa.Column('placement',sa.String(30),nullable=False),sa.Column('priority',sa.Integer(),nullable=False),
        sa.Column('starts_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('active',sa.Boolean(),nullable=False),sa.Column('note',sa.Text(),nullable=False))
    for column in ['kind','org_id','rfq_id','starts_at','expires_at']:
        op.create_index('ix_promotions_'+column,'promotions',[column])

def downgrade():
    raise RuntimeError('Forward-only: retain membership and promotion history')
