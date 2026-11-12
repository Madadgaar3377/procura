# Distribution Panels matching incident — 2026-11-11

Implementation and rollback verification are complete in this checkout. **The running production code, schema and business records have not been changed.** Deployment host/service information was requested and is still needed. The data repair must follow deployment of both the API and notification worker so the previous matcher cannot recreate incorrect eligibility. This is not production handover sign-off.

## 1. Confirmed root causes

1. `matching/service.py` explicitly allowed main-category fallback. An approved Electrical row could qualify without any relevant product or subcategory. This is why BORRI and Tecon - Eaton qualified.
2. Blank capability city was included in `NATIONWIDE_KEYWORDS`. Office city was also added to service coverage. Blank coverage therefore passed Jeddah eligibility without evidence.
3. `matching_batch_size=5` limited initial publication to the first five ordered organization IDs. A continuation after `0c38b830-ee55-4b23-a3a2-82c892dcd645` was pending with zero attempts. ABB's active ID sorts later. No stale-cache explanation is needed to account for the five results; matching queries the database directly.
4. Active ABB's approved product `ce6d0a92-1344-4ab9-ace8-1af52167208d`, named `Distribution Panels`, was saved under Electrical with **no subcategory**. The RFQ material is a different ID under DISTRIBUTION PANELS. A strict matcher must not infer equivalence from those descriptions. The supplied repair explicitly reclassifies the existing product to its exact canonical category, retaining the material ID, increasing its version, and writing material revision and audit records.
5. Previous eligible/partial matches were deliberately not overwritten by rejection diagnostics. A rerun could leave incorrect eligibility displayed.
6. Notification eligibility had a separate category query, inconsistent with matching material capabilities. The delivery worker also attempted automatic quotation preparation even after an invitation event was skipped.

## 2. Database records inspected

Read-only PostgreSQL inspection covered the schema of categories, materials, capabilities, RFQ items, matches, invitations and source trades; relevant category/material relationships; organization status and duplicate imports; approved capabilities and coverage; invitation/quotation joins; outbox states and errors; capability indexes. The public-products handler was compared with saved data using the same real PostgreSQL records in an outer rollback transaction. The deployed public HTTP endpoint was not independently exercised.

| Record | ID / findings |
| --- | --- |
| RFQ | `f1eeb71e-0858-41d7-937d-1daffafecc81`, RFQ-A647A644745D, collecting_quotes, Jeddah, deadline 2026-11-27 15:30 UTC |
| Buyer organization | `2b0cf762-81d8-400e-94d8-79462ad04476` |
| RFQ item | `55558b68-13b4-4c3f-9c5c-b67291889fb4`, 100 PCS, original ABB brand requirement preserved |
| RFQ material | `6f6bfd87-89f2-4f05-b55f-85b7c6c3f55b`, active, correctly classified, empty structured brand metadata |
| Electrical category | `fb244409-97b5-4a27-a980-60132dbdb3e5` |
| DISTRIBUTION PANELS category | `c3317afa-cfc4-407c-9c7a-1c467dd9cdec`, active child of Electrical |
| Active ABB | `43c77874-54c8-4833-a3a5-9f8054680a27`, active, verified, Jeddah office; approved Electrical capability `7ca7df6b-2ac8-4ae6-9875-9fe6a7bd2861` explicitly serves Jeddah and links Distribution Panels material `ce6d0a92-1344-4ab9-ace8-1af52167208d` |
| Inactive ABB duplicate | `86f9cb1c-6466-4d9b-88ca-e505e3dc127a`; has an exact RFQ material capability, but is inactive; its capabilities were not copied or reactivated |
| BORRI | `02abd3f0-efed-423e-8ba0-8bb22b7df1f9`; approved Electrical capability `0e167f16-36bc-42f8-9738-8fde2b37bbbe`; only linked UPS material `51945952-06e3-4cc1-8505-9c0d3b04df71`; blank coverage |
| Tecon - Eaton | `030dcf3c-0c7b-4c1d-ada1-50c93224b331`; approved Electrical capability `4640ef96-ee7a-4ed6-9a5c-d685f8ce8b0f`; no material links, blank keywords and coverage; retained UPS/Batteries source responsibility is `material_pending_review` |
| Historical invitations | BORRI `d6cd6abc-b192-42e3-98b1-66f63c0d7e6d`; Tecon `7da9c6df-31be-42e3-ae47-d89ff935484c`; both invited, no quotations at inspection; invitation events pending with zero attempts |
| Pending continuation | `75f1ecd2-4ee5-4d02-8c6a-83af2ceaa885`; initial publication event `fd57d932-f1af-4825-9e28-c680c3439388` processed |

