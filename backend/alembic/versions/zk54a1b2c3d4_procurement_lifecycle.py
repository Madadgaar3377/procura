"""Procurement lifecycle: magic links, purchase orders, delivery notes/receipts,
tax invoices with three-way matching, Document AI extractions, weighted scoring,
SLA configuration, and richer price-book / quotation commercial fields.

Purely additive: new tables, nullable or defaulted columns, and indexes.
No existing rows are modified or deleted.

Revision ID: zk54a1b2c3d4
Revises: zj53f056e441
Create Date: 2026-11-26 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'zk54a1b2c3d4'
down_revision = 'zj53f056e441'
branch_labels = None
depends_on = None


def _ts(name, nullable=True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _base():
    return [
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    ]


def _updated():
    return sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False)


def upgrade() -> None:
    # --- Additive columns on existing tables -------------------------------------
    with op.batch_alter_table('price_book_items') as b:
        b.add_column(sa.Column('warranty_months', sa.Integer(), nullable=True))
        b.add_column(sa.Column('payment_terms', sa.String(300), server_default='', nullable=False))
        b.add_column(sa.Column('stock_quantity', sa.Numeric(14, 3), nullable=True))
    with op.batch_alter_table('rfqs') as b:
        b.add_column(sa.Column('scoring_weights', sa.JSON(), nullable=True))
        b.add_column(sa.Column('delivery_address', sa.String(300), server_default='', nullable=False))
    with op.batch_alter_table('rfq_invitations') as b:
        b.add_column(sa.Column('matched_item_ids', sa.JSON(), nullable=True))
    with op.batch_alter_table('quotation_lines') as b:
        b.add_column(sa.Column('discount_percent', sa.Numeric(5, 2), server_default='0', nullable=False))
        b.add_column(sa.Column('warranty_months', sa.Integer(), nullable=True))
        b.add_column(sa.Column('availability', sa.String(20), server_default='unknown', nullable=False))
    with op.batch_alter_table('platform_settings') as b:
        b.add_column(sa.Column('sla_config', sa.JSON(), nullable=True))

    # --- Magic links -----------------------------------------------------------------
    op.create_table(
        'supplier_access_tokens', *_base(),
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('invitation_id', sa.String(36), sa.ForeignKey('rfq_invitations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rfq_id', sa.String(36), sa.ForeignKey('rfqs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('supplier_org_id', sa.String(36), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(12), nullable=False),
        sa.Column('destination_hash', sa.String(64), nullable=True),
        sa.Column('contact_id', sa.String(36), nullable=True),
        _ts('expires_at', False), _ts('revoked_at'), _ts('last_used_at'),
        sa.Column('use_count', sa.Integer(), server_default='0', nullable=False),
    )
    op.create_index('ix_supplier_access_tokens_token_hash', 'supplier_access_tokens', ['token_hash'], unique=True)
    op.create_index('ix_supplier_access_tokens_invitation', 'supplier_access_tokens', ['invitation_id', 'revoked_at'])
    op.create_index('ix_supplier_access_tokens_rfq_id', 'supplier_access_tokens', ['rfq_id'])
    op.create_index('ix_supplier_access_tokens_supplier_org_id', 'supplier_access_tokens', ['supplier_org_id'])

    # --- Purchase orders ---------------------------------------------------------------
    op.create_table(
        'purchase_orders', *_base(),
        sa.Column('reference', sa.String(40), nullable=False),
        sa.Column('rfq_id', sa.String(36), sa.ForeignKey('rfqs.id'), nullable=False),
        sa.Column('quotation_id', sa.String(36), sa.ForeignKey('quotations.id'), nullable=False),
        sa.Column('buyer_org_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('supplier_org_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('currency', sa.String(5), nullable=False),
        sa.Column('subtotal', sa.Numeric(16, 2), nullable=False),
        sa.Column('discount_total', sa.Numeric(16, 2), nullable=False),
        sa.Column('vat_total', sa.Numeric(16, 2), nullable=False),
        sa.Column('total', sa.Numeric(16, 2), nullable=False),
        sa.Column('delivery_address', sa.String(300), nullable=False),
        sa.Column('site_contact', sa.String(200), nullable=False),
        sa.Column('payment_terms', sa.Text(), nullable=False),
        sa.Column('delivery_terms', sa.Text(), nullable=False),
        sa.Column('special_instructions', sa.Text(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('quotation_reference', sa.String(100), nullable=False),
        sa.Column('required_delivery_date', sa.Date(), nullable=True),
        sa.Column('prepared_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('issued_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        _ts('issued_at'), _ts('acknowledged_at'), _updated(),
        sa.UniqueConstraint('quotation_id', name='uq_purchase_order_quotation'),
    )
    op.create_index('ix_purchase_orders_reference', 'purchase_orders', ['reference'], unique=True)
    op.create_index('ix_purchase_orders_rfq_id', 'purchase_orders', ['rfq_id'])
    op.create_index('ix_purchase_orders_buyer_status', 'purchase_orders', ['buyer_org_id', 'status', 'created_at'])
    op.create_index('ix_purchase_orders_supplier_status', 'purchase_orders', ['supplier_org_id', 'status', 'created_at'])

    op.create_table(
        'purchase_order_lines', *_base(),
        sa.Column('purchase_order_id', sa.String(36), sa.ForeignKey('purchase_orders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('rfq_item_id', sa.String(36), sa.ForeignKey('rfq_items.id'), nullable=True),
        sa.Column('quote_line_id', sa.String(36), sa.ForeignKey('quotation_lines.id'), nullable=True),
        sa.Column('material_id', sa.String(36), sa.ForeignKey('materials.id'), nullable=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('brand', sa.String(150), nullable=False),
        sa.Column('unit', sa.String(30), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit_price', sa.Numeric(14, 2), nullable=False),
        sa.Column('discount_percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('vat_rate', sa.Numeric(5, 4), nullable=False),
        sa.Column('line_total', sa.Numeric(16, 2), nullable=False),
        sa.UniqueConstraint('purchase_order_id', 'line_number', name='uq_po_line_number'),
    )
    op.create_index('ix_purchase_order_lines_purchase_order_id', 'purchase_order_lines', ['purchase_order_id'])

    # --- Delivery notes / goods receipts -------------------------------------------
    op.create_table(
        'delivery_notes', *_base(),
        sa.Column('reference', sa.String(40), nullable=False),
        sa.Column('purchase_order_id', sa.String(36), sa.ForeignKey('purchase_orders.id'), nullable=False),
        sa.Column('supplier_org_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('buyer_org_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('delivery_date', sa.Date(), nullable=True),
        sa.Column('vehicle_no', sa.String(60), nullable=False),
        sa.Column('driver_name', sa.String(150), nullable=False),
        sa.Column('carrier', sa.String(150), nullable=False),
        sa.Column('delivery_address', sa.String(300), nullable=False),
        sa.Column('supplier_remarks', sa.Text(), nullable=False),
        _ts('dispatched_at'), _ts('received_at'),
        sa.Column('received_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('received_by_name', sa.String(150), nullable=False),
        sa.Column('receipt_notes', sa.Text(), nullable=False),
        _updated(),
    )
    op.create_index('ix_delivery_notes_reference', 'delivery_notes', ['reference'], unique=True)
    op.create_index('ix_delivery_notes_po_status', 'delivery_notes', ['purchase_order_id', 'status'])
    op.create_index('ix_delivery_notes_supplier_org_id', 'delivery_notes', ['supplier_org_id'])
    op.create_index('ix_delivery_notes_buyer_org_id', 'delivery_notes', ['buyer_org_id'])

    op.create_table(
        'delivery_note_lines', *_base(),
        sa.Column('delivery_note_id', sa.String(36), sa.ForeignKey('delivery_notes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('po_line_id', sa.String(36), sa.ForeignKey('purchase_order_lines.id'), nullable=False),
        sa.Column('delivered_quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('remarks', sa.String(500), nullable=False),
        sa.Column('received_quantity', sa.Numeric(14, 3), nullable=True),
        sa.Column('damaged_quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('receipt_remarks', sa.String(500), nullable=False),
        sa.UniqueConstraint('delivery_note_id', 'po_line_id', name='uq_delivery_note_po_line'),
    )
    op.create_index('ix_delivery_note_lines_delivery_note_id', 'delivery_note_lines', ['delivery_note_id'])
    op.create_index('ix_delivery_note_lines_po_line_id', 'delivery_note_lines', ['po_line_id'])

    # --- Tax invoices ------------------------------------------------------------------
    op.create_table(
        'tax_invoices', *_base(),
        sa.Column('reference', sa.String(40), nullable=False),
        sa.Column('invoice_number', sa.String(60), nullable=False),
        sa.Column('purchase_order_id', sa.String(36), sa.ForeignKey('purchase_orders.id'), nullable=False),
        sa.Column('delivery_note_id', sa.String(36), sa.ForeignKey('delivery_notes.id'), nullable=True),
        sa.Column('supplier_org_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('buyer_org_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('currency', sa.String(5), nullable=False),
        sa.Column('subtotal', sa.Numeric(16, 2), nullable=False),
        sa.Column('discount_total', sa.Numeric(16, 2), nullable=False),
        sa.Column('vat_total', sa.Numeric(16, 2), nullable=False),
        sa.Column('total', sa.Numeric(16, 2), nullable=False),
        sa.Column('bank_name', sa.String(150), nullable=False),
        sa.Column('account_name', sa.String(150), nullable=False),
        sa.Column('iban', sa.String(40), nullable=False),
        sa.Column('swift', sa.String(20), nullable=False),
        sa.Column('payment_terms', sa.Text(), nullable=False),
        sa.Column('match_status', sa.String(30), nullable=False),
        sa.Column('match_details', sa.JSON(), nullable=False),
        _ts('matched_at'), _ts('submitted_at'),
        sa.Column('reviewed_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('review_notes', sa.Text(), nullable=False),
        _updated(),
        sa.UniqueConstraint('supplier_org_id', 'invoice_number', name='uq_tax_invoice_supplier_number'),
    )
    op.create_index('ix_tax_invoices_reference', 'tax_invoices', ['reference'], unique=True)
    op.create_index('ix_tax_invoices_po_status', 'tax_invoices', ['purchase_order_id', 'status'])
    op.create_index('ix_tax_invoices_buyer_status', 'tax_invoices', ['buyer_org_id', 'status'])
    op.create_index('ix_tax_invoices_supplier_org_id', 'tax_invoices', ['supplier_org_id'])
    op.create_index('ix_tax_invoices_buyer_org_id', 'tax_invoices', ['buyer_org_id'])
    op.create_index('ix_tax_invoices_match_status', 'tax_invoices', ['match_status'])

    op.create_table(
        'tax_invoice_lines', *_base(),
        sa.Column('invoice_id', sa.String(36), sa.ForeignKey('tax_invoices.id', ondelete='CASCADE'), nullable=False),
        sa.Column('po_line_id', sa.String(36), sa.ForeignKey('purchase_order_lines.id'), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('unit', sa.String(30), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit_price', sa.Numeric(14, 2), nullable=False),
        sa.Column('discount_percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('vat_rate', sa.Numeric(5, 4), nullable=False),
        sa.Column('line_total', sa.Numeric(16, 2), nullable=False),
        sa.UniqueConstraint('invoice_id', 'po_line_id', name='uq_invoice_po_line'),
    )
    op.create_index('ix_tax_invoice_lines_invoice_id', 'tax_invoice_lines', ['invoice_id'])
    op.create_index('ix_tax_invoice_lines_po_line_id', 'tax_invoice_lines', ['po_line_id'])

    # --- Document AI ---------------------------------------------------------------------
    op.create_table(
        'document_extractions', *_base(),
        sa.Column('org_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('rfq_id', sa.String(36), sa.ForeignKey('rfqs.id'), nullable=True),
        sa.Column('uploaded_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('file_id', sa.String(36), sa.ForeignKey('stored_files.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('method', sa.String(30), nullable=True),
        sa.Column('extracted', sa.JSON(), nullable=False),
        sa.Column('confirmed_data', sa.JSON(), nullable=True),
        sa.Column('confirmed_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        _ts('confirmed_at'),
        sa.Column('error', sa.String(500), nullable=True),
        _updated(),
    )
    op.create_index('ix_document_extractions_org_status', 'document_extractions', ['org_id', 'status', 'created_at'])
    op.create_index('ix_document_extractions_rfq_id', 'document_extractions', ['rfq_id'])

    op.create_index('ix_outbox_provider_message_id', 'outbox_events', ['provider_message_id'],
                    postgresql_where=sa.text('provider_message_id IS NOT NULL'), sqlite_where=sa.text('provider_message_id IS NOT NULL'))

    # Buyer tracking counts notification deliveries per RFQ without scanning the outbox.
    if op.get_bind().dialect.name == 'postgresql':
        # Must match the SQLAlchemy expression payload[('context','rfq_id')].as_string().
        op.execute("CREATE INDEX ix_outbox_delivery_rfq ON outbox_events ((CAST((payload #>> '{context, rfq_id}') AS VARCHAR))) "
                   "WHERE event = 'notification.delivery'")


def downgrade() -> None:
    op.drop_index('ix_outbox_provider_message_id', table_name='outbox_events')
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS ix_outbox_delivery_rfq')
    for table in ('document_extractions', 'tax_invoice_lines', 'tax_invoices', 'delivery_note_lines',
                  'delivery_notes', 'purchase_order_lines', 'purchase_orders', 'supplier_access_tokens'):
        op.drop_table(table)
    with op.batch_alter_table('platform_settings') as b:
        b.drop_column('sla_config')
    with op.batch_alter_table('quotation_lines') as b:
        b.drop_column('availability')
        b.drop_column('warranty_months')
        b.drop_column('discount_percent')
    with op.batch_alter_table('rfq_invitations') as b:
        b.drop_column('matched_item_ids')
    with op.batch_alter_table('rfqs') as b:
        b.drop_column('delivery_address')
        b.drop_column('scoring_weights')
    with op.batch_alter_table('price_book_items') as b:
        b.drop_column('stock_quantity')
        b.drop_column('payment_terms')
        b.drop_column('warranty_months')
