"""Delivery fixes layered on the original procurement worker; no RFQ/auth rewrite."""
import argparse
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select

from app import worker as core
from app.config import get_settings
from app.database import SessionLocal
from app.models import Member, Outbox, User
from app.modules.notifications.branded_templates import render_message
from app.modules.notifications.crypto import decrypt_delivery_secret
from app.modules.notifications.delivery import (
    DeliveryError,
    email_message_id,
    get_sms_status,
    send_email,
    send_sms,
)
from app.modules.notifications.providers import _mock_idempotency_id
from app.modules.notifications.service import update_delivery_notification

log=logging.getLogger("uvicorn.error")
PROCUREMENT_EVENTS={'purchase_order.issued','delivery_note.dispatched','goods_receipt.recorded','tax_invoice.submitted',
                    'tax_invoice.reviewed','sla.reminder','document.share'}


SMS_FINAL_FAILURE={'Failed','Cancelled','Expired'}


def sms_status_delay(checks):
    """Delayed reconciliation: 30 s, 1, 2, 4, 8, 16 min, then every 30 min.

    A gateway answers HTTP 202 with state Pending until the Android device fetches the
    message; a phone that is offline or asleep can hold it for minutes or hours. Fixed
    30-second polling exhausted its 24 lookups in 12 minutes and then declared the
    message uncertain while it was still queued for the device.
    """
    return min(1800,30*2**max(0,min(checks,6)))


def apply_sms(db,event,result,now):
    state=str(result.get('state') or 'Unknown');event.delivery_status=state;event.provider_message_id=result['id'];event.last_error=None
    payload={**event.payload}
    if result.get('http_status') is not None:
        key='provider_status_response_code' if result.get('lookup') else 'provider_response_code'
        payload[key]=result['http_status']
    payload.setdefault('sms_submitted_at',now.isoformat())
    if result.get('reason'):payload['provider_reason']=str(result['reason'])[:300]
    if result.get('source'):payload['provider_status_source']=result['source']
    event.payload=payload
    if state=='Delivered':event.status='processed';event.next_attempt_at=None;notice='delivered'
    elif state in SMS_FINAL_FAILURE:
        event.status='failed';event.next_attempt_at=None
        event.last_error='Provider reported delivery failure'+(f": {payload['provider_reason']}" if payload.get('provider_reason') else '');notice='failed'
    elif event.status_checks>=get_settings().delivery_status_max_polls:
        # Bounded: never resubmitted automatically. Sent = handed to the mobile network.
        event.status='processed' if state=='Sent' else 'uncertain';event.next_attempt_at=None
        event.last_error=('Provider reported Sent; no delivery report received' if state=='Sent' else
                          f'Provider still reports {state} after {event.status_checks} status checks; '
                          'check the gateway device is online, then reconcile before any manual resend')
        notice='sent' if state=='Sent' else 'uncertain'
    else:
        event.status='awaiting_status';event.next_attempt_at=now+timedelta(seconds=sms_status_delay(event.status_checks))
        notice='sent' if state=='Sent' else 'accepted'
    update_delivery_notification(db,event.id,notice)


