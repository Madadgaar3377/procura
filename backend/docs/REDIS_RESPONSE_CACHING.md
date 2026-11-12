# Redis response caching

The existing synchronous Redis pool and bounded catalog L1/L2 cache are reused.
Public response schemas, supplier eligibility, RFQ matching, and private business
queries are unchanged. No dependencies or database migrations were added.

## Endpoints and TTLs

All paths below are relative to `/api/v1`.

| GET endpoint | Redis TTL |
| --- | ---: |
| `/directory/suppliers` | 60 s |
| `/directory/suppliers/{supplier_id}` | 120 s |
| `/directory/suppliers/{supplier_id}/products` | 120 s |
| `/directory/suppliers/{supplier_id}/related` | 60 s |
| `/taxonomy/categories` (including filtered/root/subcategory options) | 1800 s |
| `/taxonomy/materials` (including autocomplete) | 300 s |
| `/imports/materials` (existing organization authorization retained) | 300 s |
| `/taxonomy/industry-catalog` (including its public discipline statistics) | 300 s |
| `/admin/dashboard/analytics` | 30 s |

There is no standalone public platform statistics endpoint in this backend.
Existing unit lookup and admin category summary caches remain, at 180 s and 30 s.
L1 lives for at most 45 s and is capped by the requested TTL and remaining Redis
TTL. Values over 256 KiB bypass storage; L1 is bounded to 256 entries / 4 MiB.

## Keys and authorization

Keys use `procurement:catalog:v2:{epoch}:{endpoint}:{sha256(canonical_params)}`.
FastAPI validates parameters and resolves dependencies before cache access.
Canonical JSON sorts parameter names, includes typed defaults and every handler
filter, pagination argument, supplier ID, and include flag, and normalizes search
whitespace where the underlying query does so. Existing Material Master queries
also normalize search case. Responses contain both languages; their language
scope is bilingual. The new response wrapper explicitly includes visibility.
Admin analytics includes the authenticated admin ID and selected day range.
The current admin permission is platform-wide; no tenant-scoped aggregate is
introduced. Existing authenticated material search shares only public reference
data after its organization permission check.

Only successful dict/list results enter the new response cache. Exceptions,
404s, authentication responses, private RFQs, invitations, quotations, account
profiles, private documents and private contact rows are not cached. Supplier
profile caching uses the existing publication-consent projection, never imported
or account contacts as a fallback. Explicitly published business contact channels
can appear in that public projection. HTTP `Cache-Control: no-store` remains.

## Commit-aware invalidation

SQLAlchemy Session hooks watch organizations, public publication consent,
supplier capabilities, verification, categories, materials and units. ORM
mutations and Session bulk DML advance a shared epoch only after the outer
transaction commits. Savepoint commits do not publish changes prematurely.
Rollback does not advance the epoch. All existing API and import/worker sessions
are covered, so each successful import batch commit makes its public changes
visible. Database changes made outside these application sessions require an
explicit invalidation or waiting for TTL expiry.

Invalidation clears this worker's L1 and advances the Redis epoch. Subsequent
requests in other workers read that epoch before using L1. In-flight readers
write into their original generation, so an old read cannot repopulate the new
generation. Old Redis entries expire naturally. No `KEYS`, database-wide flush,
or unbounded key scan is used. Epoch initialization uses a random integer so
evicting the epoch key cannot resurrect an earlier generation.

When Redis is configured, the same database transaction also inserts an empty
`cache.public.invalidate` event in the existing transactional outbox. Immediate
after-commit invalidation remains the fast path. The existing `app.worker`
replays these events, including after writer crashes, and retries Redis failures
every 15 seconds without exhausting notification retry limits. Replayed events
conservatively advance the generation again. Deploy the updated API, import
workers and outbox worker together, with identical `REDIS_URL` settings. No new
queue or worker architecture is required.

Normal successful invalidation makes subsequent requests fresh immediately.
Redis/DB cannot commit atomically: if Redis invalidation fails, another worker
can see an old entry after Redis recovery until a pending local invalidation or
durable outbox retry runs; TTL is the final bound. This is an explicit recovery
window, not a claim of distributed linearizable reads. The outbox worker must
run for durable recovery. Requests already in flight can finish with their
original snapshot.

## Connection lifecycle and fallback

One pool per process has 20 maximum connections, 0.5 s connect/command timeouts,
30 s connection health checks, and disabled automatic command retries. A lock
prevents concurrent first-use pool creation. Lifespan initializes the client and
closes/disconnects it at shutdown. These SQLAlchemy handlers are synchronous,
so they share the synchronous Redis client in FastAPI worker threads. Redis pool
lifecycle and middleware rate-limit calls use `asyncio.to_thread` to keep their
blocking work off the event loop. A second async pool is unnecessary.

Redis exceptions trip the existing 10 s circuit breaker and emit rate-limited,
sanitized error-type logging. If Redis is configured but unavailable, response
cache reads/writes are bypassed and handlers query PostgreSQL. With no Redis
configured, the existing local-only development cache remains supported.

