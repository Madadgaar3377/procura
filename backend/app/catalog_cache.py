"""Bounded cache for public Material Master lookups.

Authentication is checked by each endpoint before reading this cache. No tenant,
pricing, RFQ, contact, or authentication data belongs here.
"""
from __future__ import annotations

import json
import logging
import random
import threading
from collections import OrderedDict
from hashlib import sha256
from time import monotonic
from typing import Any, cast

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings

log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_L1: OrderedDict[str, tuple[float, str, int]] = OrderedDict()
_L1_BYTES = 0
_LOCAL_VERSION = 0
_PENDING_INVALIDATION = False
_REDIS: Redis | None = None
_REDIS_DISABLED_UNTIL = 0.0
_HITS = 0
_MISSES = 0
_REDIS_HITS = 0
_REDIS_MISSES = 0
_MAX_ITEMS = 128
_MAX_BYTES = 2 * 1024 * 1024
_MAX_VALUE_BYTES = 128 * 1024
_L1_TTL = 45


def _redis() -> Redis | None:
    global _REDIS
    url = get_settings().redis_url
    if not url or monotonic() < _REDIS_DISABLED_UNTIL:
        return None
    if _REDIS is None:
        _REDIS = cast(Redis, Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2,
                                           health_check_interval=30, decode_responses=True))
    return _REDIS


def _epoch() -> int:
    global _REDIS_DISABLED_UNTIL, _PENDING_INVALIDATION
    client = _redis()
    if client is None:
        return -1 if get_settings().redis_url else _LOCAL_VERSION
    try:
        if _PENDING_INVALIDATION:
            client.incr('procurement:catalog:v1:epoch')
            _PENDING_INVALIDATION = False
        return int(str(cast(Any, client.get('procurement:catalog:v1:epoch')) or 0))
    except RedisError:
        _REDIS_DISABLED_UNTIL = monotonic() + 5
        log.warning('Material catalog Redis cache unavailable; using bounded local cache and PostgreSQL')
        return -1


def _key(namespace: str, params: dict[str, Any], epoch: int) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    digest = sha256(canonical.encode('utf-8')).hexdigest()
    return f'procurement:catalog:v1:{epoch}:{namespace}:{digest}'


def version() -> int:
    """Take a catalog epoch snapshot for a read; use it for any ensuing cache write."""
    return _epoch()


def get(namespace: str, params: dict[str, Any], epoch: int | None = None) -> Any | None:
    global _L1_BYTES, _REDIS_DISABLED_UNTIL, _HITS, _MISSES, _REDIS_HITS, _REDIS_MISSES
    epoch = _epoch() if epoch is None else epoch
    if epoch < 0:
        return None
    key = _key(namespace, params, epoch)
    now = monotonic()
    with _LOCK:
        entry = _L1.get(key)
        if entry:
            expires, payload, size = entry
            if expires > now:
                _L1.move_to_end(key)
                _HITS += 1
                return json.loads(payload)
            _L1_BYTES -= size
            _L1.pop(key, None)
    client = _redis()
    if client is None:
        with _LOCK:
            _MISSES += 1
        return None
    try:
        redis_payload = cast(str | None, client.get(key))
    except RedisError:
        _REDIS_DISABLED_UNTIL = monotonic() + 5
        log.warning('Material catalog Redis cache read failed; falling back to PostgreSQL')
        return None
    if not redis_payload or len(redis_payload.encode('utf-8')) > _MAX_VALUE_BYTES:
        with _LOCK:
            _REDIS_MISSES += 1
            _MISSES += 1
        return None
    _store_l1(key, redis_payload)
    try:
        with _LOCK:
            _REDIS_HITS += 1
            _HITS += 1
        return json.loads(redis_payload)
    except (TypeError, json.JSONDecodeError):
        return None


def set(namespace: str, params: dict[str, Any], value: Any, ttl: int = 90, epoch: int | None = None) -> None:
    global _REDIS_DISABLED_UNTIL
    epoch = _epoch() if epoch is None else epoch
    if epoch < 0:
        return
    key = _key(namespace, params, epoch)
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)
    except (TypeError, ValueError):
        return
    size = len(payload.encode('utf-8'))
    if size > _MAX_VALUE_BYTES:
        return
    _store_l1(key, payload)
    client = _redis()
    if client is None:
        return
    try:
        client.setex(key, max(1, ttl + random.randint(0, max(1, ttl // 5))), payload)
    except RedisError:
        _REDIS_DISABLED_UNTIL = monotonic() + 5
        log.warning('Material catalog Redis cache write failed; PostgreSQL remains authoritative')


def _store_l1(key: str, payload: str) -> None:
    global _L1_BYTES
    size = len(payload.encode('utf-8'))
    if size > _MAX_VALUE_BYTES:
        return
    with _LOCK:
        old = _L1.pop(key, None)
        if old:
            _L1_BYTES -= old[2]
        _L1[key] = (monotonic() + _L1_TTL, payload, size)
        _L1_BYTES += size
        while len(_L1) > _MAX_ITEMS or _L1_BYTES > _MAX_BYTES:
            _, (_, _, removed_size) = _L1.popitem(last=False)
            _L1_BYTES -= removed_size


def invalidate() -> None:
    """Advance the shared cache namespace after a committed catalog mutation."""
    global _LOCAL_VERSION, _L1_BYTES, _REDIS_DISABLED_UNTIL, _PENDING_INVALIDATION
    with _LOCK:
        _LOCAL_VERSION += 1
        _L1.clear()
        _L1_BYTES = 0
    client = _redis()
    _PENDING_INVALIDATION = bool(get_settings().redis_url)
    if client is None:
        return
    try:
        client.incr('procurement:catalog:v1:epoch')
        _PENDING_INVALIDATION = False
    except RedisError:
        _REDIS_DISABLED_UNTIL = monotonic() + 5
        log.warning('Material catalog Redis invalidation failed; bypassing cache until the shared epoch can advance')


def metrics() -> dict[str, int]:
    """Return process-local bounded-cache size for protected diagnostics/tests."""
    with _LOCK:
        return {'l1_entries': len(_L1), 'l1_bytes': _L1_BYTES, 'max_entries': _MAX_ITEMS,
                'max_bytes': _MAX_BYTES, 'hits': _HITS, 'misses': _MISSES,
                'redis_hits': _REDIS_HITS, 'redis_misses': _REDIS_MISSES}
