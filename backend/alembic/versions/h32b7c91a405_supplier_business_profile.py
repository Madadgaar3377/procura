"""Add nullable supplier business profile fields without changing existing records."""
from alembic import op
import sqlalchemy as sa

revision = 'h32b7c91a405'
down_revision = 'g91a02b41c77'
branch_labels = None
depends_on = None

FIELDS = {'trading_name':220,'company_type':30,'description':None,'contact_name':150,
          'designation':100,'business_email':200,'business_phone':30,'country':100,'region':100}

def upgrade():
    for name,length in FIELDS.items():
        op.add_column('organizations',sa.Column(name,sa.String(length) if length else sa.Text(),nullable=True))

def downgrade():
    for name in reversed(FIELDS):
        op.drop_column('organizations',name)
