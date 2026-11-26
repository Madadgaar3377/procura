"""Outbox handlers for procurement lifecycle notifications (run only by the background worker)."""
from __future__ import annotations

from app.config import get_settings
from app.models import DeliveryNote, Invitation, Organization, PurchaseOrder, Quotation, RFQ, TaxInvoice
from app.modules.notifications.procurement import queue_in_app
from app.modules.notifications.service import enqueue_org_notice

EVENTS = {'purchase_order.issued', 'delivery_note.dispatched', 'goods_receipt.recorded', 'tax_invoice.submitted',
          'tax_invoice.reviewed', 'sla.reminder', 'document.share'}


def _notify(db, event, org_id: str, template: str, message: str, path: str, extra: dict | None = None) -> None:
    base = get_settings().frontend_url.rstrip('/')
    queue_in_app(db, event, org_id, template, message[:500])
    enqueue_org_notice(db, org_id=org_id, template=template,
                       context={'message': message, 'org_id': org_id, 'url': base + path, **(extra or {})},
                       source_key=f'{event.event}:{event.id}:{org_id}')


def handle(db, event) -> None:
    from app.worker import PermanentOutboxError
    payload = event.payload or {}
    if event.event == 'purchase_order.issued':
        po = db.get(PurchaseOrder, payload.get('purchase_order_id'))
        if not po:
            raise PermanentOutboxError('Purchase order no longer exists')
        buyer = db.get(Organization, po.buyer_org_id)
        _notify(db, event, po.supplier_org_id, 'purchase_order_issued',
                f'Purchase order {po.reference} for {po.total} {po.currency} was issued by {buyer.name if buyer else "the buyer"}.',
                f'/app/supplier/purchase-orders/{po.id}', {'reference': po.reference})
    elif event.event == 'delivery_note.dispatched':
        note = db.get(DeliveryNote, payload.get('delivery_note_id'))
        if not note:
            raise PermanentOutboxError('Delivery note no longer exists')
        _notify(db, event, note.buyer_org_id, 'delivery_dispatched',
                f'Delivery {note.reference} was dispatched. Confirm received quantities, shortages and damage.',
                f'/app/buyer/deliveries/{note.id}', {'reference': note.reference})
    elif event.event == 'goods_receipt.recorded':
        note = db.get(DeliveryNote, payload.get('delivery_note_id'))
        if not note:
            raise PermanentOutboxError('Delivery note no longer exists')
        _notify(db, event, note.supplier_org_id, 'goods_receipt_recorded',
                f'The buyer recorded receipt of delivery {note.reference} ({note.status.replace("_", " ")}).',
                f'/app/supplier/deliveries/{note.id}', {'reference': note.reference})
    elif event.event == 'tax_invoice.submitted':
        invoice = db.get(TaxInvoice, payload.get('invoice_id'))
        if not invoice:
            raise PermanentOutboxError('Invoice no longer exists')
        _notify(db, event, invoice.buyer_org_id, 'invoice_submitted',
                f'Invoice {invoice.invoice_number} for {invoice.total} {invoice.currency} was received. Three-way match: {invoice.match_status.replace("_", " ")}.',
                f'/app/buyer/invoices/{invoice.id}', {'reference': invoice.invoice_number})
    elif event.event == 'tax_invoice.reviewed':
        invoice = db.get(TaxInvoice, payload.get('invoice_id'))
        if not invoice:
            raise PermanentOutboxError('Invoice no longer exists')
        _notify(db, event, invoice.supplier_org_id, 'invoice_reviewed',
                f'Invoice {invoice.invoice_number} was {invoice.status} by the buyer.', f'/app/supplier/invoices/{invoice.id}',
                {'reference': invoice.invoice_number})
    elif event.event == 'sla.reminder':
        sla_reminder(db, event)
    elif event.event == 'document.share':
        share_document(db, event)


