"""Rate-prefilled drafts and fail-closed, explicitly authorized submission."""
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import select,func,or_
from app.models import Invitation,Quotation,QuoteLine,RFQ,RFQItem,Material,PriceBookItem,Organization,Capability,Category,Member,User,Notification,Outbox
from app.modules.materials.service import normalize_text,normalize_unit
from app.modules.pricebooks.service import utc,category_pair
from app.security import audit
from app.modules.capabilities.policy import eligibility_predicates

def build_draft(db,invitation,*,basis='supply',automatic=False):
    old=db.scalar(select(Quotation).where(Quotation.invitation_id==invitation.id))
    if old:return old
    items=db.scalars(select(RFQItem).where(RFQItem.rfq_id==invitation.rfq_id).order_by(RFQItem.created_at,RFQItem.id)).all()
    materials={m.id:m for m in db.scalars(select(Material).where(Material.id.in_([i.material_id for i in items if i.material_id]))).all()}
    now=datetime.now(timezone.utc)
    rates=db.scalars(select(PriceBookItem).where(PriceBookItem.org_id==invitation.supplier_org_id,PriceBookItem.material_id.in_(materials),PriceBookItem.active.is_(True),PriceBookItem.currency=='SAR',PriceBookItem.price_basis==basis,or_(PriceBookItem.valid_until.is_(None),PriceBookItem.valid_until>now))).all()
    by_material={r.material_id:r for r in rates}
    quote=Quotation(invitation_id=invitation.id,rfq_id=invitation.rfq_id,supplier_org_id=invitation.supplier_org_id,price_basis=basis,submission_source='automatic_draft' if automatic else 'manual',review_required=True,review_reasons=[])
    db.add(quote);db.flush();reasons=[]
    for item in items:
        material=materials.get(item.material_id);rate=by_material.get(item.material_id)
        if not material or not material.active or not rate:reasons.append(f'Missing active price for item {item.id}');continue
        if normalize_unit(item.unit)!=rate.unit or normalize_unit(material.unit)!=rate.unit:reasons.append(f'Unit mismatch for item {item.id}');continue
        if material.version!=rate.material_version:reasons.append(f'Saved price needs review for changed Material Master item {item.id}');continue
        db.add(QuoteLine(quotation_id=quote.id,rfq_item_id=item.id,offered_description=rate.offered_description or rate.supplier_description or material.name_en,brand=rate.brand,model=rate.model,offered_specs=rate.offered_specs,compliance='unconfirmed',quantity=item.quantity,unit_price=rate.unit_price,lead_days=rate.lead_days,source_rate_id=rate.id,source_rate_version=rate.version))
    quote.review_reasons=reasons or ['Review offered specifications and confirm technical compliance before submitting']
    db.flush();return quote

def submit_record(db,quote,actor,*,automatic=False):
    quote.status='submitted';quote.submitted_at=datetime.now(timezone.utc);quote.review_required=False;quote.review_reasons=[];quote.submission_source='automatic' if automatic else 'manual'
    event=Outbox(event='quotation.submitted',payload={'rfq_id':quote.rfq_id,'quotation_id':quote.id},dedupe_key=f'quotation-submitted:{quote.id}')
    db.add(event);db.flush()
    rfq=db.get(RFQ,quote.rfq_id)
    from app.modules.notifications.procurement import queue_in_app
    queue_in_app(db,event,rfq.buyer_org_id,'quotation_submitted',f'New quotation on {rfq.reference}')
    audit(db,actor,'quotation.submitted',quote.supplier_org_id,quote.id,source=quote.submission_source,policy_revision=quote.policy_revision)
    db.flush()

