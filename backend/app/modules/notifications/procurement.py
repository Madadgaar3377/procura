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
    return {
        "reference": rfq.reference,
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


def pending_orgs(db, rfq):
    responded = select(Quotation.supplier_org_id).where(
        Quotation.rfq_id == rfq.id, Quotation.status != "draft"
    )
    ids = db.scalars(
        select(Invitation.supplier_org_id).where(
            Invitation.rfq_id == rfq.id,
            Invitation.status.in_({"invited", "accepted"}),
            Invitation.supplier_org_id.not_in(responded),
        )
    ).all()
    eligible = eligible_orgs(db, rfq, ids)
    return [org_id for org_id in ids if org_id in eligible]


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
        if org.id not in eligible_orgs(db, rfq, [org.id]) or invitation.status == "declined":
            event.status = "skipped"
            event.delivery_status = "eligibility_review_required"
            return
        enqueue_rfq_notice(
            db,
            org_id=org.id,
            template="rfq_invitation",
            context={**context, "supplier_name": org.name, "org_id": org.id,
                     "invitation_id": invitation.id},
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
    base = get_settings().frontend_url.rstrip("/")
    for org_id, role, template in (
        (rfq.buyer_org_id, "buyer", "quotation_submitted"),
        (quote.supplier_org_id, "supplier", "quotation_confirmation"),
    ):
        queue_in_app(db,event,org_id,template,f'Procumaterial: {rfq.reference} quotation update.')
        enqueue_org_notice(
            db,
            org_id=org_id,
            template=template,
            context={
                "reference": rfq.reference,
                "title": rfq.project_name or rfq.title,
                "org_id": org_id,
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
