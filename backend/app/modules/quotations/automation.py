"""Rate-prefilled drafts and fail-closed, explicitly authorized submission."""
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import select,func,or_
from app.models import Invitation,Quotation,QuoteLine,RFQ,RFQItem,Material,PriceBookItem,Organization,Capability,Category,Member,User,Notification,Outbox
from app.modules.materials.service import normalize_text,normalize_unit
from app.modules.pricebooks.service import utc,category_pair
from app.security import audit
from app.modules.capabilities.policy import eligibility_predicates

def build_draft(db,invitation,*,basis='supply',automatic=False,allow_empty=True):
    """One draft per supplier per RFQ, pre-filled only from valid saved private prices.

    Only the supplier's matched lines are drafted (the portal still lets the
    supplier quote any other RFQ line). A line without a valid saved price is left
    pending supplier input; prices are never estimated or invented.
    """
    old=db.scalar(select(Quotation).where(Quotation.invitation_id==invitation.id))
    if old:return old
    items=db.scalars(select(RFQItem).where(RFQItem.rfq_id==invitation.rfq_id,RFQItem.is_active.is_(True)).order_by(RFQItem.created_at,RFQItem.id)).all()
    from app.modules.procurement.portal import matched_item_ids
    matched=set(matched_item_ids(db,invitation))
    if matched:items=[item for item in items if item.id in matched]
    materials={m.id:m for m in db.scalars(select(Material).where(Material.id.in_([i.material_id for i in items if i.material_id]))).all()}
    now=datetime.now(timezone.utc)
    valid_rate=(PriceBookItem.org_id==invitation.supplier_org_id,PriceBookItem.active.is_(True),PriceBookItem.price_basis==basis,or_(PriceBookItem.valid_until.is_(None),PriceBookItem.valid_until>now))
    rates=db.scalars(select(PriceBookItem).where(*valid_rate,PriceBookItem.material_id.in_(materials))).all() if materials else []
    by_material={r.material_id:r for r in rates}
    # Supplier-defined catalog products (no Material Master link) match on subcategory + description.
    custom_subs={i.subcategory_id for i in items if not i.material_id and i.subcategory_id}
    custom={}
    if custom_subs:
        for r in db.scalars(select(PriceBookItem).where(*valid_rate,PriceBookItem.material_id.is_(None),PriceBookItem.subcategory_id.in_(custom_subs))).all():
            custom[(r.subcategory_id,normalize_text(r.supplier_description))]=r
    reasons=[];eligible=[];currency=None
    for item in items:
        material=materials.get(item.material_id)
        rate=by_material.get(item.material_id) if material else custom.get((item.subcategory_id,normalize_text(item.description)))
        if material and not material.active:rate=None
        if not rate:reasons.append(f'Missing active price for item {item.id}: {item.description} (pending supplier input)');continue
        # A saved rate is only reused for the product the buyer asked for. A line linked to
        # a technically different Material Master record (e.g. a daylight floodlight for
        # "RGB Flood Light"), or a rate describing a different variant, is left for the
        # supplier to price manually.
        from app.modules.rfqs.material_matching import technical_conflict
        offered=rate.offered_description or rate.supplier_description or (material.name_en if material else '')
        # Material link: every critical qualifier of the buyer line must be met. Supplier rate
        # wording: only a contradiction (different variant, size or voltage) blocks it.
        conflict=(technical_conflict(item.description,material.name_en,item.specifications) if material else None) \
            or technical_conflict('',offered,f'{item.description}\n{item.specifications or ""}')
        if conflict:reasons.append(f'Saved price is for a technically different product for item {item.id}: {item.description} ({conflict}); pending supplier input');continue
        if rate.availability=='unavailable':reasons.append(f'Saved price is unavailable for item {item.id}');continue
        if rate.stock_quantity is not None and rate.stock_quantity < Decimal(item.quantity):
            reasons.append(f'Insufficient saved stock for item {item.id} (pending supplier confirmation)');continue
        if normalize_unit(item.unit)!=rate.unit or (material and normalize_unit(material.unit)!=rate.unit):reasons.append(f'Unit mismatch for item {item.id}');continue
        if material and material.version!=rate.material_version:reasons.append(f'Saved price needs review for changed Material Master item {item.id}');continue
        if rate.min_order_quantity and Decimal(item.quantity)<rate.min_order_quantity:reasons.append(f'Quantity below saved minimum for item {item.id}');continue
        from app.modules.matching.service import requested_brand, _product_description
        required_specs = _product_description(item.specifications)
        if required_specs and normalize_text(required_specs)!=normalize_text(_product_description(rate.offered_specs)):
            reasons.append(f'Specification mismatch for item {item.id}');continue
        brand_requirement = requested_brand(item)
        if brand_requirement and brand_requirement[2]:
            if normalize_text(rate.brand) not in brand_requirement[1]:
                reasons.append(f'Brand requirement mismatch for item {item.id}');continue
        if rate.unit_price<=0:reasons.append(f'Invalid saved price for item {item.id}');continue
        if currency and rate.currency!=currency:reasons.append(f'Currency mismatch for item {item.id}; no currency conversion authorized');continue
        names={normalize_text(v) for v in ((material.name_en,material.name_ar) if material else ())+(rate.offered_description,rate.supplier_description) if v}
        note='' if normalize_text(item.description) in names else 'Buyer wording differs from your saved product description; confirm the offered product.'
        if note:reasons.append(f'Confirm offered product for item {item.id}: {item.description}')
        currency=rate.currency
        eligible.append((item,material,rate,note))
    if not eligible and not allow_empty:return None
    quote=Quotation(invitation_id=invitation.id,rfq_id=invitation.rfq_id,supplier_org_id=invitation.supplier_org_id,price_basis=basis,submission_source='automatic_draft' if automatic else 'manual',review_required=True,review_reasons=[],rfq_revision=rfq.revision if (rfq:=db.get(RFQ,invitation.rfq_id)) else 0)
    db.add(quote);db.flush()
    quote.currency=currency or 'SAR'
    terms={}
    from app.serializers import record
    for item,material,rate,note in eligible:
        db.add(QuoteLine(quotation_id=quote.id,rfq_item_id=item.id,requested_snapshot=record(item),offered_description=rate.offered_description or rate.supplier_description or (material.name_en if material else item.description),brand=rate.brand,model=rate.model,offered_specs=rate.offered_specs,compliance='unconfirmed',quantity=item.quantity,unit_price=rate.unit_price,lead_days=rate.lead_days,note=note,source_rate_id=rate.id,source_rate_version=rate.version,source_rate_snapshot=record(rate),vat_rate=rate.vat_rate,warranty_months=rate.warranty_months,availability=rate.availability or 'unknown'))
        if rate.payment_terms and not terms.get('payment_terms'):terms['payment_terms']=rate.payment_terms
        if rate.valid_until and (not terms.get('validity_until') or rate.valid_until.isoformat()<terms['validity_until']):terms['validity_until']=rate.valid_until.isoformat()
    quote.commercial_terms=terms
    quote.review_reasons=reasons or ['Review offered specifications and confirm technical compliance before submitting']
    db.flush();return quote


