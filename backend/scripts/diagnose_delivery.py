"""Read-only delivery configuration/connectivity checks; never sends messages."""

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
        factory = SMTP_SSL if settings.smtp_port == 465 else SMTP
        if not settings.smtp_use_tls or settings.smtp_port not in {465, 587}:
            raise ValueError('Secure SMTP configuration required')
        kwargs = {'timeout': settings.delivery_timeout_seconds}
        if settings.smtp_port == 465:
            kwargs['context'] = create_default_context()
        with factory(settings.smtp_host, settings.smtp_port, **kwargs) as smtp:
            if settings.smtp_port == 587:
                smtp.ehlo(); smtp.starttls(context=create_default_context()); smtp.ehlo()
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
    checks += [('dkim_'+selector, selector+'._domainkey.'+domain, 'CNAME', '')
               for selector in ('hostingermail-a','hostingermail-b','hostingermail-c')]
    for name, host, kind, prefix in checks:
        try:
            response = httpx.get('https://cloudflare-dns.com/dns-query', params={'name':host,'type':kind},
                                 headers={'accept':'application/dns-json'}, timeout=3)
            response.raise_for_status()
            answers = response.json().get('Answer', [])
            result[name] = {'record_present': any(prefix in str(answer.get('data','')) for answer in answers)}
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-connections", action="store_true")
    parser.add_argument("--check-providers", action="store_true", help="SMTP login and SMS device listing only; no messages")
    parser.add_argument("--check-dns", action="store_true", help="Public SPF/DMARC and standard Hostinger DKIM DNS records")
    args = parser.parse_args()
    settings = get_settings()
    result = {
        "auth_auto_dispatch": settings.auth_delivery_auto_dispatch,
        "mock_notifications": settings.mock_notifications,
        "sms_enabled": settings.sms_gateway_enabled,
        "sms_live_mode": settings.sms_gateway_mode == "live",
        "sms_credentials_configured": bool(settings.sms_gate_username and settings.sms_gate_password),
        "smtp_credentials_configured": bool(settings.smtp_username and settings.smtp_password),
        "smtp_sender_configured": bool(settings.smtp_from_email or settings.smtp_username),
        "smtp_tls": settings.smtp_use_tls,
        "smtp_port": settings.smtp_port,
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
    print(json.dumps(result, indent=2))
    if args.check_connections and any(not item["tcp_reachable"] for item in result["connectivity"].values()):
        return 1
    if args.check_providers and any(not item['authenticated'] for item in result['providers'].values()):
        return 1
    if args.check_dns and any(item.get('status')=='unverified' or not item.get('record_present') for item in result['dns'].values() if isinstance(item,dict)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
