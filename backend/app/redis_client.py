"""Centralized, resilient Redis connection pooling and health management."""
from __future__ import annotations

import logging
from time import monotonic, perf_counter
from typing import Any

from redis import ConnectionPool, Redis
from redis.exceptions import RedisError

from app.config import get_settings

log = logging.getLogger(__name__)

_POOL: ConnectionPool | None = None
_CLIENT: Redis | None = None
_REDIS_DISABLED_UNTIL: float = 0.0
_LAST_FAILURE_LOG: float = 0.0
_COOLDOWN_SECONDS: float = 10.0
_COMMAND_TIMEOUT: float = 0.5


def _init_pool() -> ConnectionPool | None:
    global _POOL
    settings = get_settings()
    if not settings.redis_url:
        return None
    if _POOL is None:
        _POOL = ConnectionPool.from_url(
            settings.redis_url,
            max_connections=20,
            socket_connect_timeout=_COMMAND_TIMEOUT,
            socket_timeout=_COMMAND_TIMEOUT,
            health_check_interval=30,
            decode_responses=True,
        )
    return _POOL


def get_redis() -> Redis | None:
    """Return an active pooled Redis client if configured and healthy, or None if in cooldown."""
    global _CLIENT, _REDIS_DISABLED_UNTIL, _LAST_FAILURE_LOG
    settings = get_settings()
    if not settings.redis_url:
        return None
    now = monotonic()
    if now < _REDIS_DISABLED_UNTIL:
        return None
    try:
        if _CLIENT is None:
            pool = _init_pool()
            if pool is None:
                return None
            _CLIENT = Redis(connection_pool=pool)
        return _CLIENT
    except Exception as exc:
        mark_redis_failure(exc)
        return None


def mark_redis_failure(exc: Exception | None = None) -> None:
    """Trip the Redis circuit breaker for _COOLDOWN_SECONDS to prevent retry storms."""
    global _REDIS_DISABLED_UNTIL, _LAST_FAILURE_LOG
    now = monotonic()
    _REDIS_DISABLED_UNTIL = now + _COOLDOWN_SECONDS
    if now - _LAST_FAILURE_LOG > _COOLDOWN_SECONDS:
        _LAST_FAILURE_LOG = now
        err_name = type(exc).__name__ if exc else "Unknown"
        log.warning(
            "Redis unavailable (%s); degrading gracefully to local memory and PostgreSQL for %ss",
            err_name,
            int(_COOLDOWN_SECONDS),
        )


def is_redis_available() -> bool:
    """Return True if Redis is configured, not in cooldown, and responds to PING."""
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except (RedisError, OSError) as exc:
        mark_redis_failure(exc)
        return False


def ping_redis() -> bool:
    """Public probe for health endpoints."""
    return is_redis_available()


def redis_latency_ms() -> float | None:
    """Measure roundtrip latency to Redis in milliseconds."""
    client = get_redis()
    if client is None:
        return None
    try:
        t0 = perf_counter()
        client.ping()
        return round((perf_counter() - t0) * 1000.0, 2)
    except Exception as exc:
        mark_redis_failure(exc)
        return None


def redis_metrics() -> dict[str, Any]:
    """Return safe Redis health and configuration status for observability."""
    settings = get_settings()
    configured = bool(settings.redis_url)
    available = is_redis_available() if configured else False
    now = monotonic()
    in_cooldown = now < _REDIS_DISABLED_UNTIL
    return {
        "configured": configured,
        "available": available,
        "in_cooldown": in_cooldown,
        "cooldown_remaining_seconds": max(0.0, round(_REDIS_DISABLED_UNTIL - now, 1)) if in_cooldown else 0.0,
        "latency_ms": redis_latency_ms() if available else None,
    }
