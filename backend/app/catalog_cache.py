"""Bounded L1/L2 cache for safe public projections and scoped admin aggregates.

Endpoints must check authorization before reading. Private contacts, rates,
RFQ details, documents, and authentication secrets must never be stored here.
Explicitly published contact channels follow the supplier's publication consent.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from collections import OrderedDict
from hashlib import sha256
from functools import wraps
from time import monotonic, perf_counter
from typing import Any, cast

from redis.exceptions import RedisError

from app.config import get_settings
from app.redis_client import get_redis, mark_redis_failure

log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_L1: OrderedDict[str, tuple[float, str, int]] = OrderedDict()
_L1_BYTES = 0
_LOCAL_VERSION = 0
_PENDING_INVALIDATION = False
_HITS = 0
_MISSES = 0
_REDIS_HITS = 0
_REDIS_MISSES = 0
_MAX_ITEMS = 256
_MAX_BYTES = 4 * 1024 * 1024
_MAX_VALUE_BYTES = 256 * 1024
_L1_TTL = 45
_EPOCH_KEY = 'procurement:catalog:v2:epoch'
_TIMINGS: dict[str, dict[str, float]] = {}


def _synchronized(handler):
    @wraps(handler)
    def synchronized(*args, **kwargs):
        with _LOCK:
            return handler(*args, **kwargs)
    return synchronized


def _timed(operation):
    def decorate(handler):
        @wraps(handler)
        def timed(*args, **kwargs):
            started = perf_counter()
            try:
                return handler(*args, **kwargs)
            finally:
                elapsed = (perf_counter() - started) * 1000
                with _LOCK:
                    row = _TIMINGS.setdefault(operation, {'calls': 0, 'total_ms': 0.0, 'max_ms': 0.0})
                    row['calls'] += 1
                    row['total_ms'] += elapsed
                    row['max_ms'] = max(row['max_ms'], elapsed)
        return timed
    return decorate


@_synchronized
def _epoch() -> int:
    global _PENDING_INVALIDATION
    client = get_redis()
    if client is None:
        return -1 if get_settings().redis_url else _LOCAL_VERSION
    try:
        if _PENDING_INVALIDATION:
            client.set(_EPOCH_KEY, secrets.randbits(60), nx=True)
            client.incr(_EPOCH_KEY)
            _PENDING_INVALIDATION = False
        val = client.get(_EPOCH_KEY)
        if val is None:
            # Eviction/restart must not resurrect entries from an old epoch 0.
            client.set(_EPOCH_KEY, secrets.randbits(60), nx=True)
            val = client.get(_EPOCH_KEY)
        return int(str(val or 0))
    except (RedisError, OSError, ValueError) as exc:
        mark_redis_failure(exc)
        return -1


def _key(namespace: str, params: dict[str, Any], epoch: int) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    digest = sha256(canonical.encode('utf-8')).hexdigest()
    return f'procurement:catalog:v2:{epoch}:{namespace}:{digest}'


@_timed('version')
def version() -> int:
    """Take a catalog epoch snapshot for a read; use it for any ensuing cache write."""
    return _epoch()


@_timed('read')
def get(namespace: str, params: dict[str, Any], epoch: int | None = None) -> Any | None:
    global _L1_BYTES, _HITS, _MISSES, _REDIS_HITS, _REDIS_MISSES
    epoch = _epoch() if epoch is None else epoch
    if epoch < 0:
        with _LOCK:
            _MISSES += 1
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
    client = get_redis()
    if client is None:
        with _LOCK:
            _MISSES += 1
        return None
    try:
        redis_payload = cast(str | None, client.get(key))
        remaining = client.ttl(key) if redis_payload else 0
    except (RedisError, OSError) as exc:
        mark_redis_failure(exc)
        with _LOCK:
            _MISSES += 1
        return None
    if not redis_payload or len(redis_payload.encode('utf-8')) > _MAX_VALUE_BYTES:
        with _LOCK:
            _REDIS_MISSES += 1
            _MISSES += 1
        return None
    try:
        decoded = json.loads(redis_payload)
        # Never extend an L2 value's life with a new, longer L1 TTL.
        if remaining > 0:
            _store_l1(key, redis_payload, remaining)
        with _LOCK:
            _REDIS_HITS += 1
            _HITS += 1
        return decoded
    except (TypeError, json.JSONDecodeError):
        return None


@_timed('write')
def set(namespace: str, params: dict[str, Any], value: Any, ttl: int = 90, epoch: int | None = None) -> None:
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
    _store_l1(key, payload, ttl)
    client = get_redis()
    if client is None:
        return
    try:
        client.setex(key, max(1, ttl), payload)
    except (RedisError, OSError) as exc:
        mark_redis_failure(exc)


def _store_l1(key: str, payload: str, ttl: int = _L1_TTL) -> None:
    global _L1_BYTES
    size = len(payload.encode('utf-8'))
    if size > _MAX_VALUE_BYTES:
        return
    with _LOCK:
        old = _L1.pop(key, None)
        if old:
            _L1_BYTES -= old[2]
        _L1[key] = (monotonic() + min(_L1_TTL, ttl), payload, size)
        _L1_BYTES += size
        while len(_L1) > _MAX_ITEMS or _L1_BYTES > _MAX_BYTES:
            _, (_, _, removed_size) = _L1.popitem(last=False)
            _L1_BYTES -= removed_size


@_synchronized
def invalidate() -> bool:
    """Advance the shared cache namespace after a committed catalog mutation."""
    global _LOCAL_VERSION, _L1_BYTES, _PENDING_INVALIDATION
    with _LOCK:
        _LOCAL_VERSION += 1
        _L1.clear()
        _L1_BYTES = 0
    client = get_redis()
    _PENDING_INVALIDATION = bool(get_settings().redis_url)
    if client is None:
        return not bool(get_settings().redis_url)
    try:
        # Seed before increment so loss of the epoch key cannot reuse version 1.
        client.set(_EPOCH_KEY, secrets.randbits(60), nx=True)
        client.incr(_EPOCH_KEY)
        _PENDING_INVALIDATION = False
        return True
    except (RedisError, OSError) as exc:
        mark_redis_failure(exc)
        return False


def metrics() -> dict[str, Any]:
    """Return cache health metrics for observability."""
    with _LOCK:
        total_requests = _HITS + _MISSES
        hit_ratio = round((_HITS / total_requests) * 100.0, 1) if total_requests > 0 else 0.0
        return {
            'l1_entries': len(_L1),
            'l1_bytes': _L1_BYTES,
            'max_entries': _MAX_ITEMS,
            'max_bytes': _MAX_BYTES,
            'hits': _HITS,
            'misses': _MISSES,
            'hit_ratio_percent': hit_ratio,
            'redis_hits': _REDIS_HITS,
            'redis_misses': _REDIS_MISSES,
            'timings': {name: {**row, 'mean_ms': round(row['total_ms'] / row['calls'], 3)}
                        for name, row in _TIMINGS.items()},
        }
