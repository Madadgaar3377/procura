"""Redis-backed fixed-window rate limits with a development-only local fallback."""
from __future__ import annotations

from hashlib import sha256
import math
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request

from app.config import get_settings
from app.proxy import get_client_ip
from app.redis_client import get_redis, mark_redis_failure, ping_redis

_local: dict[str, tuple[int, float]] = {}
_local_lock = Lock()

_INCREMENT_WINDOW = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    return {count, tonumber(ARGV[1])}
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


def _local_consume(key: str, window_seconds: int) -> tuple[int, int]:
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
        retry_after = max(1, math.ceil(expires - now))
        return count, retry_after


def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
    scope_by_ip: bool = True,
) -> None:
    """Consume one limit slot and return HTTP 429 when the window is exhausted."""
    settings = get_settings()
    if not scope_by_ip or (request.client and request.client.host == 'shared'):
        address = 'shared'
    else:
        address = get_client_ip(request)
    raw_key = f"{scope}:{address}:{identity}"
    key = f"saudi-procurement:rate:{sha256(raw_key.encode()).hexdigest()}"

    count = 0
    retry_after = window_seconds
    client = get_redis() if (settings.redis_url and not settings.is_test) else None

    if client is not None:
        try:
            result = client.eval(_INCREMENT_WINDOW, 1, key, window_seconds)
            if isinstance(result, (list, tuple)) and len(result) >= 2:
                count = int(result[0])
                retry_after = max(1, int(result[1]))
            else:
                count = int(result)
        except Exception as exc:
            mark_redis_failure(exc)
            if settings.is_production:
                raise HTTPException(
                    503,
                    'Rate limiting is temporarily unavailable',
                    headers={'Retry-After': '10'},
                ) from exc
            count, retry_after = _local_consume(key, window_seconds)
    else:
        if settings.is_production:
            raise HTTPException(
                503,
                'Rate limiting is temporarily unavailable',
                headers={'Retry-After': '10'},
            )
        count, retry_after = _local_consume(key, window_seconds)

    if count > limit:
        raise HTTPException(
            429,
            'Too many requests; try again later',
            headers={'Retry-After': str(retry_after)},
        )