Tecon's zero public products reflects its actual approved data; no products were invented. BORRI's UPS-only public product reflects its saved link. ABB's public product name was present, but its canonical subcategory was missing. No private employee contacts are included in this report or added to public responses.

**ABB has zero organization membership records.** Real representative access still requires the existing authorized account/company-claim process. Temporary QA memberships used for verification were rolled back; they do not establish that a real ABB representative can currently sign in.

## 3. Backend files changed

- `backend/app/modules/matching/service.py`: exact active material and canonical subcategory support, including explicitly linked approved materials; no main-category fallback; SQL capability filtering before keyset pagination; all pages processed; explicit capability coverage; row locking and unique invitation constraint retained; stale eligibility corrected with audit history; saved structured brand evidence distinguished from unverified brand requirements.
- `backend/app/modules/matching/router.py`: canonical candidate diagnostics with SQL counts and bounded reason lists; human-readable results; relevant matched categories; match level, brand, coverage and historical-invitation-review fields; internal marker strings removed from buyer and administrative display.
- `backend/app/modules/notifications/procurement.py`: reuse the same eligibility evaluation for delivery/reminders without creating invitations; ineligible pending invitation events explicitly skipped for review.
- `backend/app/notification_worker.py`: no automatic quote preparation for skipped invitation events.
- `backend/app/models.py`: PostgreSQL-only material-link index metadata.
- `backend/app/response_contracts.py`: additive matching presentation fields.
- Three scripts: `inspect_distribution_matching.py`, `correct_rfq_matching.py`, `verify_rfq_workflow.py`.
- `backend/tests/test_distribution_matching_regression.py`: five incident regression tests.
- `docs/openapi.json`: updated additive response contract.

There is no supplier-name branching. No fuzzy category/material-description matching or unapproved equivalence was introduced. The inspected schema has no approved equivalence registry; arbitrary equivalent claims remain ineligible. Brand evidence only comes from relevant, approved, linked product metadata; neither company name nor a requested brand proves authorization or stock. Actual ABB evidence remains unverified.

## 4. Frontend files changed

- `frontend/features/rfqs/workspace.tsx`: existing matching table shows match level, brand requirement, service coverage and historical invitation review; defensively hides internal marker strings.
- `frontend/lib/api/generated/schema.ts`: regenerated response types.

No landing-page change or platform redesign. Other concurrent workspace edits to admin interfaces/layout/shared UI are outside this fix and were not reverted.

## 5. Migration and explicit data repair

Alembic revision `r18f4a932c70`, parent `j40a8c12d506`, adds a concurrent PostgreSQL GIN expression index on `(material_ids::jsonb)`. It changes no business data. PostgreSQL offline upgrade SQL was successfully generated; **the index has not been applied to live PostgreSQL**. Confirm the deployed Alembic revision before applying; do not run unrelated pending migrations blindly.

The data correction is separate, transactional and preview-only by default:

```powershell
cd backend
python scripts/correct_rfq_matching.py --rfq f1eeb71e-0858-41d7-937d-1daffafecc81 --repair-material ce6d0a92-1344-4ab9-ace8-1af52167208d --subcategory c3317afa-cfc4-407c-9c7a-1c467dd9cdec
```

After deploying the API/worker and reviewing the preview, repeat with `--apply`. It validates exact taxonomy-label equality and existing main-category ancestry, records the material revision and before/after audit data, reruns only this RFQ, verifies invitation idempotency and unchanged unrelated invitation counts, and commits only in apply mode. It does not run delivery workers. It never merges/deletes source products or changes inactive supplier status. The material version increase preserves existing private rate/version safeguards.

