"""WhatsApp Business Cloud API adapter for consolidated RFQ quick-quote alerts.

Delivery is never simulated: without WHATSAPP_ENABLED, WHATSAPP_ACCESS_TOKEN and
WHATSAPP_PHONE_NUMBER_ID the job is recorded as skipped with
``whatsapp_not_configured``. Status updates (sent, delivered, read, failed)
arrive through the signed webhook in ``public.py``.
"""
from __future__ import annotations

import hashlib
import hmac

import httpx

from app.config import get_settings
from app.modules.notifications.providers import DeliveryError, _phone_e164

REQUIRED_SETTINGS = ('WHATSAPP_ENABLED=true', 'WHATSAPP_ACCESS_TOKEN', 'WHATSAPP_PHONE_NUMBER_ID',
                     'WHATSAPP_RFQ_TEMPLATE (approved template with 5 body parameters)', 'WHATSAPP_APP_SECRET (webhook signatures)')


def configured() -> bool:
    s = get_settings()
    return bool(s.whatsapp_enabled and s.whatsapp_access_token.get_secret_value() and s.whatsapp_phone_number_id)


def configuration_status() -> dict:
    s = get_settings()
    return {
        'configured': configured(),
        'enabled': s.whatsapp_enabled,
        'has_access_token': bool(s.whatsapp_access_token.get_secret_value()),
        'has_phone_number_id': bool(s.whatsapp_phone_number_id),
        'template': s.whatsapp_rfq_template,
        'webhook_signature_verification': bool(s.whatsapp_app_secret.get_secret_value()),
        'required_settings': list(REQUIRED_SETTINGS),
    }


def send_template(phone: str, parameters: list[str], idempotency_key: str, *, transport=None) -> str:
    """Send the approved RFQ template. Returns the WhatsApp message id (wamid)."""
    if not configured():
        raise DeliveryError('WhatsApp is not configured: set ' + ', '.join(REQUIRED_SETTINGS[:3]), retryable=False)
    s = get_settings()
    to = _phone_e164(phone).lstrip('+')
    url = f'https://graph.facebook.com/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}/messages'
    body = {
        'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': to, 'type': 'template',
        'biz_opaque_callback_data': idempotency_key[:512],
        'template': {
            'name': s.whatsapp_rfq_template, 'language': {'code': s.whatsapp_template_language},
            'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': str(p)[:1000]} for p in parameters]}],
        },
    }
    try:
        with httpx.Client(timeout=s.delivery_timeout_seconds, transport=transport) as client:
            response = client.post(url, json=body, headers={'Authorization': f'Bearer {s.whatsapp_access_token.get_secret_value()}'})
            response.raise_for_status()
            data = response.json()
        message_id = data['messages'][0]['id']
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise DeliveryError(f'WhatsApp request failed (HTTP {status})', retryable=status in {408, 429} or status >= 500) from exc
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise DeliveryError('WhatsApp request failed (network or invalid response)', retryable=True) from exc
    return str(message_id)


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    secret = get_settings().whatsapp_app_secret.get_secret_value()
    if not secret or not header or not header.startswith('sha256='):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split('=', 1)[1])


STATUS_MAP = {'sent': 'sent', 'delivered': 'delivered', 'read': 'delivered', 'failed': 'failed'}
