# Admin operations dashboard

`GET /api/v1/admin/dashboard/analytics?days=30` requires the existing authenticated
administrator dependency. Non-admin users receive 403; unauthenticated requests
use the existing authentication failure response. `days` accepts only 7, 30, or
90 (default 30); invalid values return 422. The existing `/admin/dashboard`
contract is unchanged. OpenAPI defines `DashboardAnalytics` and its nested records.

Response fields:

| Field | Meaning |
| --- | --- |
| `updated_at` | UTC timestamp after successful aggregate collection |
| `days`, `timezone` | Returned series length and UTC bucket timezone |
| `kpis` | Lifetime companies, verified suppliers, submitted quotations, invitation records, capabilities, import batches; current active RFQs and pending company reviews |
| `series` | One row per UTC calendar date, oldest first; absent days filled with zero; RFQs created/published and suppliers created/first approved |
| `statuses` | All persisted RFQ statuses and counts, including additional statuses such as review or expired |
| `matching` | Completed RFQs, zero eligible, invitation records, accepted/declined invitations, submitted quotations |
| `alerts` | Counts and existing admin routes for pending reviews, failed imports, unreviewed category mappings, failed publication/matching jobs, zero eligible |
| `activity` | Latest 15 records merged from at most 5 records of each supported event type; type, display reference, current status, timestamp, admin link |

Lifetime totals never depend on the chart range. Active RFQs are published,
collecting_quotes, or review. Submitted quote totals include a recorded
`submitted_at` or legacy `submitted` status, so later review decisions do not
remove a previously submitted quote. Pending reviews count distinct companies
with pending verification requests or ownership claims.

Supplier approval history uses each supplier's earliest recorded approved
verification `reviewed_at`. Verified companies without dated verification
records remain in the lifetime total but cannot be placed on the growth chart.
Repeated approvals do not inflate growth. No dates are inferred from registration.

Matching completion uses a processed `rfq.published` outbox event. Zero eligible
counts only RFQs with completed publication jobs and no persisted eligible or
partial match. This is persisted operational state, not a fresh matching run.
Invitation counts do not claim email or SMS delivery. Recent activity shows
the event timestamp with its record's current status, not an invented transition log.
Links use management lists when an existing entity detail route is unavailable.

Queries use SQL COUNT, grouped statuses, four grouped daily series, a grouped
first-approval subquery, distinct review company union, matching subqueries,
and five limited recent-record queries. No per-day queries, full-table entity
loads, N+1 reads, external notifications, or matching/import mutations occur.
Apply migration `s29a5d04e781` through the normal Alembic deployment workflow
for timestamp and outbox lookup indexes; PostgreSQL indexes build concurrently.
No live schema changes were applied during implementation.

Responses use `Cache-Control: no-store`; the existing user-scoped frontend
cache retains reads for 30 seconds. Refresh bypasses that cache. Existing
data-change events invalidate it. No dashboard polling is added.
Responses omit contact information, credentials, audit metadata, and import errors.

## Verification on 2026-11-12

Read-only PostgreSQL execution passed for 7, 30 and 90 days with identical KPI
totals: 582 companies, 579 verified suppliers, 3 active RFQs, 0 submitted quotes,
2 invitations, 396 supplier capabilities, 142 import batches, 0 pending reviews.
Independent counts confirmed 582 companies and 5 total RFQs; the status
distribution also summed to 5. These are observed values, never application constants.
The aggregate function issues a fixed 22 SQL statements regardless of date range
or record count; only daily aggregates and limited activity records reach Python.

- Frontend typecheck and isolated production build (`NEXT_BUILD_DIR=.next-admin-dashboard`): passed.
- Frontend API/component regression suite: 61 passed, with the sitemap test
  pointed at the running local production preview.
- Backend suite excluding `test_bulk_upload_postgres_smoke.py`: 99 passed, 4 skipped.
- Dedicated dashboard tests: 2 passed, including empty datasets, date validation,
  lifetime range invariance, deduplicated approval history, completed matching,
  anonymous 401 and non-admin 403.
- Initial full backend run: 99 passed, 4 skipped, 1 network-blocked live PostgreSQL
  import-writing smoke test. That test was not rerun against the live database.
- Browser snapshot QA: all 14 combinations of 320/375/430/768/1024/1440/1920px
  and English/Arabic passed, with no horizontal overflow or console/runtime errors.
  All date ranges, refresh requests, and error/retry passed. Desktop English and
  mobile Arabic screenshots were inspected. Screenshots/results and the actual
  PostgreSQL snapshot stay under ignored `frontend/.test-runtime/admin-dashboard/`.
- Browser test replaces authentication with a test identity and replays the
  read-only PostgreSQL analytics snapshot. Backend authorization is tested
  separately; browser QA does not claim end-to-end live authentication coverage.

Changed source files: `backend/app/modules/admin/analytics.py`, `backend/app/main.py`,
`backend/app/models.py`, migration `s29a5d04e781_dashboard_analytics_indexes.py`,
`backend/tests/test_admin_dashboard.py`, `backend/IMPLEMENTATION_STATUS.md`, this
document, `docs/openapi.json`, `frontend/features/admin/dashboard.tsx`,
`dashboard-charts.tsx`, `dashboard.module.css`, the supplier-dialog query hook in
`frontend/features/admin/workspace.tsx`, dashboard selection in
`frontend/features/portal.tsx`, generated `schema.ts`/`routes.json`, and
`frontend/tests/admin-dashboard-browser.mjs`.

Remaining deployment step: apply the additive index migration through the
existing Alembic release process and deploy frontend/backend together. No live
deployment or schema change was performed. Historical delivery/verification
details cannot be reconstructed when the database has no corresponding records.
