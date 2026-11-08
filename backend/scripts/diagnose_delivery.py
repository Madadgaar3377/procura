"""Read-only delivery configuration/connectivity checks and safe test email dispatcher."""

import argparse
import json
import socket
from smtplib import SMTP, SMTP_SSL
from ssl import create_default_context
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
import httpx


def provider_checks(settings):
    """Authenticate without submitting SMTP DATA or POSTing an SMS."""
    results = {}
    try:
        security = (getattr(settings, 'smtp_security', '') or '').strip().upper()
        is_implicit_ssl = (
            settings.smtp_port == 465
            or (security in {'SSL/TLS', 'SSL', 'TLS'} and settings.smtp_port != 587)
        )
        if not is_implicit_ssl and not (settings.smtp_port == 587 or security == 'STARTTLS' or settings.smtp_use_tls):
            raise ValueError('Secure SMTP configuration required (SSL/TLS on port 465 or STARTTLS on port 587)')
        factory = SMTP_SSL if is_implicit_ssl else SMTP
        kwargs = {'timeout': settings.delivery_timeout_seconds}
        if is_implicit_ssl:
            kwargs['context'] = create_default_context()
        with factory(settings.smtp_host, settings.smtp_port, **kwargs) as smtp:
            if not is_implicit_ssl:
                smtp.ehlo()
                smtp.starttls(context=create_default_context())
                smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_password)
        results['smtp'] = {'authenticated': True, 'delivery_confirmed': False}
    except Exception as exc:
        results['smtp'] = {'authenticated': False, 'error_type': type(exc).__name__}
    try:
        from app.modules.notifications.delivery import validate_sms_settings
        validate_sms_settings(settings)
        url = settings.sms_gate_api_url.rsplit('/messages', 1)[0] + '/devices'
        with httpx.Client(auth=(settings.sms_gate_username, settings.sms_gate_password), timeout=settings.delivery_timeout_seconds) as client:
            response = client.get(url)
            response.raise_for_status()
            devices = response.json()
        results['sms_gateway'] = {'authenticated': True, 'http_status': response.status_code,
                                  'registered_device_count': len(devices) if isinstance(devices,list) else None,
                                  'device_delivery_confirmed': False}
    except Exception as exc:
        results['sms_gateway'] = {'authenticated': False, 'error_type': type(exc).__name__}
    return results


def dns_checks(settings):
    from email.utils import parseaddr
    address = parseaddr(settings.smtp_from_email)[1]
    if '@' not in address:
        address = settings.smtp_username
    if '@' not in address:
        return {'status': 'sender_domain_unconfigured'}
    domain = address.rsplit('@', 1)[1]
    result = {}
    checks = [('spf', domain, 'TXT', 'v=spf1'), ('dmarc', '_dmarc.'+domain, 'TXT', 'v=DMARC1')]
    # Check standard Spaceship Spacemail selectors and fallback selectors
    checks += [('dkim_'+selector, selector+'._domainkey.'+domain, 'TXT', '')
               for selector in ('s1', 's2', 'spacemail', 'mail')]
    for name, host, kind, prefix in checks:
        try:
            response = httpx.get('https://cloudflare-dns.com/dns-query', params={'name':host,'type':kind},
                                 headers={'accept':'application/dns-json'}, timeout=3)
            response.raise_for_status()
            answers = response.json().get('Answer', [])
            result[name] = {'record_present': any(prefix in str(answer.get('data','')) for answer in answers) if prefix else bool(answers)}
        except Exception as exc:
            result[name] = {'status':'unverified','error_type':type(exc).__name__}
    return result


def probe(target):
    name, host, port = target
    try:
        with socket.create_connection((host, port), timeout=3):
            return name, {"tcp_reachable": True}
    except OSError as exc:
        return name, {"tcp_reachable": False, "error_type": type(exc).__name__}


def send_test_email(recipient: str, settings) -> dict:
    """Send a single branded test email without exposing credentials or modifying the user database."""
    from uuid import uuid4
    from app.modules.notifications.branded_templates import render_message
    from app.modules.notifications.delivery import send_email

    subject, plain, html = render_message(
        'registration_welcome',
        {
            'language': 'en',
            'channel': 'email',
            'supplier_name': 'Procumaterial Partner',
            'url': settings.frontend_url.rstrip('/') + '/dashboard',
        },
    )
    subject = f"Procumaterial SMTP Test | {subject}"
    idempotency_key = f"test-{uuid4().hex}"

    # Override mock mode for explicit test email invocation
    prev_mock = settings.mock_notifications
    try:
        settings.mock_notifications = False
        message_id = send_email(recipient, subject, plain, html, idempotency_key)
        return {
            'success': True,
            'recipient': recipient,
            'message_id': message_id,
            'smtp_host': settings.smtp_host,
            'smtp_port': settings.smtp_port,
        }
    except Exception as exc:
        return {
            'success': False,
            'recipient': recipient,
            'error_type': type(exc).__name__,
            'error_message': str(exc),
        }
    finally:
        settings.mock_notifications = prev_mock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-connections", action="store_true")
    parser.add_argument("--check-providers", action="store_true", help="SMTP login and SMS device listing only; no messages")
    parser.add_argument("--check-dns", action="store_true", help="Public SPF, DMARC, and DKIM DNS records")
    parser.add_argument("--send-test-email", type=str, default="", help="Send a branded Procumaterial test email to this recipient")
    args = parser.parse_args()
    settings = get_settings()
    result = {
        "auth_auto_dispatch": settings.auth_delivery_auto_dispatch,
        "mock_notifications": settings.mock_notifications,
        "sms_enabled": settings.sms_gateway_enabled,
        "sms_live_mode": settings.sms_gateway_mode == "live",
        "sms_credentials_configured": bool(settings.sms_gate_username and settings.sms_gate_password),
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_security": settings.smtp_security,
        "smtp_from_name": settings.smtp_from_name,
        "smtp_credentials_configured": bool(settings.smtp_username and settings.smtp_password),
        "smtp_sender_configured": bool(settings.smtp_from_email or settings.smtp_username),
        "smtp_tls": settings.smtp_use_tls,
        "messages_sent": False,
    }
    if args.check_connections:
        gateway = urlsplit(settings.sms_gate_api_url)
        targets = [
            ("sms_gateway", gateway.hostname, gateway.port or 443),
            ("smtp", settings.smtp_host, settings.smtp_port),
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            result["connectivity"] = dict(pool.map(probe, targets))
    if args.check_providers:
        result['providers'] = provider_checks(settings)
    if args.check_dns:
        result['dns'] = dns_checks(settings)
    if args.send_test_email:
        test_res = send_test_email(args.send_test_email, settings)
        result['test_email_dispatch'] = test_res
        result['messages_sent'] = test_res.get('success', False)
        print(json.dumps(result, indent=2))
        return 0 if test_res.get('success') else 1

    print(json.dumps(result, indent=2))
    if args.check_connections and any(not item["tcp_reachable"] for item in result["connectivity"].values()):
        return 1
    if args.check_providers and any(not item['authenticated'] for item in result['providers'].values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
