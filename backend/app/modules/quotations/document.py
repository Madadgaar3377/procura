"""Layout-independent quotation data. No official PDF template is selected here."""
from app.models import Organization, RFQ, RFQItem
from sqlalchemy import select


def quotation_document(db,quote,lines):
    from app.modules.quotations.router import invoice
    rfq=db.get(RFQ,quote.rfq_id)
    buyer=db.get(Organization,rfq.buyer_org_id)
    supplier=db.get(Organization,quote.supplier_org_id)
    requested={item.id:item for item in db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id))}
    def company(org):
        return {'name':org.name,'city':org.city,'address':org.address,
            'cr_number':org.cr_number,'vat_number':org.vat_number,
            'email':org.business_email,'phone':org.business_phone}
    terms=quote.commercial_terms or {}
    return {'template_key':terms.get('template_key','pending_client_approval'),
        'supplier':company(supplier),'buyer':company(buyer),
        'quotation_reference':terms.get('quotation_reference') or quote.id,
        'rfq_reference':rfq.reference,'quotation_date':quote.created_at.isoformat(),
        'currency':quote.currency,'submission_source':quote.submission_source,
        'lines':[{'description':requested[line.rfq_item_id].description,
            'offered_description':line.offered_description,'brand':line.brand,'model':line.model,
            'specification':line.offered_specs,'quantity':str(line.quantity),
            'unit':requested[line.rfq_item_id].unit,'unit_price':str(line.unit_price),
            'subtotal':str(line.quantity*line.unit_price),'lead_days':line.lead_days,
            'vat_rate':str(line.vat_rate if line.vat_rate is not None else quote.vat_rate)} for line in lines],
        'totals':invoice(db,quote,lines),'commercial_terms':terms}