def share_document(db, event) -> None:
    """Render the client-format PDF and email it; the PDF is built before any network I/O."""
    from app.models import DeliveryNote, PurchaseOrder, Quotation, RFQ, TaxInvoice
    from app.modules.notifications.delivery import send_email
    from app.modules.procurement import documents
    from app.worker import PermanentOutboxError
    payload = event.payload
    if event.provider_message_id:
        return  # already accepted by SMTP; never resend
    kind = payload['kind']
    model, builder = {'rfq': (RFQ, documents.rfq_spec), 'quotation': (Quotation, documents.quotation_spec),
                      'purchase_order': (PurchaseOrder, documents.po_spec), 'delivery_note': (DeliveryNote, documents.delivery_spec),
                      'invoice': (TaxInvoice, documents.invoice_spec)}[kind]
    doc = db.get(model, payload['id'])
    if not doc:
        raise PermanentOutboxError('Shared document no longer exists')
    spec = builder(db, doc)
    pdf = documents.render(spec, lang=payload.get('lang', 'en'))
    title = spec['title'].title()
    subject = f"Procumaterial | {title} {spec.get('reference', '')}"
    plain = f"Please find attached {title} {spec.get('reference', '')}, shared through Procumaterial."
    html = '<p>' + plain + '</p>'
    delivery_id = event.id
    db.commit()
    event.provider_message_id = send_email(payload['address'], subject, plain, html, delivery_id,
                                           attachments=[(f"{title.replace(' ', '-')}-{spec.get('reference', '')}.pdf", pdf, 'application/pdf')])
    event.attempts += 1
    event.status = 'processed'
    event.delivery_status = 'accepted'


def sla_reminder(db, event) -> None:
    from app.modules.procurement.sla import MESSAGES, still_outstanding
    payload = event.payload
    rule, entity_id = payload['rule'], payload['entity_id']
    if not still_outstanding(db, rule, entity_id):
        event.status = 'skipped'
        event.delivery_status = 'action_completed'
        return
    ref, supplier, path = '', '', '/app'
    if rule in {'supplier_response', 'supplier_escalation'}:
        inv = db.get(Invitation, entity_id)
        rfq = db.get(RFQ, inv.rfq_id)
        ref = rfq.reference
        supplier = (db.get(Organization, inv.supplier_org_id) or Organization(name='Supplier')).name
        path = f'/app/buyer/rfqs/{rfq.id}' if rule == 'supplier_escalation' else '/app/supplier/inbox'
        if rule == 'supplier_response':
            # Reach category-scoped supplier contacts with a fresh magic link (imported suppliers may have no accounts).
            from app.modules.notifications.procurement import context_for
            from app.modules.notifications.recipients import enqueue as enqueue_rfq_notice
            from app.modules.procurement.portal import matched_item_ids
            enqueue_rfq_notice(db, org_id=inv.supplier_org_id, template='deadline_reminder',
                               context={**context_for(db, rfq), 'org_id': inv.supplier_org_id, 'invitation_id': inv.id,
                                        'matched_count': len(matched_item_ids(db, inv))},
                               source_key=f'sla-supplier:{inv.id}:{payload["n"]}')
    elif rule == 'quotation_approval':
        quote = db.get(Quotation, entity_id)
        ref = db.get(RFQ, quote.rfq_id).reference
        path = f'/app/supplier/quote/{quote.id}'
    elif rule == 'buyer_decision':
        rfq = db.get(RFQ, entity_id)
        ref, path = rfq.reference, f'/app/buyer/compare?rfq={rfq.id}'
    elif rule in {'po_issue', 'delivery_overdue'}:
        po = db.get(PurchaseOrder, entity_id)
        ref, path = po.reference, f'/app/{"buyer" if rule == "po_issue" else "supplier"}/purchase-orders/{po.id}'
    elif rule == 'invoice_review':
        invoice = db.get(TaxInvoice, entity_id)
        ref, path = invoice.invoice_number, f'/app/buyer/invoices/{invoice.id}'
    elif rule == 'goods_receipt':
        note = db.get(DeliveryNote, entity_id)
        ref, path = note.reference, f'/app/buyer/deliveries/{note.id}'
    message = MESSAGES[rule].format(ref=ref, supplier=supplier)
    _notify(db, event, payload['org_id'], 'sla_reminder', message, path, {'reference': ref})
    if rule == 'delivery_overdue' and payload.get('buyer_org_id'):
        _notify(db, event, payload['buyer_org_id'], 'sla_reminder', message, path.replace('/supplier/', '/buyer/'), {'reference': ref})
