"""Worker delivery adapter; preserves legacy provider helpers and endpoint contracts."""
from __future__ import annotations
from email.message import EmailMessage
from email.utils import parseaddr
from hashlib import sha256
import re
import logging

log = logging.getLogger("uvicorn.error")
from smtplib import SMTP, SMTP_SSL, SMTPException, SMTPResponseException, SMTPRecipientsRefused, SMTPAuthenticationError
from ssl import create_default_context
import httpx
from app.config import get_settings
from app.modules.notifications.providers import DeliveryError, _mock_idempotency_id, _resolve_sender


def delivery_error(message, *, retryable=True, uncertain=False, status_code=None):
    error=DeliveryError(message,retryable=retryable)
    error.uncertain=uncertain;error.status_code=status_code
    return error


def normalize_sms_phone(value):
    from app.identity import normalize_phone
    try:
        return normalize_phone(value, sms=True)
    except ValueError as exc:
        raise delivery_error('Use a valid international mobile number with + and country code',retryable=False) from exc


def validate_sms_settings(settings):
    if settings.sms_provider!='android_gateway':raise delivery_error('Unsupported SMS_PROVIDER',retryable=False)
    if not settings.sms_gateway_enabled or settings.sms_gateway_mode!='live':
        raise delivery_error('Enable SMS_GATEWAY_ENABLED and SMS_GATEWAY_MODE=live',retryable=False)
    if not settings.sms_gate_username or not settings.sms_gate_password:
        raise delivery_error('SMS credentials are not configured',retryable=False)
    if not settings.sms_gate_api_url.startswith('https://') and not (settings.is_test and settings.sms_gate_api_url.startswith('http://127.0.0.1:')):
        raise delivery_error('SMS provider URL must use HTTPS',retryable=False)


def sms_request(method, provider_id=None, payload=None, *, transport=None):
    settings=get_settings();validate_sms_settings(settings)
    log.info('SMS gateway configured; connecting operation=%s', method)
    from urllib.parse import quote
    url=settings.sms_gate_api_url+('/'+quote(provider_id,safe='') if provider_id else '')
    try:
        with httpx.Client(auth=(settings.sms_gate_username,settings.sms_gate_password),
                timeout=httpx.Timeout(settings.delivery_timeout_seconds,connect=min(5,settings.delivery_timeout_seconds)),transport=transport) as client:
            response=client.request(method,url,json=payload) if payload is not None else client.request(method,url)
            response.raise_for_status()
            result=response.json()
        if not isinstance(result,dict):raise ValueError()
        log.info('SMS gateway connected; operation=%s HTTP=%s state=%s', method, response.status_code, str(result.get('state', 'Pending')) if result.get('state') in {'Pending','Sent','Delivered','Failed','Cancelled'} else 'Unknown')
        return result,response.status_code
    except (httpx.HTTPError,ValueError) as exc:
        code=getattr(getattr(exc,'response',None),'status_code',None)
        uncertain=method=='POST' and (isinstance(exc,(httpx.ReadError,httpx.ReadTimeout,httpx.RemoteProtocolError,ValueError)) or (code is not None and code>=500))
        raise delivery_error('SMS '+('submission' if method=='POST' else 'status lookup')+' failed'+(f' (HTTP {code})' if code else ''),
            retryable=code is None or code in {408,425,429} or code>=500,uncertain=uncertain,status_code=code) from exc


def get_sms_status(provider_message_id, *, transport=None):
    if get_settings().mock_notifications:return {'id':provider_message_id,'state':'Delivered'}
    result,code=sms_request('GET',provider_message_id,transport=transport)
    return {'id':str(result.get('id') or provider_message_id),'state':str(result.get('state') or 'Unknown'),'http_status':code,'lookup':True}


