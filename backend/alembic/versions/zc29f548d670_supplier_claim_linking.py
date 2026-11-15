"""Add supplier claim linking, evidence metadata, and concurrency indexes."""
from alembic import op
import sqlalchemy as sa

revision = 'zc29f548d670'
down_revision = ('s29a5d04e781', 'zb18e437c569')
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_claims', sa.Column('document_key', sa.String(400), nullable=True))
    op.add_column('organization_claims', sa.Column('matched_by', sa.String(120), nullable=True))
    op.add_column('organization_claims', sa.Column('requested_cr_number', sa.String(80), nullable=True))
    op.add_column('organization_claims', sa.Column('assigned_role', sa.String(30), nullable=False, server_default='owner'))
    op.add_column('organization_claims', sa.Column('has_existing_owner', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('organization_claims', sa.Column('reviewer_notes', sa.Text(), nullable=True))
    op.add_column('organization_claims', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_org_claims_org_status', 'organization_claims', ['org_id', 'status'])
    op.create_index('ix_org_claims_claimant_status', 'organization_claims', ['claimant_id', 'status'])
    op.create_index(
        'uq_org_claims_pending_user_org',
        'organization_claims',
        ['org_id', 'claimant_id'],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade():
    op.drop_index('uq_org_claims_pending_user_org', table_name='organization_claims')
    op.drop_index('ix_org_claims_claimant_status', table_name='organization_claims')
    op.drop_index('ix_org_claims_org_status', table_name='organization_claims')
    for name in (
        'reviewed_at',
        'reviewer_notes',
        'has_existing_owner',
        'assigned_role',
        'requested_cr_number',
        'matched_by',
        'document_key',
    ):
        op.drop_column('organization_claims', name)
