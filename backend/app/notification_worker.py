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


def apply_sms(db,event,result,now):
    state=str(result.get('state') or 'Unknown');event.delivery_status=state;event.provider_message_id=result['id'];event.last_error=None
    if result.get('http_status') is not None:
        key='provider_status_response_code' if result.get('lookup') else 'provider_response_code'
        event.payload={**event.payload,key:result['http_status']}
    if state=='Delivered':event.status='processed';event.next_attempt_at=None;notice='delivered'
    elif state in {'Failed','Cancelled'}:
        event.status='failed';event.next_attempt_at=None;event.last_error='Provider reported delivery failure';notice='failed'
    elif event.status_checks>=get_settings().delivery_status_max_polls:
        event.status='processed' if state=='Sent' else 'uncertain';event.next_attempt_at=None
        event.last_error='Delivery confirmation unavailable; reconcile before resending';notice='sent' if state=='Sent' else 'uncertain'
    else:
        event.status='awaiting_status';event.next_attempt_at=now+timedelta(seconds=30);notice='sent' if state=='Sent' else 'accepted'
    update_delivery_notification(db,event.id,notice)


def deliver(db,event,now):
    payload=event.payload;channel=payload.get('channel');user=db.get(User,payload.get('user_id'))
    if channel not in {'sms','email'}:raise core.PermanentOutboxError('Unsupported notification channel')
    # Reconcile accepted SMS even after a code expires or its destination changes.
    # Polling never submits another message and must not be gated by OTP validity.
    if channel=='sms' and event.provider_message_id and event.delivery_status not in {'submitting','unknown',None}:
        result=get_sms_status(event.provider_message_id)
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
        from app.modules.auth.router import recovery_key
        otp=db.get(EmailOTP,context.get('otp_id'))
        if not otp or otp.email!=recovery_key(user) or otp.purpose!='password_reset' or otp.attempts>=5:
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
        try:result=get_sms_status(event.provider_message_id)
        except DeliveryError as exc:
            if getattr(exc,'status_code',None)!=404 or event.delivery_status not in {'submitting','unknown'}:raise
        else:event.status_checks+=1;apply_sms(db,event,result,now);return
    if channel=='email' and event.delivery_status=='submitting':
        event.status='uncertain';event.last_error='SMTP outcome unknown; reconcile with mailbox or SMTP logs before retry';return
    secret=decrypt_delivery_secret(event.secret_ciphertext) if event.secret_ciphertext else None
    render_context={**context,'language':user.notification_language,'channel':channel,'minutes':max(1,(ttl+59)//60)}
    if template=='password_reset' and channel=='email':
        from urllib.parse import urlsplit

        from app.modules.auth.router import recovery_link_token
        base=get_settings().frontend_url.rstrip('/');parsed=urlsplit(base)
        if parsed.scheme not in {'https','http'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or (get_settings().is_production and parsed.scheme!='https'):
            raise core.PermanentOutboxError('FRONTEND_URL must be a secure application URL')
        render_context['url']=base+'/reset-password#token='+recovery_link_token(context['otp_id'],secret)
    subject,plain,html=render_message(template,render_context,secret)
    event.delivery_status='submitting';event.provider_message_id=_mock_idempotency_id(event.id) if channel=='sms' else email_message_id(event.id)
    update_delivery_notification(db,event.id,'sending');db.commit()
    if channel=='sms':
        result=send_sms(recipient,plain,event.id,ttl_seconds=ttl);event.attempts+=1;apply_sms(db,event,result,now)
    else:
        event.provider_message_id=send_email(recipient,subject,plain,html,event.id);event.attempts+=1
        event.delivery_status='accepted';event.status='processed';event.next_attempt_at=None
        event.payload={**event.payload,'smtp_accepted':True}
        update_delivery_notification(db,event.id,'accepted')
    event.secret_ciphertext=None


def process_batch(db,limit=25,now=None,event_ids=None):
    now=core._utc(now or datetime.now(UTC))
    if event_ids is None:
        from app.modules.notifications.procurement import schedule_reminders
        schedule_reminders(db,now)
    due=or_(Outbox.next_attempt_at.is_(None),Outbox.next_attempt_at<=now)
    eligible=or_(and_(Outbox.status.in_({'pending','retry','awaiting_status'}),due),and_(Outbox.status=='processing',or_(Outbox.lock_until.is_(None),Outbox.lock_until<=now)))
    query=select(Outbox.id).where(eligible)
    if event_ids is not None:query=query.where(Outbox.id.in_(event_ids))
    ids=db.scalars(query.order_by(Outbox.created_at,Outbox.id).limit(limit)).all();done=[]
    for event_id in ids:
        try:
            event=db.scalar(select(Outbox).where(Outbox.id==event_id,eligible).with_for_update(skip_locked=True))
            if not event:continue
            event.status='processing';event.lock_until=now+timedelta(seconds=300);db.commit()
            if event.event=='rfq.published':
                from app.models import RFQ
                from app.modules.matching.service import match_and_invite
                rfq=db.scalar(select(RFQ).where(RFQ.id==event.payload.get('rfq_id')).with_for_update())
                if not rfq or rfq.status not in {'published','collecting_quotes'}:
                    raise core.PermanentOutboxError('RFQ is not dispatchable')
                match_and_invite(db,rfq,after_supplier_id=event.payload.get('after_supplier_id'),bounded=True)
            elif event.event=='notification.delivery':deliver(db,event,now)
            elif event.event=='company.verification_updated':
                from app.modules.notifications.procurement import company_notice
                company_notice(db,event)
            elif event.event=='quotation.submitted':
                from app.modules.notifications.procurement import quotation_notice
                quotation_notice(db,event)
            elif event.event in {'rfq.invitation','rfq.deadline.reminder'}:
                from app.modules.notifications.procurement import prepare
                prepare(db,event)
            else:core._process_event(db,event,now)
            event.lock_until=None
            if event.status=='processing':event.status='processed'
            if event.status not in {'failed','uncertain'}:event.last_error=None
            if event.status in {'skipped','uncertain'}:update_delivery_notification(db,event.id,event.status)
            db.commit();done.append(event_id)
            log.info('Outbox event=%s correlation_id=%s channel=%s status=%s provider_status=%s',
                event_id,(event.payload.get('context') or {}).get('correlation_id',event_id),
                event.payload.get('channel','internal'),event.status,event.delivery_status)
        except Exception as exc:  # noqa: BLE001 - isolate jobs and keep the worker available
            db.rollback();event=db.get(Outbox,event_id)
            if event:
                event.attempts+=1;event.lock_until=None
                event.last_error=str(exc)[:500] if isinstance(exc,DeliveryError) else type(exc).__name__
                retryable=not isinstance(exc,core.PermanentOutboxError) and getattr(exc,'retryable',True)
                if event.payload.get('channel')=='email' and (getattr(exc,'uncertain',False) or (event.delivery_status=='submitting' and not isinstance(exc,DeliveryError))):
                    event.status='uncertain';event.next_attempt_at=None
                elif retryable and event.attempts<get_settings().delivery_max_attempts:
                    event.status='retry';event.next_attempt_at=now+timedelta(seconds=min(900,15*2**(event.attempts-1)))
                    if event.payload.get('channel')=='email':event.delivery_status=None
                else:event.status='failed';event.next_attempt_at=None
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
