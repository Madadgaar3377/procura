"""Sanitized SMTP provider responses and permanent/temporary failure classification.

smtplib reports a rejection as an exception carrying the provider's numeric code and
response text. Earlier code stored only "SMTP data rejected (code 554)", which hid the
provider's reason and labelled MAIL FROM / RCPT TO rejections as DATA. This module keeps
the provider text (with addresses, IPs and long tokens redacted), the exact SMTP phase
and a failure class, so an operator can see *why* the provider refused a message.

Classification (RFC 5321 / RFC 3463):
- 4xx (or enhanced 4.x.x): temporary -> retry with bounded backoff.
- 5xx (or enhanced 5.x.x): permanent -> never retried for this message, except
  provider rate/quota rejections, which some providers (incorrectly) send as 554 and
  which are retried with the normal bounded attempt limit.
- ``provider_level``: the rejection concerns the sending account, sender address or
  message policy rather than one recipient; the worker pauses the email lane instead
  of submitting every queued message into the same rejection.
"""
from __future__ import annotations

import re
from smtplib import (
    SMTPAuthenticationError,
    SMTPDataError,
    SMTPRecipientsRefused,
    SMTPResponseException,
    SMTPSenderRefused,
)

_EMAIL_RE = re.compile(r'[\w.+%-]+@[\w-]+(?:\.[\w-]+)+')
_IPV4_RE = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b')
_IPV6_RE = re.compile(r'\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{1,4}\b', re.I)
# Long opaque tokens (session ids, base64 blobs) are not useful and may be sensitive.
_TOKEN_RE = re.compile(r'\b[A-Za-z0-9+/=_-]{40,}\b')
ENHANCED_RE = re.compile(r'\b([245])\.(\d{1,3})\.(\d{1,3})\b')

# Ordered: the first matching class wins.
_MAILBOX_FULL = re.compile(r'mailbox (?:is )?full|over ?quota|mailbox quota|quota (?:for|of) (?:the )?(?:user|mailbox|recipient)|'
                           r'insufficient (?:system )?storage|user.{0,30}(?:has )?exceeded.{0,20}(?:storage|quota)', re.I)
_RECIPIENT = re.compile(r'user unknown|unknown user|no such (?:user|mailbox|recipient)|mailbox (?:unavailable|not found|disabled)|'
                        r'does not exist|recipient (?:address )?rejected|invalid recipient|address rejected|'
                        r'unrouteable address|no mailbox|account (?:has been )?disabled|bad destination', re.I)
_RATE = re.compile(r'rate.?limit|too many|quota|limit (?:exceeded|reached)|exceeded|throttl|try again later|'
                   r'sending limit|daily limit|hourly limit|temporarily deferred', re.I)
_SENDER = re.compile(r'sender|mail from|from address|not owned|not allowed to send|authenticated user|spoof|alias|'
                     r'on behalf|envelope from|relay', re.I)
_SPAM = re.compile(r'spam|content|policy|blocked|blacklist|block ?list|reputation|dmarc|spf|dkim|virus|malware|'
                   r'phish|suspicious|not accepted|junk|bulk', re.I)

# RFC 3463 enhanced status subjects/details that decide the class on their own.
_ENHANCED_CLASS = {
    (1, 1): 'recipient_rejected', (1, 2): 'recipient_rejected', (1, 3): 'recipient_rejected',
    (1, 6): 'recipient_rejected', (1, 10): 'recipient_rejected', (2, 1): 'recipient_rejected',
    (1, 7): 'sender_rejected', (1, 8): 'sender_rejected',
    (2, 2): 'mailbox_full',
    (7, 8): 'authentication_failed', (7, 9): 'authentication_failed',
    (7, 23): 'policy_rejected', (7, 24): 'policy_rejected', (7, 25): 'policy_rejected',
    (7, 26): 'policy_rejected', (7, 27): 'policy_rejected',
}

# Operator-facing categories (stable vocabulary shown in the admin dashboard).
CATEGORY = {
    'authentication_failed': 'authentication_failure',
    'recipient_rejected': 'invalid_recipient',
    'mailbox_full': 'mailbox_full',
    'rate_limited': 'rate_limit',
    'policy_rejected': 'spam_rejection',
    'sender_rejected': 'permanent_rejection',
    'permanent': 'permanent_rejection',
    'temporary': 'temporary_rejection',
    'unknown': 'temporary_rejection',
}


