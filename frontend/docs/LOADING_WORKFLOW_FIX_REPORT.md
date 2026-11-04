# Procura loading and workflow verification — 2026-10-23

Implemented locally in the existing customized frontend. No deployment, commit, push, database reset, or real notification delivery was performed. This pass fixes shared loading architecture and verifies existing procurement workflows; it does not complete every feature in the requested scope.

## Root causes and fixes

- The dashboard shell and SessionGate lived inside the catch-all page. Route replacement and its full-screen loading boundary could replace the entire workspace. Protected routes now have a dedicated persistent `app/app/layout.tsx`; the existing PortalShell renders above page loading and error boundaries. Browser tests verify header DOM identity across client navigation.
- The original uncached useApi hook discarded data at every request start and did not cancel requests. It now separates initial loading from background fetching, caches by verified user/role/organization, deduplicates active reads, cancels abandoned requests, guards obsolete responses, and invalidates reads after successful mutations. Lookup freshness is five minutes; other reads use thirty seconds. Inactive entries are bounded to 150. Safe directory filters retain previous data.
- Authentication used an unbranded panel during verification and lacked a verification retry. It now shows branded loading and useful retry states, preserving HttpOnly cookies and shared refresh-token rotation. Failed logout does not falsely navigate to login.
- Organization selection depended on unordered API results and reset on refresh. The selected organization ID is remembered per verified user/workspace in sessionStorage and resolved only against active API-loaded memberships. Authentication tokens and API records are not stored there.
- RFQ and quotation initialization effects could reset editable fields after background requests. Initialization is now guarded by record identity. RFQ save has a synchronous in-flight guard. Mutations are never automatically retried.
- Requests with a caller signal previously bypassed the timeout. Caller cancellation and a 20-second timeout are now composed. Safe GETs retry 502/503/504 once after bounded backoff. Server, connection and timeout errors have safe, useful messages.
- No independent CSS-order or hydration defect was established. Screenshots show styled, aligned loading surfaces; browser workflows assert no uncaught JavaScript exceptions. These checks are not a formal zero-CLS or all-routes guarantee.

## Loading UI and page coverage

Implemented AppLoadingScreen, DashboardSkeleton, TableSkeleton, CardSkeleton, FormSkeleton, ChartSkeleton, PageLoadingState, EmptyState, ErrorState and RetryButton in `features/shared/loading.tsx`. Skeletons honor reduced motion. Dashboard placeholders follow the existing responsive metric/column layout. Shared State accepts the hook's reload callback automatically for retries.

All buyer/supplier/admin routes share the persistent shell and route error boundary. Public pages have a root error fallback; directory requests use the improved shared states. Specialized states for every requested page have not been individually audited or browser-tested.

Comparison displays lowest/highest prices only for complete, compliant offers matching requested quantities, grouped by currency, VAT and price basis. Partial/unconfirmed offers remain visible below. No dashboard statistics were invented.

## Files changed

- frontend/app/app/layout.tsx, loading.tsx, error.tsx, [...workspace]/page.tsx
- frontend/app/error.tsx, globals.css
- frontend/components/route-view.tsx
- frontend/features/workspace-layout.tsx
- frontend/features/shared/loading.tsx, ui.tsx
- frontend/features/auth/session.tsx
- frontend/features/dashboard/workspace.tsx
- frontend/features/rfqs/workspace.tsx, material-row.tsx
- frontend/features/quotations/workspace.tsx
- frontend/features/suppliers/directory.tsx
- frontend/lib/api/use-api.ts, client.ts, errors.ts
- frontend/tests/api-client.test.cjs, browser-check.mjs
- frontend/tsconfig.json (Next-generated isolated-build includes)
- frontend/docs/LOADING_WORKFLOW_FIX_REPORT.md

The requested backend/IMPLEMENTATION_STATUS.md update could not be written: the shell operation required unavailable sandbox approval, and a direct patch failed to write the file. Backend test results are recorded here instead.

Backend endpoint contracts and database schemas are unchanged. Existing FastAPI publication and worker matching contracts were reused.

## Validation results

- `python -m pytest -q`: **83 passed, 2 skipped**, 7 dependency/Alembic warnings; disposable in-memory SQLite and mock notifications. Covers exact-category matching, worker publication, RFQ-only description edits, capabilities, quotations, comparison, reports and authorization.
- `npm.cmd run test:api`: **17 passed**. Cookie transport, shared refresh, deduplicated reads, cancellation, bounded read retry, no mutation retry, connection failures, tenant denials and proxy security.
- TypeScript and repository lint command: passed. The `lint` script runs TypeScript; ESLint is not installed/configured, so no independent ESLint result is claimed.
- Production build: passed with `NEXT_BUILD_DIR=.next-loading-test`. Default `.next` build encountered EPERM on existing output files. The isolated browser build embeds `NEXT_PUBLIC_API_URL=http://127.0.0.1:18081/api/v1`; use normal development configuration for the actual local backend.
- HTTP end-to-end: passed against real local Next.js and isolated FastAPI/disposable SQLite. Buyer/supplier/admin login, tenant denial, verified capability, publication, matching, acceptance, saved-rate draft, submission/comparison, reports, cookie redaction and CSRF. Test matching dispatch is explicit; automatic worker dispatch is covered independently in pytest.
- Expanded headless Chrome browser suite: **passed**, using the existing CDP harness. Tests 1.8–5-second latency, initial skeletons inside the shell, retained background data, real HTTP 403/500 responses, offline failure/retry, authentication failure/retry, branded verification, 401/session expiry, persistent header DOM, organization isolation and refresh selection, RFQ edits surviving lookup refetch, material/unit autofill, master-data preservation, supplier capability save, enquiry acceptance, manual quotation review/submission with backend totals, comparison, selected admin workflows, mobile 390px and RTL. No uncaught JavaScript exceptions were reported.
- Local screenshots: `frontend/.test-runtime/screenshots/`, including buyer-dashboard-initial-skeleton.png, buyer-background-refresh-slow.png, buyer-refresh-403.png, buyer-refresh-500.png, buyer-refresh-offline.png, buyer-organization-isolation.png, auth-verification-error-retry.png, auth-branded-verification.png, auth-expired-session.png, supplier-capability-saved.png, supplier-manual-quotation-review.png and supplier-manual-quotation-submitted.png.

## Remaining work

- Persisted supplier auto-submission authorization and automated submission with technical/commercial checks do not exist in the backend. Saved-rate preparation currently creates drafts requiring explicit supplier submission; it never authorizes automatic submission.
- Shortlist/award/rejection transitions, favorite suppliers, buyer bulk reminders, all requested supplier outcome/enquiry metrics, and buyer-wide matched/comparable-price dashboard aggregates remain unimplemented. Existing statuses/counts remain accurate.
- Capabilities support multiple categories/subcategories through repeated additions; bulk multiselect/save is not implemented.
- Every public/admin/report/import/settings route has not been individually exercised under every failure condition. Shared shell/state fixes apply, but this is not a complete all-pages audit.
- PostgreSQL/Redis concurrency, live provider delivery, production performance/bundle analysis and independent ESLint validation remain unverified. No actual provider was contacted in tests.
- RFQ duplicate-click protection is implemented; durable server-side idempotency after an uncertain POST response remains a backend gap.
