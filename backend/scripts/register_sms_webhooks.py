"""List or register SMS Gateway for Android delivery-report webhooks.

The platform reconciles SMS status by polling (backoff 30 s -> 30 min). Webhooks make
Sent/Delivered/Failed arrive immediately. Endpoint: POST {public API}/api/v1/webhooks/sms-gate,
verified with SMS_GATE_WEBHOOK_SIGNING_KEY (set the same signing key in the SMS Gateway
app/cloud settings). Registration uses fixed webhook ids, so re-running is idempotent.

This script talks to the SMS provider API only when run by an operator. It never sends SMS.

Usage (from backend/):
  python scripts/register_sms_webhooks.py --list
  python scripts/register_sms_webhooks.py --url https://api.procumaterial.com/api/v1/webhooks/sms-gate          # dry run
  python scripts/register_sms_webhooks.py --url https://api.procumaterial.com/api/v1/webhooks/sms-gate --apply
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

EVENTS = ('sms:sent', 'sms:delivered', 'sms:failed')


def base_url(settings):
    return settings.sms_gate_api_url.rsplit('/messages', 1)[0] + '/webhooks'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--url')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    if not settings.sms_gate_username or not settings.sms_gate_password:
        print('SMS gateway credentials are not configured');return 1
    auth = (settings.sms_gate_username, settings.sms_gate_password)
    if args.list:
        response = httpx.get(base_url(settings), auth=auth, timeout=settings.delivery_timeout_seconds)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))
        return 0
    if not args.url or not args.url.startswith('https://'):
        print('--url must be the public HTTPS webhook URL');return 2
    if not settings.sms_gate_webhook_signing_key.get_secret_value():
        print('Set SMS_GATE_WEBHOOK_SIGNING_KEY first; the endpoint is disabled without it');return 2
    for event in EVENTS:
        body = {'id': 'procumaterial-' + event.replace(':', '-'), 'url': args.url, 'event': event}
        if not args.apply:
            print('would register', json.dumps(body));continue
        response = httpx.post(base_url(settings), json=body, auth=auth, timeout=settings.delivery_timeout_seconds)
        print(event, response.status_code)
        response.raise_for_status()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
