# Supplier bulk import verification — 2026-11-11

## 1. Root cause and evidence

The exact disconnect for `Supplier_Import_Part_01_of_64.xlsx` is **not yet reproduced**. That workbook, its request ID, deployed API URL, and production API/proxy logs were not supplied. Do not call the production incident resolved on the strength of local tests.

Confirmed defects and observations:

- The old dialog directly consumed a long-lived NDJSON response. It received a `started` event before showing the reported error. That particular request therefore reached FastAPI, passed authentication, parsed the workbook, and created an import batch. An initial preflight rejection or initial authentication rejection cannot explain that same response. A later transport/proxy/worker interruption remains possible; logs are needed to distinguish them.
- Its error text comes from XMLHttpRequest's transport-error event. It does not establish that the API was never reached. Streaming also coupled processing lifetime to the client connection and left no persisted UI recovery path.
- The old batch denominator used spreadsheet rows while the supplier count used distinct names. A synthetic workbook with 28 companies and two contact rows each reproduces the misleading two batches at size 35. Supplier groups now produce one batch at that size.
- Public, credential-free OPTIONS probes on November 11 found the non-www host redirects to www with HTTP 308 and no CORS headers. The existing upload route on www returns HTTP 200 from **Vercel**, with neither `Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials`, nor `X-Request-ID`. These public routes are not verified FastAPI ingress paths. This is an independently demonstrated deployment/CORS issue if either URL is used as the browser's cross-origin API base. It does not explain an already-started FastAPI stream without knowing the actual deployed API base.
- With no configured API URL, the old browser client guessed the frontend hostname on port 8000 even on production domains. Production now reports a missing API configuration explicitly.

## 2. Files changed

- `backend/app/modules/imports/supplier_jobs.py`: authenticated upload, persisted progress, retries, bounded worker, response schemas.
- `backend/app/modules/imports/bulk.py`: supplier-group batching on the retained stream route, guarded identity matching, company/contact update metrics, sheet-aware provenance, multiple material assignments, bounded XLSX parsing, safe stream errors and reports.
- `backend/app/modules/organizations/contact_import.py`: expose contact identity metrics without changing the existing return contract, so updated-contact counts represent distinct records.
- `backend/app/modules/imports/router.py`: register jobs and coordinate writes with other supplier imports.
- `backend/app/material_import_worker.py`, `backend/app/main.py`: run jobs through the existing durable worker and development poller.
- `backend/app/config.py`: bounded configurable supplier batch size.
- `backend/app/models.py`, `backend/alembic/versions/g39d7e5b2c84_supplier_bulk_job_indexes.py`: additive job identity/queue indexes.
- `frontend/features/admin/bulk-upload.tsx`: supplier upload/poll/recovery/retry UI; materials retain their existing streaming route.
- `frontend/lib/api/client.ts`, `frontend/lib/api/errors.ts`: production API configuration, HTTP/transport error separation and request IDs.
- `docs/openapi.json`, `frontend/lib/api/generated/{schema.ts,routes.json}`: refreshed API contracts.
- `backend/tests/test_supplier_bulk_jobs.py`, `frontend/tests/bulk-upload-client.test.cjs`, `frontend/tests/supplier-import-browser.mjs`: regression and browser recovery coverage.
- `backend/scripts/probe_supplier_import.py`, `docs/supplier-import-nginx.conf.example`, this report, and `backend/IMPLEMENTATION_STATUS.md`: verification and deployment notes.

## 3. Backend behavior

`POST /api/v1/imports/bulk/suppliers/jobs` validates multipart Excel and commits the job plus its source supplier groups before returning HTTP 202. It never imports real source files automatically. A group contains all identified rows for one normalized supplier identity, with worksheet and physical row provenance.

The existing `python -m app.material_import_worker` runner processes up to `SUPPLIER_IMPORT_BATCH_SIZE` groups per transaction (default 10; accepted range 1–35). Each group uses a savepoint. Supplier records, contacts, channels, category/material capabilities, assignments, source status, and progress commit together. A bad group rolls back its own changes and produces a downloadable report; unrelated valid groups commit. A transient database error rolls back the current chunk, preserving earlier commits, and pauses for manual retry after three unsuccessful attempts. Worker polling survives temporary database outages.

