"""Explicit opt-in caching for synchronous GET handlers, after dependency checks.

Only decorate handlers whose return value is a safe public projection or a
permission-scoped aggregate. FastAPI resolves authentication before calling us.
"""
from functools import wraps
from inspect import signature
import logging
from time import perf_counter

from fastapi.encoders import jsonable_encoder

from app import catalog_cache

log = logging.getLogger(__name__)


def cached_get(namespace: str, ttl: int, *, admin_only: bool = False):
    def decorate(handler):
        sig = signature(handler)

        @wraps(handler)
        def cached(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            params = {key: value for key, value in values.items()
                      if key not in {'db', 'admin', 'user', 'response'}}
            if isinstance(params.get('q'), str):
                params['q'] = ' '.join(params['q'].split())
            # Parameters are typed/validated by FastAPI. Preserve strings exactly
            # unless the underlying SQL applies the same normalization.
            params['visibility'] = 'admin' if admin_only else 'public'
            params['language'] = 'bilingual'
            if admin_only:
                admin = values.get('admin')
                if admin is None or not admin.is_admin:
                    from fastapi import HTTPException
                    raise HTTPException(403, 'Administrator access required')
                params['principal'] = admin.id
            started = perf_counter()
            epoch = catalog_cache.version()
            value = catalog_cache.get(namespace, params, epoch)
            hit = value is not None
            if not hit:
                query_started = perf_counter()
                value = handler(*args, **kwargs)
                query_ms = (perf_counter() - query_started) * 1000
                # Exceptions never reach this point; never cache Response objects.
                if isinstance(value, (dict, list)):
                    catalog_cache.set(namespace, params, jsonable_encoder(value), ttl=ttl, epoch=epoch)
                log.debug('cache_fill endpoint=%s query_ms=%.2f', namespace, query_ms)
            log.debug('cache_read endpoint=%s hit=%s duration_ms=%.2f',
                      namespace, hit, (perf_counter() - started) * 1000)
            return value

        return cached
    return decorate