The existing **production rate limiter deliberately fails closed with 503**
when Redis is down. This security policy is preserved: response-cache fallback
does not bypass middleware or guarantee whole-API availability during a Redis
outage. The repository's existing production readiness gate also remains.

## Observability and frontend

`GET /api/v1/db/performance` now requires an administrator. It retains existing
diagnostic fields and adds aggregate database query durations and cache-operation
timings. Cache hit/miss/L2 counters remain process-local; aggregate them externally
for multiple workers. DEBUG logs record fixed endpoint identities, hit status,
fill duration, response-cache duration and SQL duration, without SQL text,
parameters, search strings, tokens, Redis URLs or contact data.

The existing frontend `useApi` scoped cache already deduplicates requests.
Directory remounts now reuse it for 15 s and category lookups for 60 s. Mutation,
cross-tab and focus invalidation remain. No new query/state library or polling
was added. Existing billing and RFQ progress polling was retained because it
serves live workflow behavior outside this public caching change.

## Measurements

Command: `python scripts/benchmark_response_cache.py`.
Synthetic isolated SQLite, 1000 suppliers, 2000 capabilities, 1000 materials,
30 measured TestClient requests after five warmups. Real in-process L1 cache;
admin authentication is overridden. These are **not PostgreSQL, live Redis
network, Nginx or VPS measurements**. Concurrent local tests also introduce noise.

| Endpoint | Uncached p50 ms | Cached p50 ms | Uncached SELECTs | Cached SELECTs |
| --- | ---: | ---: | ---: | ---: |
| Directory | 38.610 | 8.336 | 3 | 0 |
| Supplier profile | 13.886 | 8.333 | 2 | 0 |
| Categories | 9.237 | 7.763 | 1 | 0 |
| Material search | 26.259 | 9.175 | 1 | 0 |
| Admin analytics | 49.944 | 10.346 | 22 | 0 |

Authentication queries in a real deployment still run before cache access.
Redis L2 hits, outages and recovery are covered with a deterministic Redis test
double. No live Redis server was available; Docker's local daemon was absent.
Live PostgreSQL/Redis performance and multi-process failure tests remain required
before claiming production performance or availability.

## Files changed for this task

- `app/redis_client.py`: pool initialization locking, explicit retry bounds and shutdown.
- `app/catalog_cache.py`: v2 epochs, TTL correctness, failure/miss and timing metrics.
- `app/response_cache.py`: explicit safe GET caching after dependency checks.
- `app/cache_invalidation.py`: commit/rollback/bulk/savepoint hooks and durable retry events.
- `app/database.py`: hook registration and safe SQL timings.
- `app/main.py`: Redis lifespan and nonblocking rate-limit middleware calls.
- `app/worker.py`: durable cache invalidation replay using the existing outbox.
- `app/modules/capabilities/directory.py`: public directory/profile/product/related caching.
- `app/modules/taxonomy/router.py`: TTLs and industry statistics caching.
- `app/modules/imports/materials.py`: authorized material search TTL.
- `app/modules/admin/analytics.py`: caching around the existing uncommitted dashboard handler.
- `app/modules/database/router.py`: admin-only diagnostics and query timing metrics.
- `../frontend/lib/api/use-api.ts`: reuse existing frontend public lookup caching.
- `../docs/openapi.json`: refresh diagnostics authentication contract; preserve existing dashboard work.
- `tests/test_response_cache.py`, `scripts/benchmark_response_cache.py`: isolated regressions and measurements.
- `docs/REDIS_RESPONSE_CACHING.md`, `IMPLEMENTATION_STATUS.md`: findings and actual validation.

OpenAPI frontend artifacts were regenerated. Other pre-existing dashboard/model/
migration/frontend changes in the workspace were preserved.

## Validation results

- Full backend `python -m pytest -q`, with `APP_ENV=test`,
  `DATABASE_URL=sqlite:///:memory:` and empty `REDIS_URL`: **129 passed,
  5 skipped**, 51.41 s. Thirty new cache tests cover SQL-to-cache/L2 transitions,
  key/filter separation, authorization before hits, private contact exclusion,
  consent revocation, archive visibility, material partial search, actual supplier
  import commits, transaction rollback/savepoints, stale in-flight fills, epoch
  eviction, Redis outages, durable invalidation replay, and shared pool lifecycle.
- The five skips are existing opt-in PostgreSQL/retained-source/hosted checks;
  no production database was modified. L2 uses a deterministic Redis double.
- Frontend `npm.cmd run typecheck`: passed.
- `node --test tests/api-client.test.cjs tests/directory-pagination.test.cjs
  tests/supplier-profile.test.cjs tests/industries.test.cjs`: **21 passed**.
- OpenAPI exported and frontend artifacts regenerated successfully.
- `git diff --check`: passed (only repository line-ending notices).
- Existing warnings: Starlette's httpx TestClient deprecation and an inaccessible
  pre-existing pytest cache directory. No dependency changes were made.
- Postman CLI was unavailable; these checks use the existing pytest/Node suites.