Administrative correction preserves historical invitations and quotations. Incorrect match eligibility changes to `ineligible` with `rfq.match_eligibility_corrected` audit events; buyer results explicitly show `invitation_review_required=true`. Original invitation statuses remain visible. Pending ineligible notification preparation is skipped under the shared policy. Already delivered invitations are not silently revoked. Any supplier communication or further administrative action requires a separately authorized action through the established process.

## 6. Actual verification

Final isolated backend suite: **97 passed, 5 skipped**, using `DATABASE_URL=sqlite:///:memory:`, `APP_ENV=test`, `python -m pytest -q --tb=short -p no:cacheprovider`. Skipped PostgreSQL/source-specific checks are not counted as passing. An earlier unisolated run hit the sandbox network restriction in an existing hosted bulk-upload test; it was not rerun against production because that unrelated test creates/deletes supplier import data.

Five new regression tests verify A–J using the supplied RFQ ID in an isolated fixture: exact material, exact subcategory and linked material subcategory eligibility; UPS/generic-category rejection; explicit other-city rejection; unknown coverage failure despite Jeddah office city; no coverage borrowing from unrelated products; approvals; multi-page matching with page size two; duplicate prevention; audited historical rejection; skipped ineligible delivery preparation; brand metadata evidence versus unverified requirements; explicit audited taxonomy correction; supplier visibility/acceptance/draft/edit/submission; buyer draft isolation, submitted quotation view and comparison; outsider denial.

Real PostgreSQL rollback tests:

- Finalized-code matching preview: seven evaluated records (including five historical diagnostic records and both ABB organizations), one eligible, six rejected, one hypothetical new invitation; second run zero new invitations; zero unrelated invitations generated. All writes rolled back.
- Actual RFQ/API handler workflow: temporary QA buyer/supplier memberships; real saved RFQ/capabilities; corrected public product grouping (nine saved public products); invitation visible and accepted; synthetic quote drafted, edited and submitted; buyer retrieved and compared submitted quotation; matching presentation verified. **The entire outer transaction was rolled back**, including every route-level commit. No test user, membership, quote, invitation, notification or taxonomy change was retained. Providers, real account sign-in and deployed browser behavior were not tested.
- Frontend TypeScript check passed. Frontend API/UI tests: **57 passed, one sitemap test failed** because no server listened on `127.0.0.1:3000`; this is outside matching. No claim that the entire frontend suite passed.
- OpenAPI export/generation and scoped `git diff --check` passed. Postman CLI was unavailable; no unrelated workspace setup was introduced.

## 7. Corrected real-RFQ results — rollback preview, not committed production state

| Supplier | Correct outcome | Invitation handling |
| --- | --- | --- |
| ABB, active organization | Eligible by approved linked Distribution Panels **subcategory** after explicit taxonomy repair; Jeddah coverage; requested ABB brand unverified | One new hypothetical invitation; rerun zero duplicates |
| ABB, inactive duplicate | Excluded: inactive organization | None |
| BORRI | Excluded: only UPS product, no approved relevant material/subcategory; service coverage also unknown | Existing invited status retained; flagged for administrative review |
| Tecon - Eaton | Excluded: generic Electrical only, no approved relevant material/subcategory; service coverage also unknown | Existing invited status retained; flagged for administrative review |
| Al-Khaleej Al-Awwal | Excluded: no approved relevant material/subcategory; saved Riyadh coverage also does not satisfy Jeddah | None |
| Star Care for Trading Est | Excluded: no approved relevant material/subcategory; saved Riyadh coverage also does not satisfy Jeddah | None |
| Energy International Controls (EIC) | Excluded: no approved relevant material/subcategory; saved Riyadh coverage also does not satisfy Jeddah | None |

The primary rejection reason is the first failed eligibility check. Location regressions separately prove that a relevant approved supplier restricted to Riyadh is excluded from Jeddah delivery.

## 8. Unrelated invitations and remaining rollout work

No production invitations or messages were created, revoked or deleted by this investigation. The real-data correction preview verified zero unrelated invitation additions and zero duplicates on a second run. Every synthetic workflow change was rolled back.

To finish: identify/deploy/restart the actual API and notification worker; verify the applicable index migration; apply the reviewed scoped correction; provision an authorized real ABB representative through the existing claim/membership workflow; then confirm the deployed buyer/supplier experience and actual configured delivery. These steps remain outstanding. HTTP 200 alone is not acceptance evidence.
