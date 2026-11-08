"""SMS Gateway for Android and Hostinger SMTP adapters. Mock mode never opens a socket."""
from __future__ import annotations

from email.message import EmailMessage
from hashlib import sha256
import re
from smtplib import SMTP, SMTP_SSL, SMTPException
from ssl import create_default_context
from urllib.parse import quote

import httpx

from app.config import get_settings


class DeliveryError(RuntimeError):
    """Safe-to-store delivery failure without credentials, recipient, or message text."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _mock_idempotency_id(idempotency_key: str) -> str:
    # Provider IDs are visible to organization admins, so do not expose a DB UUID directly.
    return sha256(idempotency_key.encode('utf-8')).hexdigest()[:32]


def _phone_e164(phone: str) -> str:
    compact = re.sub(r'[\s()\-]', '', phone)
    if re.fullmatch(r'\+[1-9][0-9]{7,14}', compact):
        return compact
    if re.fullmatch(r'966[0-9]{8,9}', compact):
        return '+' + compact
    if re.fullmatch(r'0[0-9]{8,9}', compact):
        return '+966' + compact[1:]
    if re.fullmatch(r'5[0-9]{8}', compact):
        return '+966' + compact
    raise DeliveryError('SMS recipient must be a valid E.164 or Saudi national phone number', retryable=False)


def send_sms(phone: str, text: str, idempotency_key: str, *, ttl_seconds: int = 3600, transport=None) -> dict:
    settings = get_settings()
    provider_id = _mock_idempotency_id(idempotency_key)
    phone = _phone_e164(phone)
    if settings.mock_notifications:
        return {'id': provider_id, 'state': 'Delivered'}
    if not settings.sms_gateway_enabled:
        raise DeliveryError('SMS gateway is not enabled (SMS_GATEWAY_ENABLED=false)', retryable=False)
    if not settings.sms_gate_username or not settings.sms_gate_password:
        raise DeliveryError('SMS credentials are not configured')
    if not settings.sms_gate_api_url.startswith('https://'):
        raise DeliveryError('SMS provider URL must use HTTPS')

    payload = {
        'id': provider_id,
        'phoneNumbers': [phone],
        'textMessage': {'text': text},
        'ttl': max(1, min(int(ttl_seconds), 86400)),
    }
    try:
        with httpx.Client(
            auth=(settings.sms_gate_username, settings.sms_gate_password),
            timeout=settings.delivery_timeout_seconds,
            transport=transport,
        ) as client:
            response = client.post(settings.sms_gate_api_url, json=payload)
            response.raise_for_status()
            result = response.json() if response.content else {}
        if not isinstance(result, dict):
            raise DeliveryError('SMS provider returned an invalid response', retryable=False)
    except (httpx.HTTPError, ValueError) as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status == 409:
            # Idempotency / duplicate reconciliation: the message ID was already accepted by provider
            try:
                existing = get_sms_status(provider_id, transport=transport)
                return {'id': provider_id, 'state': str(existing.get('state') or 'Pending')}
            except Exception:
                return {'id': provider_id, 'state': 'Pending'}
        detail = f' (HTTP {status})' if status else ''
        retryable = status is None or status in {408, 425, 429} or status >= 500
        raise DeliveryError(f'SMS provider request failed{detail}', retryable=retryable) from exc
    return {'id': str(result.get('id') or provider_id), 'state': str(result.get('state') or 'Pending')}


def get_sms_status(provider_message_id: str, *, transport=None) -> dict:
    settings = get_settings()
    if settings.mock_notifications:
        return {'id': provider_message_id, 'state': 'Delivered'}
    if not settings.sms_gate_username or not settings.sms_gate_password:
        raise DeliveryError('SMS credentials are not configured')
    url = settings.sms_gate_api_url.rstrip('/') + '/' + quote(provider_message_id, safe='')
    try:
        with httpx.Client(
            auth=(settings.sms_gate_username, settings.sms_gate_password),
            timeout=settings.delivery_timeout_seconds,
            transport=transport,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise DeliveryError('SMS provider returned an invalid status response', retryable=False)
    except (httpx.HTTPError, ValueError) as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        detail = f' (HTTP {status})' if status else ''
        retryable = status is None or status in {408, 425, 429} or status >= 500
        raise DeliveryError(f'SMS status request failed{detail}', retryable=retryable) from exc
    return {'id': str(result.get('id') or provider_message_id), 'state': str(result.get('state') or 'Unknown'), 'reason': result.get('reason')}


def _resolve_sender(settings) -> str:
    """Build RFC 5322 From address with display name (e.g. Procumaterial <orders@procumaterial.com>)."""
    from email.utils import formataddr, parseaddr
    raw = (getattr(settings, 'smtp_from_email', '') or '').strip()
    name, addr = parseaddr(raw)
    email_address = addr if '@' in addr else (raw if '@' in raw else (getattr(settings, 'smtp_username', '') or '').strip())
    if not email_address or '@' not in email_address:
        raise DeliveryError('SMTP_FROM_EMAIL or SMTP_USERNAME must contain a valid email address', retryable=False)
    display_name = (getattr(settings, 'smtp_from_name', '') or '').strip() or name or 'Procumaterial'
    return formataddr((display_name, email_address))


def send_email(address: str, subject: str, plain_text: str, html_body: str, idempotency_key: str) -> str:
    from smtplib import SMTPAuthenticationError, SMTPRecipientsRefused, SMTPResponseException
    settings = get_settings()
    message_id = f'<delivery-{sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]}@procurement.local>'
    if settings.mock_notifications:
        return message_id
    if not all((settings.smtp_host, settings.smtp_username, settings.smtp_password)):
        raise DeliveryError('SMTP credentials are not configured', retryable=False)
    if not settings.smtp_from_email and not settings.smtp_username:
        raise DeliveryError('SMTP sender address is not configured', retryable=False)
    
    security = (getattr(settings, 'smtp_security', '') or '').strip().upper()
    is_implicit_ssl = (
        settings.smtp_port == 465
        or (security in {'SSL/TLS', 'SSL', 'TLS'} and settings.smtp_port != 587)
    )
    if not is_implicit_ssl and not (settings.smtp_port == 587 or security == 'STARTTLS' or settings.smtp_use_tls):
        raise DeliveryError('Secure SMTP configuration required (SSL/TLS on port 465 or STARTTLS on port 587)', retryable=False)

    sender = _resolve_sender(settings)
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = sender
    message['To'] = address
    message['Message-ID'] = message_id
    message.set_content(plain_text)
    message.add_alternative(html_body, subtype='html')
    stage = 'connect'
    try:
        if is_implicit_ssl:
            with SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.delivery_timeout_seconds, context=create_default_context()) as smtp:
                stage = 'authenticate'
                smtp.login(settings.smtp_username, settings.smtp_password)
                stage = 'data'
                refused = smtp.send_message(message)
                if refused:
                    raise DeliveryError('SMTP recipient refused', retryable=False)
        else:
            with SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.delivery_timeout_seconds) as smtp:
                smtp.ehlo()
                if settings.smtp_use_tls or security == 'STARTTLS':
                    smtp.starttls(context=create_default_context())
                    smtp.ehlo()
                stage = 'authenticate'
                smtp.login(settings.smtp_username, settings.smtp_password)
                stage = 'data'
                refused = smtp.send_message(message)
                if refused:
                    raise DeliveryError('SMTP recipient refused', retryable=False)
    except SMTPAuthenticationError as exc:
        raise DeliveryError(f'SMTP authentication failed (code {exc.smtp_code})', retryable=False) from exc
    except SMTPRecipientsRefused as exc:
        codes = [value[0] for value in exc.recipients.values()]
        retryable = bool(codes) and all(400 <= code < 500 for code in codes)
        raise DeliveryError('SMTP recipient refused', retryable=retryable) from exc
    except SMTPResponseException as exc:
        retryable = 400 <= exc.smtp_code < 500
        raise DeliveryError(f'SMTP {stage} rejected (code {exc.smtp_code})', retryable=retryable) from exc
    except (TimeoutError, OSError) as exc:
        raise DeliveryError(f'SMTP {stage} timed out or connection failed ({type(exc).__name__})', retryable=True) from exc
    except SMTPException as exc:
        raise DeliveryError(f'SMTP {stage} failed ({type(exc).__name__})', retryable=True) from exc
    return message_id
