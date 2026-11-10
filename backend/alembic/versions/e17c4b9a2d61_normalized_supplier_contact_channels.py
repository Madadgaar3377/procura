"""Store multiple normalized contact channels per supplier contact."""
from alembic import op
import sqlalchemy as sa
import re

revision = 'e17c4b9a2d61'
down_revision = 'd5a8c2f19b30'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organization_contacts') as batch:
        batch.add_column(sa.Column('merged_into_contact_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_contact_merged_into', 'organization_contacts', ['merged_into_contact_id'], ['id'])
        batch.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_organization_contacts_merged_into_contact_id', 'organization_contacts', ['merged_into_contact_id'])
    op.create_index('ix_organization_contacts_deleted_at', 'organization_contacts', ['deleted_at'])
    op.create_table(
        'organization_contact_channels',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('contact_id', sa.String(36), sa.ForeignKey('organization_contacts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('org_id', sa.String(36), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('value', sa.String(300), nullable=False),
        sa.Column('normalized_value', sa.String(220), nullable=False),
        sa.Column('label', sa.String(20), nullable=False, server_default='other'),
        sa.Column('extension', sa.String(12), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('needs_review', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('source_value', sa.String(500), nullable=True),
        sa.Column('source_reference', sa.String(120), nullable=True),
        sa.UniqueConstraint('contact_id', 'kind', 'normalized_value', name='uq_contact_channel_identity'),
    )
    op.create_index('ix_organization_contact_channels_contact_id', 'organization_contact_channels', ['contact_id'])
    op.create_index('ix_organization_contact_channels_org_id', 'organization_contact_channels', ['org_id'])
    op.create_index('ix_contact_channel_org_kind_normalized', 'organization_contact_channels', ['org_id', 'kind', 'normalized_value'])
    op.create_table('organization_contact_import_sources',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('contact_id',sa.String(36),sa.ForeignKey('organization_contacts.id',ondelete='CASCADE'),nullable=False),
        sa.Column('org_id',sa.String(36),sa.ForeignKey('organizations.id',ondelete='CASCADE'),nullable=False),
        sa.Column('source_reference',sa.String(120),nullable=False),
        sa.Column('raw_name',sa.String(500),nullable=True),
        sa.Column('raw_email',sa.String(1000),nullable=True),
        sa.Column('raw_phone',sa.String(1000),nullable=True),
        sa.UniqueConstraint('contact_id','source_reference',name='uq_contact_import_source'))
    op.create_index('ix_organization_contact_import_sources_contact_id','organization_contact_import_sources',['contact_id'])
    op.create_index('ix_organization_contact_import_sources_org_id','organization_contact_import_sources',['org_id'])

    bind = op.get_bind()
    rows = bind.execute(sa.text('SELECT id, org_id, email, phone, phone_normalized, fax, raw_email, raw_phone, created_at FROM organization_contacts')).mappings()
    for row in rows:
        values = []
        if row['email']:
            value = row['email'].strip()
            if value:
                values.append(('email', value, value.casefold(), 'other', row['raw_email'], True))
        if row['phone']:
            value = row['phone'].strip()
            normalized = row['phone_normalized'] or re.sub(r'[^0-9+]', '', value)
            if value and normalized:
                values.append(('phone', value, normalized, 'other', row['raw_phone'], True))
        if row['fax']:
            value = row['fax'].strip()
            normalized = re.sub(r'[^0-9+]', '', value)
            if value and normalized:
                values.append(('fax', value, normalized, 'fax', row['raw_phone'], True))
        for kind, value, normalized, label, source, primary in values:
            bind.execute(sa.text('''INSERT INTO organization_contact_channels
                (id, created_at, contact_id, org_id, kind, value, normalized_value, label, is_primary, is_verified, needs_review, source_value)
                VALUES (:id,:created,:contact,:org,:kind,:value,:normalized,:label,:primary,false,:review,:source)
                ON CONFLICT (contact_id, kind, normalized_value) DO NOTHING'''), {
                'id': str(__import__('uuid').uuid4()), 'created': row['created_at'], 'contact': row['id'], 'org': row['org_id'],
                'kind': kind, 'value': value[:300], 'normalized': normalized[:220], 'label': label,
                'primary': primary, 'review': False, 'source': source[:500] if source else None,
            })


def downgrade():
    op.drop_index('ix_organization_contact_import_sources_org_id',table_name='organization_contact_import_sources')
    op.drop_index('ix_organization_contact_import_sources_contact_id',table_name='organization_contact_import_sources')
    op.drop_table('organization_contact_import_sources')
    op.drop_index('ix_organization_contacts_deleted_at', table_name='organization_contacts')
    op.drop_column('organization_contacts', 'deleted_at')
    op.drop_index('ix_contact_channel_org_kind_normalized', table_name='organization_contact_channels')
    op.drop_index('ix_organization_contact_channels_org_id', table_name='organization_contact_channels')
    op.drop_index('ix_organization_contact_channels_contact_id', table_name='organization_contact_channels')
    op.drop_table('organization_contact_channels')
    op.drop_index('ix_organization_contacts_merged_into_contact_id', table_name='organization_contacts')
    with op.batch_alter_table('organization_contacts') as batch:
        batch.drop_constraint('fk_contact_merged_into', type_='foreignkey')
        batch.drop_column('deleted_at')
        batch.drop_column('merged_into_contact_id')