def prepare_invitation_quote(db, invitation_id):
    """Prefill privately for every matched supplier; submission still needs authorization.

    Lock the invitation to serialize retries with manual draft creation. This is
    part of the invitation outbox transaction, so failures roll back and retry.
    """
    inv=db.scalar(select(Invitation).where(Invitation.id==invitation_id).with_for_update())
    if not inv or inv.status not in {'invited','accepted'}:return None
    rfq=db.get(RFQ,inv.rfq_id)
    if not rfq or rfq.status not in {'published','collecting_quotes'} or utc(rfq.response_deadline)<=datetime.now(timezone.utc):return None
    from app.modules.notifications.procurement import eligible_orgs
    if inv.supplier_org_id not in eligible_orgs(db,rfq,[inv.supplier_org_id]):return None
    quote=prepare_authorized_quote(db,invitation_id)
    if quote:return quote
    return build_draft(db,inv,automatic=True,allow_empty=False)


def refresh_automatic_draft(db, invitation, affected_ids):
    """Reprice only affected, still valid saved-rate lines of an unsubmitted draft."""
    from app.modules.matching.service import requested_brand, _product_description
    from app.modules.rfqs.material_matching import technical_conflict
    from app.serializers import record
    quote=db.scalar(select(Quotation).where(Quotation.invitation_id==invitation.id).with_for_update())
    if not quote or quote.status!='draft' or quote.submission_source!='automatic_draft':return quote
    rfq=db.get(RFQ,invitation.rfq_id)
    scope=set(invitation.matched_item_ids or [])
    items={item.id:item for item in db.scalars(select(RFQItem).where(
        RFQItem.rfq_id==rfq.id,RFQItem.is_active.is_(True),
        RFQItem.id.in_(set(affected_ids)&scope))).all()}
    old={line.rfq_item_id:line for line in db.scalars(select(QuoteLine).where(
        QuoteLine.quotation_id==quote.id,QuoteLine.rfq_item_id.in_(affected_ids))).all()}
    reasons=list(quote.review_reasons or [])
    now=datetime.now(timezone.utc)
    for item_id in affected_ids:
        item=items.get(item_id);line=old.get(item_id)
        if not item:
            if line:db.delete(line)
            continue
        material=db.get(Material,item.material_id) if item.material_id else None
        query=select(PriceBookItem).where(PriceBookItem.org_id==quote.supplier_org_id,
            PriceBookItem.active.is_(True),PriceBookItem.price_basis==quote.price_basis,
            or_(PriceBookItem.valid_until.is_(None),PriceBookItem.valid_until>now))
        if material:query=query.where(PriceBookItem.material_id==material.id)
        else:query=query.where(PriceBookItem.material_id.is_(None),
            PriceBookItem.subcategory_id==item.subcategory_id)
        rates=db.scalars(query.order_by(PriceBookItem.updated_at.desc())).all()
        rate=next((candidate for candidate in rates if material or
            normalize_text(candidate.supplier_description)==normalize_text(item.description)),None)
        offered=(rate.offered_description or rate.supplier_description or (material.name_en if material else '')) if rate else ''
        valid=bool(rate and rate.unit_price>0 and rate.technical_confirmed
            and rate.valid_until and utc(rate.valid_until)>now and rate.availability=='available'
            and (not material or (material.active and material.version==rate.material_version))
            and (rate.stock_quantity is None or rate.stock_quantity>=item.quantity)
            and (not rate.min_order_quantity or item.quantity>=rate.min_order_quantity)
            and normalize_unit(item.unit)==rate.unit
            and (not material or normalize_unit(material.unit)==rate.unit))
        if valid and material and technical_conflict(item.description,material.name_en,item.specifications):
            valid=False
        if rate and valid:
            valid=not technical_conflict('',offered,f'{item.description}\n{item.specifications or ""}')
            required_specs=_product_description(item.specifications)
            if required_specs and normalize_text(required_specs)!=normalize_text(_product_description(rate.offered_specs)):valid=False
            brand=requested_brand(item)
            if brand and brand[2] and normalize_text(rate.brand) not in brand[1]:valid=False
            if quote.currency!=rate.currency:valid=False
        if not valid:
            if line:db.delete(line)
            reasons.append(f'No valid saved rate for revised material {item.id}; supplier input required')
            continue
        if not line:
            line=QuoteLine(quotation_id=quote.id,rfq_item_id=item.id)
            db.add(line)
        line.requested_snapshot=record(item)
        line.quantity=item.quantity;line.unit_price=rate.unit_price
        line.offered_description=offered;line.offered_specs=rate.offered_specs
        line.brand=rate.brand;line.model=rate.model;line.compliance='unconfirmed'
        line.lead_days=rate.lead_days
        line.source_rate_id=rate.id;line.source_rate_version=rate.version
        line.source_rate_snapshot=record(rate);line.vat_rate=rate.vat_rate
        line.warranty_months=rate.warranty_months;line.availability=rate.availability or 'unknown'
    quote.rfq_revision=rfq.revision;quote.review_required=True
    quote.review_reasons=list(dict.fromkeys(reasons+[f'Review RFQ revision {rfq.revision} before submitting']))
    db.flush()
    return quote

