"""Register object metadata without moving or deleting existing uploads."""
from alembic import op
import sqlalchemy as sa

revision = 'y85b20d17c3e'
down_revision = 'x74a19c02b6de'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('stored_files',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('bucket_type', sa.String(12), nullable=False),
        sa.Column('object_key', sa.String(500), nullable=False),
        sa.Column('original_filename', sa.String(200), nullable=False),
        sa.Column('safe_filename', sa.String(200), nullable=False),
        sa.Column('content_type', sa.String(120), nullable=False),
        sa.Column('file_extension', sa.String(12), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('checksum_sha256', sa.String(64)), sa.Column('etag', sa.String(200)),
        sa.Column('visibility', sa.String(12), nullable=False, server_default='private'),
        sa.Column('purpose', sa.String(40), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default='pending_upload'),
        sa.Column('owner_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=True),
        sa.Column('uploaded_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('entity_type', sa.String(24), nullable=False), sa.Column('entity_id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)))
    op.create_index('uq_stored_files_object_key', 'stored_files', ['object_key'], unique=True)
    op.create_index('ix_stored_files_status_created', 'stored_files', ['status', 'created_at'])
    op.create_index('ix_stored_files_purpose_entity', 'stored_files', ['purpose', 'entity_id'])
    for column in ('owner_user_id', 'organization_id', 'purpose', 'status'):
        op.create_index(f'ix_stored_files_{column}', 'stored_files', [column])


def downgrade():
    op.drop_table('stored_files')
