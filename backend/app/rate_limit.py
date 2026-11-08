"""Redis-backed fixed-window rate limits with a development-only local fallback."""
from __future__ import annotations

from hashlib import sha256
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request

from app.config import get_settings
from app.redis_client import get_redis, mark_redis_failure, ping_redis

_local: dict[str, tuple[int, float]] = {}
_local_lock = Lock()

_INCREMENT_WINDOW = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


def _local_consume(key: str, window_seconds: int) -> int:
    now = monotonic()
    with _local_lock:
        count, expires = _local.get(key, (0, now + window_seconds))
        if expires <= now:
            count, expires = 0, now + window_seconds
        count += 1
        _local[key] = (count, expires)
        if len(_local) > 10_000:
            for old_key, (_, old_expiry) in list(_local.items()):
                if old_expiry <= now:
                    _local.pop(old_key, None)
        return count


def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Consume one limit slot and return HTTP 429 when the window is exhausted."""
    settings = get_settings()
    address = request.client.host if request.client else 'unknown'
    raw_key = f"{scope}:{address}:{identity}"
    key = f"saudi-procurement:rate:{sha256(raw_key.encode()).hexdigest()}"

    count = 0
    client = get_redis() if (settings.redis_url and not settings.is_test) else None

    if client is not None:
        try:
            count = int(client.eval(_INCREMENT_WINDOW, 1, key, window_seconds))
        except Exception as exc:
            mark_redis_failure(exc)
            if settings.is_production:
                raise HTTPException(503, 'Rate limiting is temporarily unavailable') from exc
            count = _local_consume(key, window_seconds)
    else:
        if settings.is_production:
            raise HTTPException(503, 'Rate limiting is temporarily unavailable')
        count = _local_consume(key, window_seconds)

    if count > limit:
        raise HTTPException(429, 'Too many requests; try again later', headers={'Retry-After': str(window_seconds)})