The queue is PostgreSQL-backed and does not depend on Redis delivery or an in-process BackgroundTask. Transaction locks disappear after worker death; pending source groups remain eligible for the next worker. The development API poller also invokes this processor. A separately supervised worker is required outside development.

`GET /api/v1/imports/bulk/suppliers/jobs/{batch_id}` reports committed counters. `POST .../{batch_id}/retry` requeues failed groups or resumes pending work. All three routes require an active administrator. Worker execution rechecks that the initiating administrator remains active and authorized. Existing error/warning download routes remain admin-only.

## 4. Frontend behavior

- Uses browser multipart boundaries and existing HttpOnly session credentials; no manually set multipart Content-Type or new token storage.
- Prevents duplicate submission while upload/processing is active.
- Saves the returned job ID in localStorage. Reopening the dialog or reloading the page can recover it. Polling failures retain displayed committed progress and continue polling; Refresh progress provides a manual refresh.
- Shows distinct supplier totals separately from source rows, committed counts for supplier/contact/assignment/capability/taxonomy changes, failed groups/source rows, completed batches, stage, and percentage.
- Offers failed-row download and retry for partial success; distinguishes upload progress from database processing.
- Preserves HTTP status errors including 401, 403, 413, 422, 429, 500, 502 and 504, and includes the response request ID where available. Database stack traces are not sent to the browser.
- Requires the real `NEXT_PUBLIC_API_URL` on both production frontend domains. Rebuild after setting it; do not point it at an unverified frontend route or guess a new backend host.

## 5. Nginx/CORS deployment requirements

No production configuration was edited or deployed. No Nginx configuration exists in this checkout. The publicly observed server is Vercel; the actual FastAPI ingress and any Nginx host still need to be identified.

FastAPI already permits `https://procumaterial.com` and `https://www.procumaterial.com`, with credentials and exposed request IDs. Existing CORS tests cover both. Do not replace this with wildcard credentials or add a second conflicting proxy CORS policy.

Review `supplier-import-nginx.conf.example` against the actual API virtual host: 9 MB multipart body allowance with an 8 MB backend workbook limit; preserve `/api/v1` in proxy_pass; forward Host and X-Forwarded-Proto; bounded connect/read/send timeouts; send OPTIONS to FastAPI; avoid canonical-host redirects on API preflights. Keep authentication and rate limiting enabled. If frontend and backend are cross-site, review the existing session cookie deployment settings as well.

After identifying the API host, run the read-only probe with `--base https://ACTUAL_API_HOST` for both frontend origins. Then verify an authenticated synthetic upload and committed PostgreSQL records through that same ingress in a staging environment. The probe alone cannot prove authenticated upload functionality or backend health.

## 6. Database changes

Uses existing `import_batches`, `import_rows`, organizations, contacts, channels, capabilities, categories and materials. No tables were reset, truncated or replaced, and no production data was modified.

The additive Alembic migration adds a partial unique `(initiated_by, source_sha256)` index for the new `supplier_bulk` job kind, a queue lookup index, and a pending-row lookup index. It does not impose new company-name uniqueness on existing suppliers. Existing relationship uniqueness constraints remain in force. Transaction-scoped advisory locks serialize supplier identity lookup/create across the importer paths and work with PostgreSQL transaction pooling. Existing engine, TLS, pool and timeout configuration is reused; no Neon settings were changed.

Full Alembic upgrade from an empty database to the new head was verified on an isolated local PostgreSQL 18 instance. Production/Neon migration application remains unperformed.

## 7. Tests

Supplier-job tests passed against separately created local PostgreSQL databases initialized through Alembic (not create_all). Each fixture generates and removes only its own test database. They never select the configured production database.

