"""Index account cleanup references and administration ordering.

Revision ID: f8c12a4d9b60
Revises: a61d9f3208bc
"""
from alembic import op
import sqlalchemy as sa

revision='f8c12a4d9b60'
down_revision='a61d9f3208bc'
branch_labels=None
depends_on=None

INDEXES=[
    ('ix_users_created_id','users',['created_at','id']),
    ('ix_notifications_recipient_user','notifications',['recipient_user_id']),
    ('ix_import_batches_initiated_by','import_batches',['initiated_by']),
    ('ix_material_revisions_actor_user','material_revisions',['actor_user_id']),
    ('ix_audit_events_actor_user','audit_events',['actor_user_id']),
    ('ix_organization_claims_claimant','organization_claims',['claimant_id']),
    ('ix_organization_claims_reviewer','organization_claims',['reviewer_id']),
    ('ix_verifications_reviewer','verifications',['reviewer_id']),
    ('ix_contact_inquiries_handler','contact_inquiries',['handled_by']),
]

def upgrade():
    inspector=sa.inspect(op.get_bind())
    for name,table,columns in INDEXES:
        existing={i['name'] for i in inspector.get_indexes(table)}
        if name not in existing:op.create_index(name,table,columns)
    existing={i['name'] for i in inspector.get_indexes('outbox_events')}
    if 'ix_outbox_events_recipient_user' not in existing:
        op.create_index('ix_outbox_events_recipient_user','outbox_events',[sa.column('payload',sa.JSON())['user_id'].as_string()])

def downgrade():
    op.drop_index('ix_outbox_events_recipient_user',table_name='outbox_events')
    for name,table,columns in reversed(INDEXES):op.drop_index(name,table_name=table)