def submit_record(db,quote,actor,*,automatic=False):
    quote.status='submitted';quote.submitted_at=datetime.now(timezone.utc);quote.review_required=False;quote.review_reasons=[];quote.submission_source='automatic' if automatic else 'manual'
    quote.rfq_revision=rfq.revision if (rfq:=db.get(RFQ,quote.rfq_id)) else quote.rfq_revision
    event=Outbox(event='quotation.submitted',payload={'rfq_id':quote.rfq_id,'quotation_id':quote.id,'quotation_revision':quote.revision},dedupe_key=f'quotation-submitted:{quote.id}:{quote.revision}')
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
    quote=build_draft(db,inv,basis=policy.get('price_basis','supply'),automatic=True,allow_empty=False)
    if not quote:return None
    quote.policy_revision=policy.get('revision')
    actor=db.get(User,policy.get('authorized_by'));reasons=[]
    if not actor or not actor.is_active or not actor.phone_verified or not db.scalar(select(Member.id).where(Member.user_id==actor.id,Member.organization_id==org.id,Member.role.in_({'owner','manager'}))):reasons.append('Automatic authorization owner no longer has permission')
    if not policy.get('confirm_terms') or not org.is_active or not org.is_supplier or org.verified_status!='verified':reasons.append('Company verification or authorization is no longer valid')
    if not policy.get('valid_until') or utc(datetime.fromisoformat(policy['valid_until']))<=datetime.now(timezone.utc):reasons.append('Automatic authorization expired')
    from app.modules.matching.service import city_covered_by
    if rfq.city and rfq.city.strip():
        if not any(city_covered_by(rfq.city, c)[0] for c in (policy.get('service_cities') or [])):
            reasons.append('RFQ service city is outside the authorized coverage')
    from app.modules.procurement.portal import matched_item_ids
    matched=set(matched_item_ids(db,inv))
    items=db.scalars(select(RFQItem).where(RFQItem.rfq_id==rfq.id,
        RFQItem.is_active.is_(True),RFQItem.id.in_(matched))).all() if matched else []
    lines={line.rfq_item_id:line for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()}
    materials={m.id:m for m in db.scalars(select(Material).where(Material.id.in_([i.material_id for i in items if i.material_id]))).all()}
    rates={r.id:r for r in db.scalars(select(PriceBookItem).where(PriceBookItem.id.in_([l.source_rate_id for l in lines.values() if l.source_rate_id]))).all()}
    all_caps=db.scalars(select(Capability).where(Capability.org_id==org.id,*eligibility_predicates())).all()
    caps=[c for c in all_caps if city_covered_by(rfq.city, c.city)[0]]
    required={q.strip().casefold() for q in (rfq.required_qualifications or [])}
    allowed={c.category_id for c in caps if required.issubset({q.strip().casefold() for q in (c.qualifications or [])})}
    for item in items:
        material=materials.get(item.material_id);line=lines.get(item.id);rate=rates.get(line.source_rate_id) if line else None
        if not material or not line or not rate:reasons.append('Some requested items have no valid compatible saved rate');continue
        main,sub=category_pair(db,material)
        if item.material_id not in policy.get('material_ids',[]) or (sub or main) not in allowed or item.main_category_id not in {None,main} or item.subcategory_id not in {None,sub}:reasons.append('Requested material or category is outside approved coverage')
        if not rate.technical_confirmed or rate.material_version!=material.version or not rate.valid_until or utc(rate.valid_until)<=datetime.now(timezone.utc):reasons.append('A saved rate requires technical or validity review')
        if rate.availability!='available':reasons.append('Saved rate availability requires supplier confirmation')
        if normalize_text(item.description)!=normalize_text(material.name_en) or item.specifications.strip() or (rate.offered_description and normalize_text(rate.offered_description)!=normalize_text(material.name_en)) or (rate.supplier_description and normalize_text(rate.supplier_description)!=normalize_text(material.name_en)):reasons.append('Buyer description or specifications require manual technical confirmation')
        if normalize_unit(item.unit)!=rate.unit or rate.unit!=normalize_unit(material.unit) or rate.price_basis!=policy.get('price_basis') or rate.currency!='SAR':reasons.append('Unit, currency or price basis mismatch')
        if Decimal(item.quantity)>Decimal(str(policy.get('max_quantity') or 0)):reasons.append('Requested quantity exceeds the authorized limit')
    from app.modules.quotations.router import invoice
    quote.vat_rate=Decimal(str(policy.get('vat_rate','0.15')))
    if Decimal(invoice(db,quote)['total'])>Decimal(str(policy.get('max_total') or 0)):reasons.append('Quotation total exceeds the authorized limit')
    if len(lines)!=len(items):reasons.append('Quotation coverage is incomplete')
    # A saved automation policy may prepare an offer, but the supplier must approve
    # this particular RFQ and its commercial terms before it is submitted.
    inv.status='accepted'
    if reasons:
        quote.review_reasons=list(dict.fromkeys(reasons));quote.review_required=True
        if actor and actor.is_active:
            db.add(Notification(recipient_user_id=actor.id,event='quotation.review_required',message=f'Manual quotation review required for {rfq.reference}',status='delivered'))
        audit(db,actor,'quotation.automatic_review_required',org.id,quote.id,reasons=quote.review_reasons,policy_revision=quote.policy_revision)
    elif policy.get('auto_submit'):
        submit_record(db,quote,actor,automatic=True)
    else:
        quote.review_required=True
        quote.review_reasons=['Supplier approval required before quotation submission']
        if actor and actor.is_active:
            db.add(Notification(recipient_user_id=actor.id,event='quotation.review_required',message=f'Quotation draft ready for approval on {rfq.reference}',status='delivered'))
    db.flush();return quote
