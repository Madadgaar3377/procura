"""Add composite and partial indexes for fast PostgreSQL RFQ matching, quotations, and outbox queries.

Revision ID: zj53f056e441
Revises: zi52e945d330
Create Date: 2026-11-26 08:45:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'zj53f056e441'
down_revision = 'zi52e945d330'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    op.create_index(
        'ix_capabilities_cat_elig_org',
        'supplier_capabilities',
        ['category_id', 'eligibility_status', 'confirmed', 'accepts_rfqs', 'org_id'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_capabilities_org_elig',
        'supplier_capabilities',
        ['org_id', 'eligibility_status', 'confirmed', 'accepts_rfqs'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_pricebook_org_mat_active',
        'price_book_items',
        ['org_id', 'material_id', 'active', 'price_basis'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_pricebook_mat_active_org',
        'price_book_items',
        ['material_id', 'active', 'org_id'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_pricebook_sub_active_org',
        'price_book_items',
        ['subcategory_id', 'active', 'org_id'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_rfq_matches_rfq_score',
        'rfq_matches',
        ['rfq_id', sa.text('score DESC'), 'supplier_org_id'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_rfq_matches_rfq_status',
        'rfq_matches',
        ['rfq_id', 'status'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_invitations_rfq_status_org',
        'invitations',
        ['rfq_id', 'status', 'supplier_org_id'],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        'ix_quotations_rfq_status_source',
        'quotations',
        ['rfq_id', 'status', 'submission_source'],
        unique=False,
        if_not_exists=True,
    )

    if is_pg:
        op.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS ix_outbox_delivery_rfq_id "
            "ON outbox_events ((payload->'context'->>'rfq_id'), status, delivery_status) "
            "WHERE event = 'notification.delivery'"
        ))
        op.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS ix_outbox_reminder_rfq_id "
            "ON outbox_events ((payload->>'rfq_id'), created_at) "
            "WHERE event = 'rfq.deadline.reminder'"
        ))
        op.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS ix_capabilities_material_ids_gin "
            "ON supplier_capabilities USING gin ((material_ids::jsonb))"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute(sa.text('DROP INDEX IF EXISTS ix_capabilities_material_ids_gin'))
        op.execute(sa.text('DROP INDEX IF EXISTS ix_outbox_reminder_rfq_id'))
        op.execute(sa.text('DROP INDEX IF EXISTS ix_outbox_delivery_rfq_id'))

    for name, table in [
        ('ix_quotations_rfq_status_source', 'quotations'),
        ('ix_invitations_rfq_status_org', 'invitations'),
        ('ix_rfq_matches_rfq_status', 'rfq_matches'),
        ('ix_rfq_matches_rfq_score', 'rfq_matches'),
        ('ix_pricebook_sub_active_org', 'price_book_items'),
        ('ix_pricebook_mat_active_org', 'price_book_items'),
        ('ix_pricebook_org_mat_active', 'price_book_items'),
        ('ix_capabilities_org_elig', 'supplier_capabilities'),
        ('ix_capabilities_cat_elig_org', 'supplier_capabilities'),
    ]:
        op.drop_index(name, table_name=table, if_exists=True)
