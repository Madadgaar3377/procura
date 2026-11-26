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
    )
    op.create_index(
        'ix_capabilities_org_elig',
        'supplier_capabilities',
        ['org_id', 'eligibility_status', 'confirmed', 'accepts_rfqs'],
        unique=False,
    )
    op.create_index(
        'ix_pricebook_org_mat_active',
        'price_book_items',
        ['org_id', 'material_id', 'active', 'price_basis'],
        unique=False,
    )
    op.create_index(
        'ix_pricebook_mat_active_org',
        'price_book_items',
        ['material_id', 'active', 'org_id'],
        unique=False,
    )
    op.create_index(
        'ix_pricebook_sub_active_org',
        'price_book_items',
        ['subcategory_id', 'active', 'org_id'],
        unique=False,
    )
    op.create_index(
        'ix_rfq_matches_rfq_score',
        'rfq_matches',
        ['rfq_id', sa.text('score DESC'), 'supplier_org_id'],
        unique=False,
    )
    # ze41b760f892 owns ix_rfq_matches_rfq_status; do not drop it here.
    op.create_index(
        'ix_rfq_invitations_rfq_status_org',
        'rfq_invitations',
        ['rfq_id', 'status', 'supplier_org_id'],
        unique=False,
    )
    op.create_index(
        'ix_quotations_rfq_status_source',
        'quotations',
        ['rfq_id', 'status', 'submission_source'],
        unique=False,
    )

    if is_pg:
        # r18f4a932c70 already owns the material_ids GIN index.
        op.execute(sa.text(
            "CREATE INDEX ix_outbox_delivery_rfq_id "
            "ON outbox_events ((payload->'context'->>'rfq_id'), status, delivery_status) "
            "WHERE event = 'notification.delivery'"
        ))
        op.execute(sa.text(
            "CREATE INDEX ix_outbox_reminder_rfq_id "
            "ON outbox_events ((payload->>'rfq_id'), created_at) "
            "WHERE event = 'rfq.deadline.reminder'"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute(sa.text('DROP INDEX ix_outbox_reminder_rfq_id'))
        op.execute(sa.text('DROP INDEX ix_outbox_delivery_rfq_id'))

    for name, table in [
        ('ix_quotations_rfq_status_source', 'quotations'),
        ('ix_rfq_invitations_rfq_status_org', 'rfq_invitations'),
        ('ix_rfq_matches_rfq_score', 'rfq_matches'),
        ('ix_pricebook_sub_active_org', 'price_book_items'),
        ('ix_pricebook_mat_active_org', 'price_book_items'),
        ('ix_pricebook_org_mat_active', 'price_book_items'),
        ('ix_capabilities_org_elig', 'supplier_capabilities'),
        ('ix_capabilities_cat_elig_org', 'supplier_capabilities'),
    ]:
        op.drop_index(name, table_name=table)