def send_sms(phone,text,idempotency_key, *, ttl_seconds=3600,transport=None):
    recipient=normalize_sms_phone(phone);message_id=_mock_idempotency_id(idempotency_key)
    settings=get_settings()
    if settings.mock_notifications and settings.sms_gateway_enabled and settings.sms_gateway_mode=='live' and not settings.is_test:
        raise delivery_error('Conflicting delivery configuration: disable MOCK_NOTIFICATIONS for authorized live SMS',retryable=False)
    if settings.mock_notifications:
        log.warning('SMS delivery simulated: MOCK_NOTIFICATIONS=true; no SMS sent')
        return {'id':message_id,'state':'Delivered'}
    payload={'id':message_id,'textMessage':{'text':text},'phoneNumbers':[recipient],
             'ttl':max(1,min(int(ttl_seconds),86400)),'withDeliveryReport':True}
    try:result,code=sms_request('POST',payload=payload,transport=transport)
    except DeliveryError as exc:
        if getattr(exc,'status_code',None)==409:return get_sms_status(message_id,transport=transport)
        raise
    return {'id':str(result.get('id') or message_id),'state':'Pending' if code==202 else str(result.get('state') or 'Pending'),'http_status':code}


def email_message_id(key):return '<delivery-'+sha256(key.encode()).hexdigest()[:32]+'@procurement.local>'


def send_email(address, subject, plain_text, html_body, idempotency_key):
    settings = get_settings()
    message_id = email_message_id(idempotency_key)
    if settings.mock_notifications:
        log.warning("Email delivery simulated: MOCK_NOTIFICATIONS=true; no email sent")
        return message_id
    if not (settings.smtp_username and settings.smtp_password):
        raise delivery_error("SMTP credentials are not configured", retryable=False)
    security = (getattr(settings, "smtp_security", "") or "").strip().upper()
    is_implicit_ssl = (
        settings.smtp_port == 465
        or (security in {"SSL/TLS", "SSL", "TLS"} and settings.smtp_port != 587)
    )
    if not is_implicit_ssl and not (settings.smtp_port == 587 or security == "STARTTLS" or settings.smtp_use_tls):
        raise delivery_error("Use SMTP SSL on port 465 or STARTTLS on port 587", retryable=False)
    sender = _resolve_sender(settings)
    if any("\r" in value or "\n" in value for value in (address, sender, subject)) or not all(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", parseaddr(value)[1]) for value in (sender, address)):
        raise delivery_error("Invalid SMTP sender or recipient", retryable=False)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = address
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(plain_text)
    message.add_alternative(html_body, subtype="html")
    log.info("Email SMTP configured; connecting port=%s security=%s", settings.smtp_port, "SSL/TLS" if is_implicit_ssl else "STARTTLS")
    stage = "connect"
    try:
        factory = SMTP_SSL if is_implicit_ssl else SMTP
        kwargs = {"timeout": settings.delivery_timeout_seconds}
        if is_implicit_ssl:
            kwargs["context"] = create_default_context()
        with factory(settings.smtp_host, settings.smtp_port, **kwargs) as smtp:
            if not is_implicit_ssl:
                smtp.ehlo()
                smtp.starttls(context=create_default_context())
                smtp.ehlo()
            stage = "authenticate"
            smtp.login(settings.smtp_username, settings.smtp_password)
            log.info("Email SMTP connected and authenticated")
            stage = "data"
            refused = smtp.send_message(message)
            if refused:
                raise delivery_error("SMTP recipient refused", retryable=False)
            stage = "accepted"
            log.info("Email send done: SMTP accepted message; inbox delivery is not confirmed")
    except SMTPAuthenticationError as exc:
        raise delivery_error(f"SMTP authentication failed (code {exc.smtp_code})", retryable=False, status_code=exc.smtp_code) from exc
    except SMTPRecipientsRefused as exc:
        codes = [value[0] for value in exc.recipients.values()]
        raise delivery_error("SMTP recipient refused", retryable=bool(codes) and all(400 <= code < 500 for code in codes)) from exc
    except SMTPResponseException as exc:
        if stage == "accepted":
            return message_id
        raise delivery_error(f"SMTP {stage} rejected (code {exc.smtp_code})", retryable=400 <= exc.smtp_code < 500, status_code=exc.smtp_code) from exc
    except (TimeoutError, OSError) as exc:
        if stage == "accepted":
            return message_id
        raise delivery_error(f"SMTP {stage} timed out or failed ({type(exc).__name__})", retryable=True, uncertain=stage == "data") from exc
    except SMTPException as exc:
        if stage == "accepted":
            return message_id
        raise delivery_error(f"SMTP {stage} failed ({type(exc).__name__})", retryable=True, uncertain=stage == "data") from exc
    return message_id
