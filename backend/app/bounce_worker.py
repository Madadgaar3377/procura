"""Return-path mailbox bounce processor (IMAPS).

Polls the dedicated bounce mailbox (BOUNCE_IMAP_*), parses delivery status reports and
applies them to the original delivery jobs (see app.modules.notifications.bounces).
Processed reports are moved to BOUNCE_IMAP_PROCESSED_FOLDER (or flagged \\Seen), and
every report is applied at most once even if it is fetched again. Messages that are not
delivery reports are left unread for a person to handle.

Usage:  python -m app.bounce_worker --once            # one poll
        python -m app.bounce_worker --interval 120    # service mode
        python -m app.bounce_worker --diagnose        # connect/login/count only
Credentials are read from the environment and never logged.
"""
from __future__ import annotations

import argparse
import imaplib
import logging
import ssl
import time

from app.config import get_settings
from app.database import SessionLocal

log = logging.getLogger('uvicorn.error')


def connect(settings, factory=None):
    factory = factory or (lambda host, port: imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context()))
    client = factory(settings.bounce_imap_host, settings.bounce_imap_port)
    client.login(settings.bounce_imap_username, settings.bounce_imap_password.get_secret_value())
    return client


def _finish(client, uid: bytes, processed_folder: str):
    if processed_folder:
        try:
            client.create(processed_folder)
        except imaplib.IMAP4.error:
            pass  # Already exists.
        status, _ = client.uid('COPY', uid, processed_folder)
        if status == 'OK':
            client.uid('STORE', uid, '+FLAGS', '(\\Deleted)')
            return
    client.uid('STORE', uid, '+FLAGS', '(\\Seen)')


def poll_once(*, factory=None, session_factory=SessionLocal, limit: int = 200) -> dict:
    """Process up to ``limit`` unread reports. Returns counts (no addresses)."""
    from app.modules.notifications.bounces import process_report
    settings = get_settings()
    if not settings.bounce_imap_host:
        return {'enabled': False}
    stats = {'enabled': True, 'fetched': 0, 'reports': 0, 'not_reports': 0, 'applied': {}}
    client = connect(settings, factory)
    try:
        status, _ = client.select(settings.bounce_imap_folder)
        if status != 'OK':
            raise RuntimeError('Bounce mailbox folder could not be selected')
        status, data = client.uid('SEARCH', None, 'UNSEEN')
        uids = (data[0] or b'').split()[:limit] if status == 'OK' and data else []
        for uid in uids:
            status, parts = client.uid('FETCH', uid, '(BODY.PEEK[])')
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if status != 'OK' or not raw:
                continue
            stats['fetched'] += 1
            with session_factory() as db:
                try:
                    results = process_report(db, raw)
                    db.commit()
                except Exception as exc:  # noqa: BLE001 - one bad report never stops the poll
                    db.rollback()
                    log.warning('Bounce report skipped (%s)', type(exc).__name__)
                    continue
            if not results:
                stats['not_reports'] += 1  # Left unread for a person.
                continue
            stats['reports'] += 1
            for result in results:
                stats['applied'][result] = stats['applied'].get(result, 0) + 1
            _finish(client, uid, settings.bounce_imap_processed_folder)
        if settings.bounce_imap_processed_folder:
            client.expunge()
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass
    log.info('Bounce poll fetched=%s reports=%s not_reports=%s applied=%s',
             stats['fetched'], stats['reports'], stats['not_reports'], stats['applied'])
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--diagnose', action='store_true')
    parser.add_argument('--interval', type=int, default=120)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if not settings.bounce_imap_host:
        log.error('BOUNCE_IMAP_HOST is not configured; bounce processing disabled')
        return 2
    if args.diagnose:
        try:
            client = connect(settings)
            status, data = client.select(settings.bounce_imap_folder, readonly=True)
            unseen = client.search(None, 'UNSEEN')[1][0].split() if status == 'OK' else []
            client.logout()
            log.info('Bounce mailbox reachable folder=%s messages=%s unread=%s',
                     settings.bounce_imap_folder, (data or [b'?'])[0].decode(), len(unseen))
            return 0
        except Exception as exc:  # noqa: BLE001
            log.error('Bounce mailbox unavailable (%s)', type(exc).__name__)
            return 1
    while True:
        try:
            poll_once()
        except Exception as exc:  # noqa: BLE001 - keep the service alive
            log.error('Bounce poll failed (%s); will retry', type(exc).__name__)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(30, args.interval))


if __name__ == '__main__':
    raise SystemExit(main())
