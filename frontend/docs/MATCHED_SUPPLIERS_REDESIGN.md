# Matched suppliers workspace redesign

Implemented directly in the existing buyer workspace. The shell, navigation,
Procumaterial palette and typography are preserved. No backend endpoint,
response schema, supplier matching rule or RFQ business logic was changed.
No screenshot attachment was available in this task message; the existing code
and the requested hierarchy were used as the visual reference.

## Interface

- One breadcrumb, provided by the existing workspace shell.
- “Find the right suppliers.” header with a small refresh action.
- Compact RFQ reference/project dropdown. RFQ search and server pagination sit
  in an expandable “Find another RFQ” control. A selected RFQ stays available
  even when its list page or search changes.
- Delivery city, main categories, quantities grouped by unit and RFQ status.
  Missing cities are explicitly unspecified, not nationwide coverage.
- Three counters only: matched suppliers, invitations recorded and quotations
  received. Missing data is not presented as a verified zero.
- Recommendations show only current `eligible`/`partial` matching outcomes,
  excluding conflicting “not covered” delivery data. The backend itself counts
  both these statuses as eligible. Supplier rows are horizontal on desktop and
  stacked on mobile. Each has one favorite action.
- Exact material, subcategory and main category evidence remain distinct.
  Unsupported percentage scores and inferred exact-product claims are removed.
- Other evaluated suppliers are collapsed and show safe explanations for
  location, unknown coverage, verification, approval, inactive organizations,
  missing material capability, rejected capabilities and project qualifications.
  Internal markers, UUIDs and unrecognized diagnostic strings are not echoed.
- Favorites is a compact tab with server pagination, profile links and removal.
  It does not imply that every saved supplier qualifies for the selected RFQ.
- Invitation progress explains what a recorded invitation means. Email/SMS
  delivery is never inferred from an invitation record. Reminder eligibility and
  the backend's 24-hour cooldown remain enforced. Requests are described as
  recorded, rather than claiming external delivery.

## Performance and state handling

Matching pages stay at 25 evaluated suppliers. Pagination now uses
`suppliers_evaluated`, fixing the previous use of eligible/invited counts that
could hide ineligible results on later pages. Invitation pages are independent;
an additional results request is made only when their offset differs from the
supplier page. Favorites fetch 26 rows as a bounded next-page sentinel and show
25. Previously visited favorite IDs and successful mutations are remembered;
saving uses the existing idempotent PUT endpoint.

The existing `useApi` scoped cache, request deduplication, mutation invalidation
and RFQ list debounce are reused. The duplicate standalone favorites request
and eager admin diagnostics request were removed. Embedded RFQ matching skips
the RFQ list request and has its own small refresh control.

Queued/processing results poll every five seconds while the tab is visible,
for at most 90 seconds. Completed, failed, closed and draft states stop polling.
A stalled state pauses automatic refresh and offers a clear refresh/support
message; it does not invent completion. Contradictory aggregate/result evidence
shows a warning. A manual refresh starts a fresh bounded polling window.

## Backend limitations and inconsistency reporting

The results response currently does not provide RFQ-wide accepted/declined or
quotation totals. Invitation rows are separately paginated. Consequently,
accepted/declined/pending counts explicitly describe the current invitation
page. When the optional `quotations_received` field is absent and invitation
results are incomplete, the quotation counter also says “On this invitation
page.” Submitted, shortlisted, awarded and rejected quotations are recognized
as received; drafts are excluded. No page count is silently promoted to a total.

The matching router calculates progress from grouped statuses across publication
jobs; it does not expose the latest job's authoritative state to buyers. An old
pending/retry job can therefore coexist with recorded results. Existing results
alone are not proof that a queued rerun has completed. This UI preserves that
distinction and uses the bounded status warning rather than overriding backend
progress. Direct contradictions, such as `no_eligible_suppliers` alongside
eligible results, are reported visibly and tested. No live RFQ or production
job was inspected or modified, so no specific live inconsistency is claimed.

## Files

- `features/rfqs/matched-suppliers.tsx`: simplified page and supplier rows.
- `features/rfqs/matched-suppliers.module.css`: scoped responsive styling.
- `features/rfqs/matching-model.ts`: evidence, grouping and state helpers.
- `features/rfqs/matching-copy.ts`: Arabic UI copy, preserving source company names.
- `features/rfqs/workspace.tsx`: remove the unused large favorites export.
- `tests/matched-suppliers.test.cjs`: behavior tests replacing obsolete layout-string assertions.
- `tests/matching-workspace-browser.mjs`: actual production-app browser checks
  with isolated API fixtures, viewport measurements and screenshots.

Existing uncommitted backend caching and dashboard work was preserved.

## Verification

- `npm.cmd run typecheck`: passed.
- `npm.cmd run build`: production webpack build passed.
- Full frontend suite, `node --test tests/*.test.cjs`: **71 passed**.
  `SITEMAP_TEST_ORIGIN` pointed at the local production app for its existing
  sitemap smoke test. No real supplier records were used.
- Real headless Chrome against the production build: **14 viewport/language
  combinations** at 320, 375, 430, 768, 1024, 1440 and 1920 pixels, in English
  and Arabic. Zero horizontal overflow, one breadcrumb, three counters, one
  favorite button per supplier and collapsed other results.
- Browser interactions: RFQ switching and URL, Favorites save/remove/pagination,
  evaluated pagination, quotation link, reminders/cooldown, contradictory status,
  stalled polling and completion. No browser console/runtime errors.
- Desktop and Arabic mobile screenshots were visually reviewed. Local artifacts:
  `.test-runtime/matching-workspace/{1440,375}-{en,ar}.png` and `results.json`.

Browser tests intercept API calls with isolated fixtures. They verify client
behavior and rendering, not production authentication, delivery, matching
quality or live RFQ results. No deployment or external messages were made.
