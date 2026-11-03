# Direct API, bounded database waits and six-character passwords

## Request path

Application requests and uploads now go directly from the browser to FastAPI `/api/v1`, without the Next.js `/api/backend` hop. Configure `NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1` before building/starting the frontend, or omit it in local development to use the page's hostname on port 8000. Use the same hostname spelling for frontend and backend (localhost with localhost, or 127.0.0.1 with 127.0.0.1) so SameSite cookies work. Rebuild after changing NEXT_PUBLIC_API_URL. It is a public backend address, never a credential.

FastAPI `CORS_ORIGINS` must include the exact frontend origin. The workspace config includes localhost and 127.0.0.1 port 3000, preserving existing allowed origins. Other frontend ports/domains must be listed explicitly. Browser login, refresh and logout use `/auth/browser/login`, `/auth/browser/refresh` and `/auth/browser/logout`; return no access/refresh tokens in JSON and set HttpOnly SameSite=Lax cookies. Authenticated mutations require an allowed Origin. Existing bearer-token endpoints and the legacy Next.js proxy remain for compatibility, but the application client no longer uses the proxy. Keep frontend and API on the same site for cookie authentication.

Concurrent identical GETs are coalesced. StrictMode/unmount cleanup no longer cancels and immediately duplicates shared reads. Parallel expired-cookie requests share one refresh; mutation requests are never automatically repeated after a timeout or 503. Uploads use the direct API and refresh/retry only on an authentication denial. Development OTP fields are excluded from browser-origin responses and stripped defensively by the client. CORS exposes Retry-After, request IDs and backend Server-Timing.

## Performance changes

The supplied logs show Next.js taking only a few milliseconds while waiting up to its 20-second upstream timeout. Removing that hop removes this particular timeout but cannot establish hosted database speed. Signup now uses one duplicate lookup and avoids the extra post-commit refresh. Admin users use a window count rather than separate count/list queries. Permanent user deletion updates/drops queued deliveries in bulk rather than fetching and processing them individually. Category counts and measurement units use the existing invalidated catalogue cache.

PostgreSQL connections now have bounded connect, pool, lock, statement and idle-transaction waits. Defaults: 5s connect/pool, 3s lock, 15s statement, 30s idle transaction. Backend database failures return sanitized HTTP 503 and Retry-After instead of an opaque proxy 502. Server-Timing records backend duration. Configure these limits for measured workload requirements; connection-scoped limits apply to API/worker sessions. Migration commands use their own bounded options.

## Database update

Alembic revision `f8c12a4d9b60` adds indexes for account-cleanup foreign keys, outbox recipient lookup and user list ordering. It follows the existing `a61d9f3208bc` head and changes no records or table columns. A disposable SQLite migration test upgrades the previous head and verifies preserved user data, new indexes and harmless repeat execution.

From a backend host that can reach the configured database, stop API/workers and run:

    python scripts/upgrade_database.py

Then restart the API/workers and frontend. The command uses the existing configured PostgreSQL database, refuses missing/unknown/multiple revision histories, takes a bounded advisory lock, applies pending reviewed revisions in a transaction, checks that existing table row counts are unchanged, and rolls back on failure. It never resets, drops, stamps or downgrades the database.

**Hosted migration remains unapplied here.** A read-only connection attempt failed with WinError 10013 (network permission denied), and an authorized run of the guarded migration command failed to connect. No hosted database writes occurred. Hosted lock attribution, PostgreSQL query plans, real latency and concurrency therefore remain unverified.

## Passwords

Signup, password reset, admin create/edit and CLI admin creation accept passwords of 6?128 characters. Frontend minimum/confirmation validation, strength feedback, translations and OpenAPI schemas agree. Six numeric characters are accepted. Existing hashes and passwords need no migration.

## Validation evidence

Frontend production build and 14 transport tests passed. Real Chrome exercised the disposable FastAPI/SQLite server using the direct API client, verified a network resource for `/api/v1/admin/users` on the backend origin, and passed login, category edit/move, permanent deletion, procurement, mobile and RTL checks. Targeted tests verify six-character numeric signup/reset, HttpOnly cookie refresh/logout, origin enforcement, ordinary-user admin denial, credentialed CORS and sanitized 503 responses. The migration test upgrades the previous Alembic head, preserves an existing user and checks the new indexes. No live provider messages or configured database writes were made.

Final full backend suite: **75 passed, 2 skipped** (existing dependency/SQLite migration warnings). Frontend: **14 passed**, production build and direct-API browser checks passed. The protected IMPLEMENTATION_STATUS.md remains unwritable; actual results are recorded here.