def sanitize_smtp_text(value) -> str:
    """Provider response text without recipient/sender addresses, IPs or long tokens."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    text = ' '.join(str(value or '').split())
    text = _EMAIL_RE.sub('<address>', text)
    text = _IPV6_RE.sub('<ip>', _IPV4_RE.sub('<ip>', text))
    text = _TOKEN_RE.sub('<token>', text)
    return text[:500]


def _text_class(text: str, stage: str) -> str | None:
    if _MAILBOX_FULL.search(text):
        return 'mailbox_full'
    # Outside RCPT TO a sender/relay phrase is about the sending account, even when the
    # sentence also says "does not exist" (e.g. "sender domain does not exist").
    order = ((_RECIPIENT, 'recipient_rejected'), (_RATE, 'rate_limited'), (_SENDER, 'sender_rejected'),
             (_SPAM, 'policy_rejected'))
    if stage != 'rcpt_to':
        order = ((_SENDER, 'sender_rejected'), (_RATE, 'rate_limited'), (_RECIPIENT, 'recipient_rejected'),
                 (_SPAM, 'policy_rejected'))
    return next((name for pattern, name in order if pattern.search(text)), None)


def classify(code: int | None, text: str, stage: str) -> tuple[str, bool, bool]:
    """(failure_class, retryable, provider_level) for one SMTP rejection."""
    text = text or ''
    enhanced = ENHANCED_RE.search(text)
    klass = int(enhanced.group(1)) if enhanced else (code // 100 if code else 0)
    matched = _ENHANCED_CLASS.get((int(enhanced.group(2)), int(enhanced.group(3)))) if enhanced else None
    worded = _text_class(text, stage)
    # 5.7.0/5.7.1 and generic codes carry no class of their own: the wording decides.
    if matched is None or (matched == 'policy_rejected' and worded in {'rate_limited', 'sender_rejected'}):
        matched = worded or matched
    if stage == 'mail_from' and matched not in {'rate_limited', 'policy_rejected', 'authentication_failed'}:
        matched = 'sender_rejected'
    if stage == 'rcpt_to' and matched is None:
        matched = 'recipient_rejected'
    if matched == 'authentication_failed':
        return 'authentication_failed', False, True
    if matched == 'mailbox_full':
        # The recipient's own mailbox: a soft bounce, retried with bounded backoff and
        # never suppresses the address or pauses the lane.
        return 'mailbox_full', True, False
    if klass == 4:
        return (matched or 'temporary'), True, matched in {'rate_limited', 'sender_rejected', 'policy_rejected'}
    if klass == 5:
        if matched == 'rate_limited':
            return 'rate_limited', True, True
        if matched in {'sender_rejected', 'policy_rejected'}:
            return matched, False, True
        if matched == 'recipient_rejected':
            return 'recipient_rejected', False, False
        # A bare 5xx at DATA without a recognisable reason is permanent for this message;
        # the worker pauses after repeated ones (see notification_worker).
        return 'permanent', False, False
    return 'unknown', True, False


def describe(exc: SMTPResponseException, stage_hint: str) -> dict:
    """Structured, storable description of an SMTP rejection."""
    if isinstance(exc, SMTPSenderRefused):
        stage = 'mail_from'
    elif isinstance(exc, SMTPDataError):
        stage = 'data'
    elif isinstance(exc, SMTPAuthenticationError):
        stage = 'authenticate'
    else:
        stage = stage_hint
    text = sanitize_smtp_text(getattr(exc, 'smtp_error', b''))
    code = getattr(exc, 'smtp_code', None)
    if isinstance(exc, SMTPAuthenticationError):
        failure, retryable, provider_level = 'authentication_failed', False, True
    else:
        failure, retryable, provider_level = classify(code, text, stage)
    enhanced = ENHANCED_RE.search(text)
    return {'stage': stage, 'code': code, 'enhanced_code': enhanced.group(0) if enhanced else None,
            'response': text, 'failure_class': failure, 'category': CATEGORY.get(failure, 'permanent_rejection'),
            'retryable': retryable, 'permanent': not retryable, 'provider_level': provider_level}


def describe_recipients(exc: SMTPRecipientsRefused) -> dict:
    """RCPT TO rejections: one code/text per refused recipient (addresses redacted)."""
    entries = [(code, sanitize_smtp_text(text)) for code, text in exc.recipients.values()]
    code, text = entries[0] if entries else (None, '')
    failure, retryable, provider_level = classify(code, text, 'rcpt_to')
    if entries and failure != 'mailbox_full':
        retryable = all(400 <= c < 500 for c, _ in entries)
    enhanced = ENHANCED_RE.search(text)
    return {'stage': 'rcpt_to', 'code': code, 'enhanced_code': enhanced.group(0) if enhanced else None,
            'response': text, 'failure_class': failure, 'category': CATEGORY.get(failure, 'permanent_rejection'),
            'retryable': retryable,
            'permanent': not retryable, 'provider_level': provider_level,
            'permanent_recipient': bool(entries) and all(500 <= c < 600 for c, _ in entries)
            and failure == 'recipient_rejected'}


def summary(detail: dict) -> str:
    """One-line operator message, safe to store in outbox.last_error."""
    code = ' '.join(str(v) for v in (detail.get('code'), detail.get('enhanced_code')) if v)
    kind = 'temporary' if detail.get('retryable') else 'permanent'
    response = detail.get('response') or 'no response text'
    return f"SMTP {detail['stage']} rejected ({code or 'no code'}, {kind}, {detail['failure_class']}): {response}"[:500]
