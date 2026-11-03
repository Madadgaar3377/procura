"""Encryption for short-lived delivery secrets stored in the transactional outbox."""
from base64 import urlsafe_b64encode
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.field_encryption_key
    if not key:
        if settings.is_production:
            raise RuntimeError('FIELD_ENCRYPTION_KEY is required')
        key = urlsafe_b64encode(sha256(settings.jwt_secret.encode('utf-8')).digest()).decode('ascii')
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, TypeError) as exc:
        raise RuntimeError('FIELD_ENCRYPTION_KEY must be a valid Fernet key') from exc


def encrypt_delivery_secret(value: str) -> str:
    return _fernet().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_delivery_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise RuntimeError('Unable to decrypt queued delivery secret') from exc