def prepare_authorized_quote(db,invitation_id):
    inv=db.scalar(select(Invitation).where(Invitation.id==invitation_id).with_for_update())
    if not inv or inv.status not in {'invited','accepted'}:return None
    org=db.scalar(select(Organization).where(Organization.id==inv.supplier_org_id).with_for_update())
    policy=(org.quote_automation or {}) if org else {}
    if not policy.get('enabled'):return None
    authorized_at=datetime.fromisoformat(policy['authorized_at'])
    if utc(inv.created_at)<utc(authorized_at):return None # Never retroactively submit older invitations.
    old=db.scalar(select(Quotation).where(Quotation.invitation_id==inv.id))
    if old:return old # Never overwrite or automatically submit an existing reviewed/manual draft.
    rfq=db.get(RFQ,inv.rfq_id)
    if not rfq or rfq.status not in {'published','collecting_quotes'} or utc(rfq.response_deadline)<=datetime.now(timezone.utc):return None
    quote=build_draft(db,inv,basis=policy.get('price_basis','supply'),automatic=True);quote.policy_revision=policy.get('revision')
    actor=db.get(User,policy.get('authorized_by'));reasons=[]
    if not actor or not actor.is_active or not actor.phone_verified or not db.scalar(select(Member.id).where(Member.user_id==actor.id,Member.organization_id==org.id,Member.role.in_({'owner','manager'}))):reasons.append('Automatic authorization owner no longer has permission')
    if not policy.get('confirm_terms') or not org.is_active or not org.is_supplier or org.verified_status!='verified':reasons.append('Company verification or authorization is no longer valid')
    if not policy.get('valid_until') or utc(datetime.fromisoformat(policy['valid_until']))<=datetime.now(timezone.utc):reasons.append('Automatic authorization expired')
    if rfq.city.casefold() not in {c.casefold() for c in policy.get('service_cities',[])}:reasons.append('RFQ service city is outside the authorized coverage')
    items=db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id)).all()
    lines={line.rfq_item_id:line for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()}
    materials={m.id:m for m in db.scalars(select(Material).where(Material.id.in_([i.material_id for i in items if i.material_id]))).all()}
    rates={r.id:r for r in db.scalars(select(PriceBookItem).where(PriceBookItem.id.in_([l.source_rate_id for l in lines.values() if l.source_rate_id]))).all()}
    caps=db.scalars(select(Capability).where(Capability.org_id==org.id,*eligibility_predicates(),func.lower(Capability.city)==rfq.city.casefold())).all()
    required={q.strip().casefold() for q in (rfq.required_qualifications or [])}
    allowed={c.category_id for c in caps if required.issubset({q.strip().casefold() for q in (c.qualifications or [])})}
    for item in items:
        material=materials.get(item.material_id);line=lines.get(item.id);rate=rates.get(line.source_rate_id) if line else None
        if not material or not line or not rate:reasons.append('Some requested items have no valid compatible saved rate');continue
        main,sub=category_pair(db,material)
        if item.material_id not in policy.get('material_ids',[]) or (sub or main) not in allowed or item.main_category_id not in {None,main} or item.subcategory_id not in {None,sub}:reasons.append('Requested material or category is outside approved coverage')
        if not rate.technical_confirmed or rate.material_version!=material.version or not rate.valid_until or utc(rate.valid_until)<=datetime.now(timezone.utc):reasons.append('A saved rate requires technical or validity review')
        if normalize_text(item.description)!=normalize_text(material.name_en) or item.specifications.strip() or (rate.offered_description and normalize_text(rate.offered_description)!=normalize_text(material.name_en)) or (rate.supplier_description and normalize_text(rate.supplier_description)!=normalize_text(material.name_en)):reasons.append('Buyer description or specifications require manual technical confirmation')
        if normalize_unit(item.unit)!=rate.unit or rate.unit!=normalize_unit(material.unit) or rate.price_basis!=policy.get('price_basis') or rate.currency!='SAR':reasons.append('Unit, currency or price basis mismatch')
        if Decimal(item.quantity)>Decimal(str(policy.get('max_quantity') or 0)):reasons.append('Requested quantity exceeds the authorized limit')
    from app.modules.quotations.router import invoice
    quote.vat_rate=Decimal(str(policy.get('vat_rate','0.15')))
    if Decimal(invoice(db,quote)['total'])>Decimal(str(policy.get('max_total') or 0)):reasons.append('Quotation total exceeds the authorized limit')
    if len(lines)!=len(items):reasons.append('Quotation coverage is incomplete')
    # The supplier authorized participation explicitly; submission still fails closed.
    inv.status='accepted'
    if reasons:
        quote.review_reasons=list(dict.fromkeys(reasons));quote.review_required=True
        for member in db.scalars(select(Member).where(Member.organization_id==org.id)).all():db.add(Notification(recipient_user_id=member.user_id,event='quotation.review_required',message=f'Manual quotation review required for {rfq.reference}',status='delivered'))
        audit(db,actor,'quotation.automatic_review_required',org.id,quote.id,reasons=quote.review_reasons,policy_revision=quote.policy_revision)
    else:
        for line in lines.values():line.compliance='compliant'
        submit_record(db,quote,actor,automatic=True)
    db.flush();return quote
