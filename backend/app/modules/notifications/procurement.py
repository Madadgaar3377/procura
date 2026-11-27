"""Procurement notification preparation shared by the active delivery worker."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app.config import get_settings
from app.models import RFQ, Capability, Category, Invitation, Organization, Outbox, Quotation, RFQItem
from app.modules.capabilities.policy import eligibility_predicates
from app.modules.notifications.service import enqueue_org_notice
from app.modules.notifications.recipients import enqueue as enqueue_rfq_notice


def context_for(db, rfq):
    base = get_settings().frontend_url.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password
        or parsed.query or parsed.fragment or (get_settings().is_production and
        (parsed.scheme != 'https' or parsed.hostname not in {'procumaterial.com', 'www.procumaterial.com'}))):
        raise ValueError("FRONTEND_URL must be an absolute application URL")
    pairs = db.execute(
        select(RFQItem.main_category_id, RFQItem.subcategory_id).where(RFQItem.rfq_id == rfq.id)
    ).all()
    ids = {value for pair in pairs for value in pair if value}
    names = dict(db.execute(select(Category.id, Category.name_en).where(Category.id.in_(ids))).all())
    categories = sorted(
        {names.get(main, "") + (" / " + names.get(sub, "") if sub else "") for main, sub in pairs}
    )
    deadline = rfq.response_deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    buyer=db.get(Organization,rfq.buyer_org_id)
    return {
        "reference": rfq.reference,
        "buyer": buyer.name if rfq.public_display_authorized and buyer else "Procumaterial buyer",
        "title": rfq.project_name or rfq.title,
        "delivery_location": rfq.city,
        "deadline": deadline.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "categories": ", ".join(categories),
        "url": base + "/app/supplier/rfqs/" + rfq.id,
        "rfq_id": rfq.id,
    }


def eligible_orgs(db, rfq, org_ids=None):
    from app.modules.matching.service import eligible_supplier_ids

    if org_ids is None:
        org_ids = db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq.id)).all()
    return eligible_supplier_ids(db, rfq, org_ids)


def pending_invitations(db, rfq, invitation_ids=None):
    """Invitations awaiting a response, from the persisted matching result.

    Active (invited/accepted, never cancelled or declined) invitations of suppliers that
    are currently matched, active and not suspended, with no submitted quotation. The
    buyer page, the reminder endpoint and the reminder worker all use this one query,
    so a supplier offered for a reminder is exactly a supplier the endpoint accepts.
    """
    from app.models import RFQMatch
    from app.modules.matching.service import MATCHED_STATUSES

    responded = select(Quotation.supplier_org_id).where(Quotation.rfq_id == rfq.id, Quotation.status != "draft")
    matched = select(RFQMatch.supplier_org_id).where(RFQMatch.rfq_id == rfq.id, RFQMatch.status.in_(MATCHED_STATUSES))
    query = (select(Invitation).join(Organization, Organization.id == Invitation.supplier_org_id).where(
        Invitation.rfq_id == rfq.id, Invitation.status.in_({"invited", "accepted"}),
        Invitation.supplier_org_id.not_in(responded), Invitation.supplier_org_id.in_(matched),
        Organization.is_active.is_(True), Organization.verified_status.not_in({"rejected", "suspended"})))
    if invitation_ids is not None:
        query = query.where(Invitation.id.in_(list(invitation_ids)))
    return db.scalars(query.order_by(Invitation.id)).all()


def pending_orgs(db, rfq):
    return [invitation.supplier_org_id for invitation in pending_invitations(db, rfq)]


def prepare(db, event):
    from app.worker import PermanentOutboxError

    rfq = db.get(RFQ, event.payload.get("rfq_id"))
    if not rfq:
        raise PermanentOutboxError("RFQ no longer exists")
    deadline = rfq.response_deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if rfq.status not in {"published", "collecting_quotes"} or deadline <= datetime.now(UTC):
        event.status = "skipped"
        event.delivery_status = "rfq_closed_or_expired"
        return
    context = context_for(db, rfq)
    if event.event == "rfq.invitation":
        invitation = db.get(Invitation, event.payload.get("invitation_id"))
        org = db.get(Organization, event.payload.get("supplier_org_id"))
        if not invitation or not org or invitation.rfq_id != rfq.id or invitation.supplier_org_id != org.id:
            raise PermanentOutboxError("Invalid invitation notification target")
        from app.modules.matching.service import supplier_eligibility

        reason = "invitation_declined: supplier declined this invitation" if invitation.status == "declined" \
            else supplier_eligibility(db, rfq, [org.id])[org.id]
        if reason:
            event.status = "skipped"
            event.delivery_status = "eligibility_review_required"
            event.payload = {**event.payload, "eligibility_reason": reason[:500]}
            return
        from app.modules.procurement.portal import matched_item_ids
        quote=db.scalar(select(Quotation).where(Quotation.invitation_id==invitation.id))
        from app.models import QuoteLine
        quoted=set(db.scalars(select(QuoteLine.rfq_item_id).where(QuoteLine.quotation_id==quote.id))) if quote else set()
        scope=matched_item_ids(db,invitation)
        missing_query=select(RFQItem.description).where(RFQItem.rfq_id==rfq.id,RFQItem.id.not_in(quoted))
        missing=db.scalars(missing_query.where(RFQItem.id.in_(scope)) if scope else missing_query).all()
        pricing_message=('Your quotation is prepared from your saved prices. Open the secure link, review and approve it.' if quote and quote.status=='draft' else
            'Your quotation has been submitted.' if quote else 'Open the secure link to enter your prices.')
        if missing:pricing_message+=' Prices needed for: '+', '.join(missing[:10])+(f' (+{len(missing)-10} more)' if len(missing)>10 else '')
        enqueue_rfq_notice(
            db,
            org_id=org.id,
            template="rfq_invitation",
            context={**context, "supplier_name": org.name, "org_id": org.id,
                     "invitation_id": invitation.id,"pricing_message":pricing_message,
                     "matched_count": len(matched_item_ids(db, invitation)) or len(missing)},
            source_key=f"rfq-invitation:{invitation.id}",
        )
    else:
        selected=event.payload.get('invitation_ids')
        targets=None if selected is None else set(db.scalars(select(Invitation.supplier_org_id).where(
            Invitation.rfq_id==rfq.id,Invitation.id.in_(selected))).all())
        for org_id in pending_orgs(db, rfq):
            if targets is not None and org_id not in targets:continue
            queue_in_app(db,event,org_id,'deadline_reminder',f'Procumaterial: {rfq.reference} response deadline is approaching.')
            enqueue_rfq_notice(
                db,
                org_id=org_id,
                template="deadline_reminder",
                context={**context, "org_id": org_id},
                source_key=f"rfq-reminder:{event.id}:{org_id}",
            )


def schedule_reminders(db, now):
    due = db.scalars(
        select(RFQ)
        .where(
            RFQ.status.in_({"published", "collecting_quotes"}),
            RFQ.response_deadline > now,
            RFQ.response_deadline <= now + timedelta(hours=24),
        )
        .with_for_update(skip_locked=True)
    ).all()
    queued = 0
    for rfq in due:
        key = f"rfq-deadline-24h:{rfq.id}"
        recent = db.scalar(
            select(Outbox.id)
            .where(
                Outbox.event == "rfq.deadline.reminder",
                Outbox.payload["rfq_id"].as_string() == rfq.id,
                Outbox.created_at > now - timedelta(hours=24),
            )
            .limit(1)
        )
        if recent or db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
            continue
        db.add(Outbox(event="rfq.deadline.reminder", payload={"rfq_id": rfq.id}, dedupe_key=key))
        queued += 1
    if queued:
        db.commit()
    return queued


def quotation_notice(db, event):
    from app.worker import PermanentOutboxError

    quote = db.get(Quotation, event.payload.get("quotation_id"))
    rfq = db.get(RFQ, event.payload.get("rfq_id"))
    if not quote or not rfq or quote.rfq_id != rfq.id or quote.status == "draft":
        raise PermanentOutboxError("Invalid quotation notification target")
    supplier = db.get(Organization, quote.supplier_org_id)
    supplier_name = supplier.name if supplier else "Supplier"
    terms = quote.commercial_terms or {}
    quotation_ref = terms.get("quotation_reference") or f"QT-{quote.id[:8].upper()}"
    base = get_settings().frontend_url.rstrip("/")
    for org_id, role, template in (
        (rfq.buyer_org_id, "buyer", "quotation_submitted"),
        (quote.supplier_org_id, "supplier", "quotation_confirmation"),
    ):
        queue_in_app(db, event, org_id, template, f'Procumaterial: {rfq.reference} quotation {quotation_ref} from {supplier_name}.')
        enqueue_org_notice(
            db,
            org_id=org_id,
            template=template,
            context={
                "reference": rfq.reference,
                "title": rfq.project_name or rfq.title,
                "quotation_id": quote.id,
                "quotation_reference": quotation_ref,
                "supplier_name": supplier_name,
                "submission_source": quote.submission_source,
                "attach_quotation_pdf": True,
                "org_id": org_id,
                "rfq_id": rfq.id,
                "url": base + f"/app/{role}/quote/{quote.id}",
            },
            source_key=f"quotation-submitted:{quote.id}:{role}",
        )


def queue_in_app(db, event, org_id, template, message):
    from app.models import AccountPreference, Member, Notification, User
    recipients=db.scalars(select(User.id).join(Member,Member.user_id==User.id).where(Member.organization_id==org_id,User.is_active.is_(True))).all()
    for user_id in recipients:
        prefs=db.get(AccountPreference,user_id)
        if prefs and (not prefs.in_app_enabled or not prefs.events.get(template,True)):continue
        existing=db.scalar(select(Notification.id).where(Notification.delivery_outbox_id==event.id,Notification.recipient_user_id==user_id,Notification.channel=='in_app',Notification.event==template))
        if not existing:
            db.add(Notification(recipient_user_id=user_id,event=template,message=message,channel='in_app',status='delivered',delivery_outbox_id=event.id))
    db.flush()


def company_notice(db, event):
    from app.worker import PermanentOutboxError
    org=db.get(Organization,event.payload.get('org_id'))
    if not org or not org.is_active:
        raise PermanentOutboxError('Organization notification target no longer exists')
    enqueue_org_notice(db,org_id=org.id,template='verification_updated',context={'org_id':org.id,'status':str(event.payload.get('status','updated'))},source_key=f'verification-update:{event.id}')
    queue_in_app(db,event,org.id,'verification_updated','Your company verification status was updated.')


def invitation_response_notice(db, event):
    from app.worker import PermanentOutboxError
    rfq = db.get(RFQ, event.payload.get('rfq_id'))
    inv = db.get(Invitation, event.payload.get('invitation_id'))
    supplier = db.get(Organization, event.payload.get('supplier_org_id'))
    action = str(event.payload.get('action') or '')
    if not rfq or not inv or not supplier or action not in {'accept', 'decline'}:
        raise PermanentOutboxError('Invalid invitation response notification target')
    template = 'rfq_invitation_accepted' if action == 'accept' else 'rfq_invitation_declined'
    base = get_settings().frontend_url.rstrip('/')
    queue_in_app(
        db, event, rfq.buyer_org_id, template,
        f'Procumaterial: {supplier.name} {action}ed invitation for {rfq.reference}.',
    )
    enqueue_org_notice(
        db,
        org_id=rfq.buyer_org_id,
        template=template,
        context={
            'reference': rfq.reference,
            'title': rfq.project_name or rfq.title,
            'supplier_name': supplier.name,
            'org_id': rfq.buyer_org_id,
            'url': f'{base}/app/buyer/rfqs/{rfq.id}',
        },
        source_key=f'rfq-invitation-responded:{inv.id}:{action}',
    )


def rfq_status_notice(db, event):
    from app.worker import PermanentOutboxError
    rfq = db.get(RFQ, event.payload.get('rfq_id'))
    if not rfq:
        raise PermanentOutboxError('RFQ no longer exists')
    status = str(event.payload.get('status') or rfq.status)
    base = get_settings().frontend_url.rstrip('/')
    supplier_org_ids = db.scalars(
        select(Invitation.supplier_org_id).where(
            Invitation.rfq_id == rfq.id,
            Invitation.status != 'declined',
        )
    ).all()
    for org_id in supplier_org_ids:
        queue_in_app(
            db, event, org_id, 'rfq_status_updated',
            f'Procumaterial: RFQ {rfq.reference} status is now {status}.',
        )
        enqueue_org_notice(
            db,
            org_id=org_id,
            template='rfq_status_updated',
            context={
                'reference': rfq.reference,
                'title': rfq.project_name or rfq.title,
                'status': status,
                'org_id': org_id,
                'url': f'{base}/app/supplier/rfqs/{rfq.id}',
            },
            source_key=f'rfq-status:{rfq.id}:{status}:{org_id}',
        )


def account_approved_notice(db, event):
    from app.models import AccountPreference, Notification, User
    from app.modules.notifications.service import queue_delivery
    from app.worker import PermanentOutboxError
    user = db.get(User, event.payload.get('user_id'))
    if not user or not user.is_active:
        raise PermanentOutboxError('Approved account target no longer exists')
    base = get_settings().frontend_url.rstrip('/')
    prefs = db.get(AccountPreference, user.id)
    if not prefs or (prefs.in_app_enabled and prefs.events.get('account_approved', True)):
        existing = db.scalar(
            select(Notification.id).where(
                Notification.delivery_outbox_id == event.id,
                Notification.recipient_user_id == user.id,
                Notification.channel == 'in_app',
                Notification.event == 'account_approved',
            )
        )
        if not existing:
            db.add(Notification(
                recipient_user_id=user.id,
                event='account_approved',
                message='Your Procumaterial account has been approved.',
                channel='in_app',
                status='delivered',
                delivery_outbox_id=event.id,
            ))
    context = {
        'url': f'{base}/app',
        'approval_method': str(event.payload.get('approval_method') or ''),
    }
    if user.email and user.email_verified and user.email_notifications_enabled:
        queue_delivery(
            db, user_id=user.id, channel='email', template='account_approved',
            context=context, dedupe_key=f'account-approved:{user.id}:email',
        )
    if user.phone_verified and user.sms_notifications_enabled:
        queue_delivery(
            db, user_id=user.id, channel='sms', template='account_approved',
            context=context, dedupe_key=f'account-approved:{user.id}:sms',
        )
