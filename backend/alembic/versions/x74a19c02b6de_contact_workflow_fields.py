"""Extend existing contact inquiries with ticket metadata and classification."""
from alembic import op
import sqlalchemy as sa

revision = 'x74a19c02b6de'
down_revision = 'w63d948c013'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('contact_inquiries', sa.Column('ticket_reference', sa.String(32), nullable=True))
    op.add_column('contact_inquiries', sa.Column('submission_key', sa.String(36), nullable=True))
    op.add_column('contact_inquiries', sa.Column('user_id', sa.String(36), nullable=True))
    op.add_column('contact_inquiries', sa.Column('organization_id', sa.String(36), nullable=True))
    op.add_column('contact_inquiries', sa.Column('user_type', sa.String(20), server_default='other', nullable=False))
    op.add_column('contact_inquiries', sa.Column('category', sa.String(40), server_default='general_inquiry', nullable=False))
    op.add_column('contact_inquiries', sa.Column('subject', sa.String(200), server_default='General inquiry', nullable=False))
    op.add_column('contact_inquiries', sa.Column('internal_notes', sa.Text(), nullable=True))
    op.add_column('contact_inquiries', sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('contact_inquiries', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE contact_inquiries SET ticket_reference = 'PM-LEGACY-' || substr(id, 1, 22) WHERE ticket_reference IS NULL")
    op.execute('UPDATE contact_inquiries SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL')
    with op.batch_alter_table('contact_inquiries') as batch:
        batch.alter_column('ticket_reference', existing_type=sa.String(32), nullable=False)
        batch.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'))
        batch.create_foreign_key('fk_contact_inquiries_user_id_users', 'users', ['user_id'], ['id'])
        batch.create_foreign_key('fk_contact_inquiries_organization_id_organizations', 'organizations', ['organization_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_contact_inquiries_user_id', 'contact_inquiries', ['user_id'])
    op.create_index('ix_contact_inquiries_organization_id', 'contact_inquiries', ['organization_id'])
    op.create_index('ix_contact_inquiries_category', 'contact_inquiries', ['category'])
    op.create_index('ix_contact_inquiries_created_at', 'contact_inquiries', ['created_at'])
    op.create_index('ix_contact_inquiries_ticket_reference', 'contact_inquiries', ['ticket_reference'], unique=True)
    op.create_index('ix_contact_inquiries_submission_key', 'contact_inquiries', ['submission_key'], unique=True)

def downgrade():
    op.drop_index('ix_contact_inquiries_ticket_reference', table_name='contact_inquiries')
    op.drop_index('ix_contact_inquiries_submission_key', table_name='contact_inquiries')
    op.drop_index('ix_contact_inquiries_category', table_name='contact_inquiries')
    op.drop_index('ix_contact_inquiries_created_at', table_name='contact_inquiries')
    op.drop_index('ix_contact_inquiries_organization_id', table_name='contact_inquiries')
    op.drop_index('ix_contact_inquiries_user_id', table_name='contact_inquiries')
    with op.batch_alter_table('contact_inquiries') as batch:
        batch.drop_constraint('fk_contact_inquiries_user_id_users', type_='foreignkey')
        batch.drop_constraint('fk_contact_inquiries_organization_id_organizations', type_='foreignkey')
        for column in ('updated_at', 'resolved_at', 'internal_notes', 'subject', 'category', 'user_type', 'organization_id', 'user_id', 'submission_key', 'ticket_reference'):
            batch.drop_column(column)
