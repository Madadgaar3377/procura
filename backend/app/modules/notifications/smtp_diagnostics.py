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
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # Recipient-specific wording first: "mailbox full / quota exceeded" is the recipient's
    # mailbox, not the sending account's quota.
    ('recipient_rejected', re.compile(r'user unknown|unknown user|no such (?:user|mailbox)|mailbox '
                                      r'(?:unavailable|not found|disabled|full)|does not exist|'
                                      r'recipient (?:address )?rejected|invalid recipient', re.I)),
    ('rate_limited', re.compile(r'rate.?limit|too many|quota|limit (?:exceeded|reached)|exceeded|throttl|'
                                r'try again later|sending limit|daily limit|hourly limit', re.I)),
    ('sender_rejected', re.compile(r'sender|mail from|from address|not owned|not allowed to send|'
                                   r'authenticated user|spoof|alias|on behalf|envelope from|relay', re.I)),
    ('policy_rejected', re.compile(r'spam|content|policy|blocked|blacklist|block ?list|reputation|'
                                   r'dmarc|spf|dkim|virus|malware|phish|suspicious|not accepted', re.I)),
)


def sanitize_smtp_text(value) -> str:
    """Provider response text without recipient/sender addresses, IPs or long tokens."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    text = ' '.join(str(value or '').split())
    text = _EMAIL_RE.sub('<address>', text)
    text = _IPV6_RE.sub('<ip>', _IPV4_RE.sub('<ip>', text))
    text = _TOKEN_RE.sub('<token>', text)
    return text[:500]


def classify(code: int | None, text: str, stage: str) -> tuple[str, bool, bool]:
    """(failure_class, retryable, provider_level) for one SMTP rejection."""
    enhanced = ENHANCED_RE.search(text or '')
    klass = int(enhanced.group(1)) if enhanced else (code // 100 if code else 0)
    matched = next((name for name, pattern in _PATTERNS if pattern.search(text or '')), None)
    if stage == 'mail_from' and matched not in {'rate_limited', 'policy_rejected'}:
        matched = 'sender_rejected'
    if stage == 'rcpt_to' and matched is None:
        matched = 'recipient_rejected'
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
            'response': text, 'failure_class': failure, 'retryable': retryable,
            'permanent': not retryable, 'provider_level': provider_level}


def describe_recipients(exc: SMTPRecipientsRefused) -> dict:
    """RCPT TO rejections: one code/text per refused recipient (addresses redacted)."""
    entries = [(code, sanitize_smtp_text(text)) for code, text in exc.recipients.values()]
    code, text = entries[0] if entries else (None, '')
    failure, retryable, provider_level = classify(code, text, 'rcpt_to')
    if entries:
        retryable = all(400 <= c < 500 for c, _ in entries)
    enhanced = ENHANCED_RE.search(text)
    return {'stage': 'rcpt_to', 'code': code, 'enhanced_code': enhanced.group(0) if enhanced else None,
            'response': text, 'failure_class': failure, 'retryable': retryable,
            'permanent': not retryable, 'provider_level': provider_level,
            'permanent_recipient': bool(entries) and all(500 <= c < 600 for c, _ in entries)
            and failure == 'recipient_rejected'}


def summary(detail: dict) -> str:
    """One-line operator message, safe to store in outbox.last_error."""
    code = ' '.join(str(v) for v in (detail.get('code'), detail.get('enhanced_code')) if v)
    kind = 'temporary' if detail.get('retryable') else 'permanent'
    response = detail.get('response') or 'no response text'
    return f"SMTP {detail['stage']} rejected ({code or 'no code'}, {kind}, {detail['failure_class']}): {response}"[:500]