def deliver(db,event,now):
    payload=event.payload;channel=payload.get('channel');user=db.get(User,payload['user_id']) if payload.get('user_id') else None
    if payload.get('procurement_recipient') and channel in {'sms','email','whatsapp'}:
        return deliver_procurement(db,event,now)
    if channel not in {'sms','email'}:raise core.PermanentOutboxError('Unsupported notification channel')
    # Reconcile accepted SMS even after a code expires or its destination changes.
    # Polling never submits another message and must not be gated by OTP validity.
    if channel=='sms' and event.provider_message_id and event.delivery_status not in {'submitting','unknown',None}:
        provider_id=event.provider_message_id
        db.commit()
        result=get_sms_status(provider_id)
        event.status_checks+=1;apply_sms(db,event,result,now);return
    if not user or not user.is_active:
        event.status='skipped';event.delivery_status='recipient_inactive';event.secret_ciphertext=None;return
    template=payload.get('template','');authentication=template in {'phone_change_otp','phone_otp','email_otp','password_reset','password_changed','phone_changed'}
    context=payload.get('context') or {}
    if not authentication:
        from app.models import AccountPreference
        prefs=db.get(AccountPreference,user.id)
        if prefs and not prefs.events.get(template,True):
            event.status='skipped';event.delivery_status='preference_disabled';return
    if not authentication and context.get('org_id'):
        member=db.scalar(select(Member.id).where(Member.user_id==user.id,Member.organization_id==context['org_id']))
        if not member:
            event.status='skipped';event.delivery_status='membership_removed';return
    if template in {'rfq_invitation','deadline_reminder'} and context.get('rfq_id'):
        from app.models import RFQ
        from app.modules.notifications.procurement import eligible_orgs, pending_orgs
        rfq=db.get(RFQ,context['rfq_id'])
        deadline=core._utc(rfq.response_deadline) if rfq else now
        eligible=eligible_orgs(db,rfq) if rfq and template=='rfq_invitation' else pending_orgs(db,rfq) if rfq else []
        if not rfq or rfq.status not in {'published','collecting_quotes'} or deadline<=now or context.get('org_id') not in eligible:
            event.status='skipped';event.delivery_status='invitation_no_longer_eligible';return
    recipient=user.phone_e164 if channel=='sms' else user.email
    if template=='phone_change_otp':
        from app.models import PhoneOTP
        from app.security import credential_stamp
        challenge=db.get(PhoneOTP,context.get('otp_id'))
        if not challenge or challenge.user_id!=user.id or challenge.purpose!='phone_change' or challenge.previous_phone!=user.phone_e164 or challenge.credential_hash!=credential_stamp(user) or challenge.attempts>=5:
            event.status='skipped';event.delivery_status='phone_change_invalidated';event.secret_ciphertext=None;return
        recipient=challenge.phone
    if template=='phone_changed':
        from app.models import PhoneOTP
        change=db.get(PhoneOTP,context.get('change_id'))
        if not change or not change.used or change.user_id!=user.id or change.purpose!='phone_change':
            event.status='skipped';event.delivery_status='phone_change_invalidated';return
        recipient=change.previous_phone if channel=='sms' else user.email if user.email_verified else None
    from app.identity import normalize_email, normalize_phone
    if recipient:
        recipient=normalize_phone(recipient) if channel=='sms' else normalize_email(recipient)
    if authentication and (not context.get('recipient') or context['recipient']!=recipient):
        event.status='skipped';event.delivery_status='recipient_changed';event.secret_ciphertext=None;return
    if template in {'password_reset','password_changed'}:
        verified=user.phone_verified if channel=='sms' else user.email_verified
        if not verified or (context.get('recipient') and context['recipient']!=recipient):
            event.status='skipped';event.delivery_status='recovery_destination_changed';event.secret_ciphertext=None;return
    if template=='password_reset':
        from app.models import EmailOTP
        from app.modules.auth.router import valid_recovery
        otp=db.get(EmailOTP,context.get('otp_id'))
        if not valid_recovery(otp,user,now) or otp.purpose!='password_reset':
            event.status='skipped';event.delivery_status='recovery_destination_changed';event.secret_ciphertext=None;return
    consent=user.phone_verified and user.sms_notifications_enabled if channel=='sms' else user.email_verified and user.email_notifications_enabled
    if not recipient or (not authentication and not consent):
        event.status='skipped';event.delivery_status='recipient_not_verified_or_opted_in';return
    ttl=core._remaining_code_ttl(db,'phone_otp' if template=='phone_change_otp' else template,payload.get('context') or {},now) if authentication else 3600
    if template in {'phone_otp','email_otp','password_reset'}:
        from app.models import EmailOTP, PhoneOTP
        code=db.get(PhoneOTP if template=='phone_otp' else EmailOTP,context.get('otp_id'))
        expected={'phone_otp':'registration','email_otp':'verification','password_reset':'password_reset'}[template]
        if not code or code.attempts>=5 or code.purpose!=expected:
            ttl=None
    if template in {'phone_otp','email_otp'}:
        from app.models import EmailOTP, PhoneOTP
        code=db.get(PhoneOTP if template=='phone_otp' else EmailOTP,context.get('otp_id'))
        if not code or code.attempts>=5:ttl=None
    if ttl is None:
        event.status='skipped';event.delivery_status='code_expired';event.secret_ciphertext=None;return
    if channel=='sms' and event.provider_message_id:
        provider_id=event.provider_message_id
        db.commit()
        try:result=get_sms_status(provider_id)
        except DeliveryError as exc:
            if getattr(exc,'status_code',None)!=404 or event.delivery_status not in {'submitting','unknown'}:raise
        else:event.status_checks+=1;apply_sms(db,event,result,now);return
    if channel=='email' and event.delivery_status=='submitting':
        event.status='uncertain';event.last_error='SMTP outcome unknown; reconcile with mailbox or SMTP logs before retry';return
    secret=decrypt_delivery_secret(event.secret_ciphertext) if event.secret_ciphertext else None
    render_context={**context,'language':user.notification_language,'channel':channel,'minutes':max(1,(ttl+59)//60)}
    if template=='password_reset':
        import json
        recovery=json.loads(secret)
        secret=recovery['code']
    if template=='password_reset' and channel=='email':
        from urllib.parse import urlsplit

        base=get_settings().frontend_url.rstrip('/');parsed=urlsplit(base)
        if parsed.scheme not in {'https','http'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or (get_settings().is_production and parsed.scheme!='https'):
            raise core.PermanentOutboxError('FRONTEND_URL must be a secure application URL')
        render_context['url']=base+'/reset-password#token='+recovery['link_token']
    subject,plain,html=render_message(template,render_context,secret)
    email_attachments=None
    if channel=='email' and context.get('attach_quotation_pdf') and context.get('quotation_id'):
        try:
            from app.models import Quotation, QuoteLine
            from app.modules.quotations.document import build_quotation_pdf, quotation_document
            quote=db.get(Quotation,context['quotation_id'])
            if quote and quote.status!='draft':
                q_lines=db.scalars(select(QuoteLine).where(QuoteLine.quotation_id==quote.id)).all()
                doc=quotation_document(db,quote,q_lines)
                pdf_bytes=build_quotation_pdf(doc,lang=user.notification_language or 'en')
                safe_ref=''.join(ch if ch.isalnum() or ch in {'-','_'} else '_' for ch in str(doc.get('quotation_reference') or quote.id))
                email_attachments=[(f'Quotation-{safe_ref}.pdf',pdf_bytes,'application/pdf')]
        except Exception as exc:  # noqa: BLE001 - never block email delivery if PDF fails
            log.warning('Quotation PDF attachment skipped quote_id=%s (%s)',context.get('quotation_id'),type(exc).__name__)
    event.delivery_status='submitting';event.provider_message_id=_mock_idempotency_id(event.id) if channel=='sms' else email_message_id(event.id)
    delivery_id=event.id
    update_delivery_notification(db,delivery_id,'sending');db.commit()
    if channel=='sms':
        result=send_sms(recipient,plain,delivery_id,ttl_seconds=ttl);event.attempts+=1;apply_sms(db,event,result,now)
    else:
        event.provider_message_id=(send_email(recipient,subject,plain,html,delivery_id,attachments=email_attachments)
            if email_attachments else send_email(recipient,subject,plain,html,delivery_id));event.attempts+=1
        event.delivery_status='accepted';event.status='processed';event.next_attempt_at=None
        event.payload={**event.payload,'smtp_accepted':True}
        update_delivery_notification(db,event.id,'accepted')
    event.secret_ciphertext=None


def deliver_procurement(db, event, now):
    """Deliver an authorized RFQ destination without requiring a user or OTP."""
    from app.models import Invitation, RFQ
    from app.modules.notifications.procurement import eligible_orgs
    from app.modules.notifications.recipients import current_recipient, restriction
    payload=event.payload;context=payload['context'];channel=payload['channel']
    # Once submitted, reconcile only. Never resubmit because authorization changed.
    if channel=='sms' and event.provider_message_id:
        provider_id=event.provider_message_id
        db.commit()
        try:
            result=get_sms_status(provider_id)
        except DeliveryError as exc:
            if getattr(exc,'status_code',None)==404 and event.delivery_status in {'submitting','unknown'}:
                event.status='uncertain';event.next_attempt_at=None
                event.last_error='SMS outcome unknown; reconcile with provider before resending'
                return
            raise
        event.status_checks+=1;apply_sms(db,event,result,now);return
    if event.delivery_status=='submitting':
        event.status='uncertain';event.next_attempt_at=None
        event.last_error='SMTP outcome unknown; reconcile with SMTP logs before retry'
        return
    rfq=db.get(RFQ,context['rfq_id'])
    invitation=db.scalar(select(Invitation).where(Invitation.rfq_id==context['rfq_id'],
        Invitation.supplier_org_id==context['org_id']))
    reason=restriction(db,channel,payload['destination'])
    if not reason and (not rfq or rfq.status not in {'published','collecting_quotes'} or
        core._utc(rfq.response_deadline)<=now or not invitation or invitation.status=='declined' or
        context['org_id'] not in eligible_orgs(db,rfq,[context['org_id']])):
        reason='invitation_no_longer_eligible'
    if not reason and not current_recipient(db,payload):reason='authorization_changed'
    if not reason and channel in {'sms','whatsapp'} and payload['template']=='rfq_invitation':
        # One invitation SMS per destination per RFQ and supplier, whatever re-queued it
        # (rematch, reinstated invitation, recovery job). Reminders have their own cooldown.
        earlier=db.scalar(select(Outbox.id).where(
            Outbox.id!=event.id,Outbox.event=='notification.delivery',
            Outbox.status.in_({'awaiting_status','processed','uncertain'}),Outbox.provider_message_id.is_not(None),
            Outbox.payload['channel'].as_string()==channel,Outbox.payload['template'].as_string()=='rfq_invitation',
            Outbox.payload['destination'].as_string()==payload['destination'],
            Outbox.payload['context']['rfq_id'].as_string()==context['rfq_id'],
            Outbox.payload['context']['org_id'].as_string()==context['org_id']).limit(1))
        if earlier:
            reason='duplicate_suppressed'
            event.payload={**payload,'duplicate_of':earlier}
    if reason:
        event.status='skipped';event.delivery_status=reason;return
    if channel=='email' and get_settings().is_production and not get_settings().smtp_reply_to:
        raise core.PermanentOutboxError('Configure a monitored SMTP_REPLY_TO before sending RFQ email')
    settings=get_settings()
    if channel=='whatsapp':
        from app.modules.procurement.whatsapp import configured
        if not configured():
            event.status='skipped';event.delivery_status='whatsapp_not_configured'
            event.last_error='WhatsApp credentials are not configured (WHATSAPP_ENABLED, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID)'
            return
    # Controlled test recipients: redirect every external RFQ message when configured.
    destination=payload['destination']
    redirect=settings.notification_redirect_email if channel=='email' else settings.notification_redirect_phone
    if redirect:
        destination=redirect
        event.payload={**payload,'redirected_to_test_recipient':True}
    # Passwordless, revocable magic link scoped to this supplier and RFQ invitation.
    from app.modules.procurement.portal import issue_link, portal_url
    link=portal_url(issue_link(db,invitation,rfq,channel=channel,destination=payload['destination'],contact_id=payload.get('contact_id'),now=now))
    subject,plain,html=render_message(payload['template'],{**context,'channel':'sms' if channel=='whatsapp' else channel,'url':link})
    event.payload={**event.payload,'last_attempt_at':now.isoformat()}
    event.delivery_status='submitting'
    event.provider_message_id=_mock_idempotency_id(event.id) if channel in {'sms','whatsapp'} else email_message_id(event.id)
    delivery_id=event.id
    db.commit()
    if channel=='whatsapp':
        from app.modules.procurement.whatsapp import send_template
        event.provider_message_id=send_template(destination,[rfq.reference,str(context.get('buyer') or 'Procumaterial buyer'),
            str(context.get('matched_count') or len(context.get('materials') or [])),str(context.get('deadline') or ''),link],delivery_id)
        event.attempts+=1;event.status='processed';event.delivery_status='accepted';event.next_attempt_at=None
        update_delivery_notification(db,event.id,'accepted')
        return
    payload={**payload,'destination':destination}
    if channel=='sms':
        result=send_sms(payload['destination'],plain,delivery_id)
        event.attempts+=1;apply_sms(db,event,result,now)
    else:
        event.provider_message_id=send_email(payload['destination'],subject,plain,html,delivery_id)
        event.attempts+=1;event.delivery_status='accepted';event.status='processed';event.next_attempt_at=None
        event.payload={**event.payload,'smtp_accepted':not get_settings().mock_notifications}
    if get_settings().mock_notifications:
        event.delivery_status='simulated';event.status='processed';event.next_attempt_at=None


CONTACT_EVENTS=('contact.inquiry.support','contact.inquiry.ack')
# Worker lanes over the one durable outbox table. Each lane runs as its own process
# (systemd procura-worker@<lane>), so a slow SMTP server or SMS gateway can only delay
# its own lane, never RFQ matching or invitation preparation. 'all' is the legacy
# single-process mode and remains the default.
LANES=('all','matching','email','sms','quotations','events')


def lane_filter(lane):
    channel=func.coalesce(Outbox.payload['channel'].as_string(),'')
    delivery=Outbox.event=='notification.delivery'
    parts={
        'matching':Outbox.event=='rfq.published',
        'email':or_(and_(delivery,channel=='email'),Outbox.event.in_(CONTACT_EVENTS)),
        'sms':and_(delivery,channel.in_(('sms','whatsapp'))),
        'quotations':Outbox.event=='quotation.auto_prepare',
    }
    if lane=='all':return None
    if lane=='events':
        return and_(*(~condition for condition in parts.values()))
    if lane not in parts:raise ValueError(f'Unknown worker lane {lane!r}; use one of {", ".join(LANES)}')
    return parts[lane]


# Email provider circuit breaker (per worker process). A provider-level rejection
# (authentication, sender address, policy/spam, sending quota) is identical for every
# queued message, so the lane pauses instead of submitting each one into the same
# rejection. Paused messages are deferred without consuming delivery attempts, up to
# delivery_max_deferrals; then they fail with the stored provider reason.
_EMAIL_PAUSE={'until':None,'reason':None,'data_rejections':0}


def email_paused(now):
    until=_EMAIL_PAUSE['until']
    return until is not None and now<until


def record_email_outcome(exc,now):
    if exc is None:
        _EMAIL_PAUSE['data_rejections']=0
        return
    detail=getattr(exc,'provider_response',None)
    if not detail:return
    if detail.get('stage')=='data' and detail.get('permanent'):
        _EMAIL_PAUSE['data_rejections']+=1
    if detail.get('provider_level') or _EMAIL_PAUSE['data_rejections']>=3:
        _EMAIL_PAUSE['until']=now+timedelta(seconds=get_settings().smtp_failure_pause_seconds)
        _EMAIL_PAUSE['reason']=str(exc)[:500]
        log.error('Email lane paused until %s after provider-level SMTP rejection: %s',
                  _EMAIL_PAUSE['until'].isoformat(),_EMAIL_PAUSE['reason'])


def is_email_event(event):
    return (event.event=='notification.delivery' and event.payload.get('channel')=='email') or event.event in CONTACT_EVENTS


def defer_paused_email(event,now):
    """Hold an unsent email while the provider rejects the account; bounded."""
    deferrals=int(event.payload.get('provider_pause_deferrals') or 0)+1
    event.payload={**event.payload,'provider_pause_deferrals':deferrals}
    event.lock_until=None
    if deferrals>get_settings().delivery_max_deferrals:
        event.status='failed';event.next_attempt_at=None
        event.last_error=('Email provider kept rejecting the sending account; not submitted. '+(_EMAIL_PAUSE['reason'] or ''))[:500]
    else:
        event.status='retry';event.next_attempt_at=_EMAIL_PAUSE['until']
        event.last_error=('Deferred: email provider paused after rejection. '+(_EMAIL_PAUSE['reason'] or ''))[:500]


def prepare_invitation_with_isolated_quote(db,event):
    """Invitation notice first-class; the automatic quotation draft cannot block it.

    The draft is attempted in a savepoint (local database work only). If it fails, the
    invitation notice still goes out and a separate quotation.auto_prepare job retries the
    draft in the quotations lane.
    """
    from app.modules.notifications.procurement import prepare
    invitation_id=event.payload.get('invitation_id')
    if event.event=='rfq.invitation' and invitation_id:
        from app.modules.quotations.automation import prepare_invitation_quote
        try:
            with db.begin_nested():
                prepare_invitation_quote(db,invitation_id)
        except Exception as exc:  # noqa: BLE001 - quotation drafting must not block the invitation
            log.warning('Automatic quotation draft deferred invitation=%s (%s)',invitation_id,type(exc).__name__)
            key=f'quotation-auto-prepare:{invitation_id}'
            if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key==key)):
                db.add(Outbox(event='quotation.auto_prepare',dedupe_key=key,
                              payload={'invitation_id':invitation_id,'rfq_id':event.payload.get('rfq_id'),
                                       'first_error':type(exc).__name__}))
    prepare(db,event)


def auto_prepare_quote(db,event):
    from app.modules.quotations.automation import prepare_invitation_quote
    prepare_invitation_quote(db,event.payload['invitation_id'])


def process_batch(db,limit=25,now=None,event_ids=None,lane='all'):
    now=core._utc(now or datetime.now(UTC))
    if event_ids is None and lane in {'all','events'}:
        from app.modules.notifications.procurement import schedule_reminders
        from app.modules.organizations.auto_approval import run_auto_approval_sweep
        from app.modules.procurement.sla import sweep as sla_sweep
        for sweep in (schedule_reminders, run_auto_approval_sweep, sla_sweep):
            try:
                sweep(db, now)
            except Exception as exc:  # noqa: BLE001 - isolate background sweeps from outbox queue
                db.rollback()
                log.warning('Background sweep=%s failed (%s)', sweep.__name__, type(exc).__name__)
    due=or_(Outbox.next_attempt_at.is_(None),Outbox.next_attempt_at<=now)
    eligible=or_(and_(Outbox.status.in_({'pending','retry','awaiting_status'}),due),and_(Outbox.status=='processing',or_(Outbox.lock_until.is_(None),Outbox.lock_until<=now)))
    lane_condition=lane_filter(lane)
    if lane_condition is not None:eligible=and_(eligible,lane_condition)
    query=select(Outbox.id).where(eligible)
    if event_ids is not None:query=query.where(Outbox.id.in_(event_ids))
    ids=db.scalars(query.order_by(Outbox.created_at,Outbox.id).limit(limit)).all();done=[]
    poll_started=time.monotonic();budget=get_settings().outbox_poll_time_budget_seconds
    for position,event_id in enumerate(ids):
        # Unclaimed events stay queued for the next poll; nothing is skipped or lost.
        if event_ids is None and position and time.monotonic()-poll_started>=budget:break
        # Eligibility results are reused only within one event (quotation draft + notice).
        db.info.pop('eligibility_memo',None)
        try:
            event=db.scalar(select(Outbox).where(Outbox.id==event_id,eligible).with_for_update(skip_locked=True))
            if not event:continue
            if (is_email_event(event) and email_paused(now) and event.delivery_status!='submitting'
                    and event.status!='awaiting_status'):
                defer_paused_email(event,now)
                if event.status=='failed':update_delivery_notification(db,event.id,'failed')
                db.commit();continue
            event.status='processing';event.lock_until=now+timedelta(seconds=300);db.commit()
            if event.event=='rfq.published':
                core._process_event(db,event,now)
            elif event.event=='notification.delivery':deliver(db,event,now)
            elif event.event=='company.verification_updated':
                from app.modules.notifications.procurement import company_notice
                company_notice(db,event)
            elif event.event=='rfq.invitation.responded':
                from app.modules.notifications.procurement import invitation_response_notice
                invitation_response_notice(db,event)
            elif event.event=='rfq.status_updated':
                from app.modules.notifications.procurement import rfq_status_notice
                rfq_status_notice(db,event)
            elif event.event=='account.approved':
                from app.modules.notifications.procurement import account_approved_notice
                account_approved_notice(db,event)
            elif event.event=='quotation.submitted':
                from app.modules.notifications.procurement import quotation_notice
                quotation_notice(db,event)
            elif event.event in {'rfq.invitation','rfq.deadline.reminder'}:
                prepare_invitation_with_isolated_quote(db,event)
            elif event.event=='quotation.auto_prepare':
                auto_prepare_quote(db,event)
            elif event.event in PROCUREMENT_EVENTS:
                from app.modules.procurement.notices import handle
                handle(db,event)
            elif event.event=='document_ai.extract':
                from app.modules.procurement.docai import process_extraction
                process_extraction(db,event)
            else:core._process_event(db,event,now)
            event.lock_until=None
            if event.status=='processing':event.status='processed'
            if event.status not in {'failed','uncertain'}:event.last_error=None
            if event.status in {'skipped','uncertain'}:update_delivery_notification(db,event.id,event.status)
            if is_email_event(event) and event.status=='processed':record_email_outcome(None,now)
            db.commit();done.append(event_id)
            log.info('Outbox event=%s correlation_id=%s channel=%s status=%s provider_status=%s%s',
                event_id,(event.payload.get('context') or {}).get('correlation_id',event_id),
                event.payload.get('channel','internal'),event.status,event.delivery_status,
                f" reason={event.payload['eligibility_reason']}" if event.payload.get('eligibility_reason') else '')
        except Exception as exc:  # noqa: BLE001 - isolate jobs and keep the worker available
            db.rollback();event=db.get(Outbox,event_id)
            if event:
                event.attempts+=1;event.lock_until=None
                event.last_error=str(exc)[:500] if isinstance(exc,(DeliveryError,core.PermanentOutboxError)) else type(exc).__name__
                retryable=not isinstance(exc,core.PermanentOutboxError) and getattr(exc,'retryable',True)
                detail=getattr(exc,'provider_response',None)
                if detail:
                    # Complete sanitized provider response (no addresses, IPs or tokens).
                    event.payload={**event.payload,'provider_response':detail}
                    if is_email_event(event):record_email_outcome(exc,now)
                if event.payload.get('channel')=='email' and (getattr(exc,'uncertain',False) or (event.delivery_status=='submitting' and not isinstance(exc,DeliveryError))):
                    event.status='uncertain';event.next_attempt_at=None
                elif retryable and event.attempts<get_settings().delivery_max_attempts:
                    delay=min(900,15*2**(event.attempts-1))
                    if detail and detail.get('failure_class')=='rate_limited':
                        delay=max(delay,get_settings().smtp_failure_pause_seconds)
                    event.status='retry';event.next_attempt_at=now+timedelta(seconds=delay)
                    if event.payload.get('channel')=='email':event.delivery_status=None
                else:event.status='failed';event.next_attempt_at=None
                if (event.payload.get('procurement_recipient') and getattr(exc,'permanent_recipient',False)):
                    from app.models import DeliveryRestriction
                    from app.modules.notifications.recipients import restriction
                    destination=event.payload['destination'];channel=event.payload['channel']
                    if not restriction(db,channel,destination):
                        db.add(DeliveryRestriction(channel=channel,destination=destination,
                            reason='suppressed',detail='SMTP permanently refused recipient'))
                # A definitive rejection before acceptance is safe to retry. An
                # uncertain SMS keeps its deterministic provider ID for lookup.
                if (event.payload.get('channel')=='sms' and event.delivery_status=='submitting'
                    and isinstance(exc,DeliveryError) and not getattr(exc,'uncertain',False)):
                    event.provider_message_id=None;event.delivery_status=None
                update_delivery_notification(db,event.id,'queued' if event.status=='retry' else event.status);db.commit()
            log.warning('Notification job=%s channel=%s status=%s error=%s', event_id, event.payload.get('channel') if event else 'unknown', event.status if event else 'unknown', str(exc) if isinstance(exc,DeliveryError) else type(exc).__name__)
    return done


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');parser.add_argument('--diagnose',action='store_true');parser.add_argument('--interval',type=int,default=5)
    args=parser.parse_args();logging.basicConfig(level=logging.INFO)
    if args.diagnose:
        settings=get_settings();log.info('Mock=%s SMS enabled=%s mode=%s credentials=%s SMTP credentials=%s port=%s',settings.mock_notifications,settings.sms_gateway_enabled,settings.sms_gateway_mode,bool(settings.sms_gate_username and settings.sms_gate_password),bool(settings.smtp_username and settings.smtp_password),settings.smtp_port)
        try:
            with SessionLocal() as db:log.info('Outbox states: %s',db.execute(select(Outbox.status,func.count()).group_by(Outbox.status)).all())
        except Exception as exc:  # noqa: BLE001 - isolate jobs and keep the worker available
            log.error('Outbox diagnostic unavailable (%s)',type(exc).__name__);return 1
        return 0
    while True:
        try:
            with SessionLocal() as db:done=process_batch(db)
            if done:log.info('Processed %s jobs',len(done))
        except Exception as exc:  # noqa: BLE001 - isolate jobs and keep the worker available
            log.error('Worker unavailable (%s); will retry',type(exc).__name__)
            if args.once:return 1
        if args.once:return 0
        time.sleep(max(1,args.interval))

if __name__=='__main__':raise SystemExit(main())