Coverage includes 1, 10, 28, 35 and 36 suppliers; repeated supplier/contact rows; changed-file business identity reuse; multiple material assignments; protected unrelated company data; contact-category relationships; malformed/oversized Excel; HTTP auth failures; partial-group rollback and retry; progress visible to a separate database session; concurrent uploads/workers; a real PostgreSQL statement timeout; and forced worker process exit before commit followed by restart. The 28/35 one-batch case covers the retained stream route and durable path.

Frontend unit tests exercise multipart credentials/boundaries, configured API base, status-specific errors with request IDs, transport interruption after received progress, and server-side stream errors.

A real headless Chrome test against the local Next.js frontend, FastAPI, and PostgreSQL also passed: a synthetic administrator authenticated through real HttpOnly cookies; a 28-supplier XLSX upload returned a durable job; connectivity was disabled at 3.57% committed progress; the displayed value stayed at 3.57%; reconnecting and reloading recovered the same job and reached 100%. A separate database session then verified job `fa3d5659-b692-4898-a3b4-f1603400c0be` completed with exactly **28 organizations and 28 contacts**, matching the persisted counters. Batch size 1 was intentionally used for this browser test to make the interruption observable; the normal default remains 10.

Redis was configured to an unreachable loopback port during this browser test. Development's existing local rate-limit fallback remained enabled, and the PostgreSQL worker completed independently. Production's existing fail-closed rate-limit policy was not changed; its behavior during an actual deployed Redis outage remains unverified. All browser test credentials and supplier rows were synthetic, and all test targets were loopback-only.

Final results: **76 backend tests passed, 1 skipped**, including all **16 isolated PostgreSQL job tests**; **54 frontend API/UI tests passed**; TypeScript checks and the optimized Next.js production build passed. The browser recovery test passed separately as described above. The initial frontend suite attempts lacked a running server or hit a cold-start timeout in the existing sitemap test; it passed after starting/warming the local server. The first browser attempt encountered sandbox Chrome control restrictions, then a test setup login used a username where the API requires phone/email; the corrected phone-based test passed outside the sandbox. No authentication code was changed to accommodate the test. The build required an outside-sandbox retry for existing `.next` file permissions. Test warnings concern Starlette/Alembic deprecations and pytest cache directory permissions. No production supplier workbook was imported during verification.

Reproduce PostgreSQL tests by supplying `SUPPLIER_BULK_TEST_DATABASE_URL` for a **local database with `test` in its name**, setting the main `DATABASE_URL` to an isolated SQLite test URL to keep unrelated opt-in hosted smoke tests disabled, and running `python -m pytest -q`. The fixture creates UUID-named test databases; the supplied local role needs CREATEDB. Run from `backend/`.

## 8. Retry guarantees and limits

- Same file bytes and administrator return the existing persisted job, including after a lost upload response. PostgreSQL uniqueness and a checksum lock cover concurrent submissions.
- A completed source group is not applied again when resuming that job. Failed groups can be explicitly retried. The source row/group status and business data share one transaction.
- A changed file is a new job, but existing supplier/contact/capability identities are reused. Concurrent workers are serialized for identity creation, and relationships retain their existing unique constraints.
- A worker crash before commit leaves the current chunk pending; after commit the saved source status prevents reapplication. Both paths are covered by database tests, including an actual terminated process.
- File recognition is scoped to the initiating administrator; a different administrator may create another job, whose business identities are still deduplicated.
- Retry does not repair invalid Excel values. Reupload a corrected workbook when source data needs correction. Ambiguous identity matches fail safely for review instead of overwriting an unrelated company.

## 9. Remaining issues

The exact production disconnect remains unclassified without its workbook/request logs and actual API URL. Public frontend preflight routing is demonstrably unsuitable as a verified cross-origin API endpoint. Production Nginx/upstream/worker configuration, production Redis failure behavior, Neon connectivity, production throughput, and authenticated end-to-end upload through the production proxy remain unverified. Browser reconnect/reload and Redis unavailability were verified locally as described above.

The existing production readiness gate in app/main.py remains in place. This change does not remove its outstanding security/concurrency requirements. No push or deployment was performed. The implementation is locally database-verified; it is not a verified production incident resolution.
