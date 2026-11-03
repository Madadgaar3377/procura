# Permanent deletion and complete category editor ? 2026-10-22

## User deletion

`DELETE /api/v1/admin/users/{id}` now permanently removes the user row, refresh sessions, organization memberships, account notifications, queued deliveries, verification/recovery codes and claimant records. Nullable reviewer, material-revision, inquiry-handler and audit actor references are cleared. Existing import batches are reassigned to the deleting administrator, retaining shared imported data. Organizations, RFQs and quotations are independent records and remain available to their remaining members. Deleted account identifiers may be registered again. Existing access and refresh credentials fail. Self-deletion remains forbidden, and every user mutation remains admin-only.

The Users page describes permanent deletion, exposes Delete for active and inactive accounts and confirms the irreversible effect. Inactive accounts can be enabled through Edit; deleted accounts cannot be restored.

## Category deletion

`DELETE /api/v1/taxonomy/categories/{id}` now deletes the selected category and all descendant subcategories, their materials, material revisions, price book entries and category capability links. Removed material IDs are also removed from remaining supplier capability lists. Nullable category/material references in RFQ items are cleared while saved description, specifications, unit, quantity and quotation links remain intact. Unrelated categories, materials and companies remain unchanged. The previous blanket linked-record/subcategory error no longer blocks these supported deletions.

Deletion inspects deployed foreign keys so the existing legacy RFQ schema remains supported. Unknown required dependencies fail with HTTP 409 and roll back the transaction. All deletion changes are atomic, admin-only, audited and invalidate the catalogue cache. No migration or configured database mutation was performed while implementing this change.

## Complete category editing

`PUT /api/v1/taxonomy/categories/{id}` retains its CategoryOut response and accepts the expanded CategoryEdit request: the previous category name/parent/sort fields plus optional material_id, material_subcategory_id, material_description and unit. Existing name-only callers remain supported. Description and unit must be supplied together. A supplied material must belong to the edited category; omitting material_id creates a new material when description/unit are supplied. Main-category material edits require a subcategory belonging to that main category.

The popup shows the main category, subcategory/name, existing-material selector, description and standard unit. The only material is prefilled automatically; otherwise the admin selects the material explicitly. Search and pagination support categories with more than 100 materials. Subcategories can move to another active main category, updating material category IDs, discipline and duplicate identities. Category renaming and material edits save in one transaction; invalid units, unrelated material IDs and duplicates roll back the entire request. Material IDs and procurement snapshots remain stable during edits.

## Verification

- Full disposable SQLite backend suite: **71 passed, 2 skipped**, with three existing dependency/migration warnings.
- Frontend API proxy tests: **10 passed**.
- Production build in `.next-account-category-fixes`: passed compilation, TypeScript, static generation and tracing.
- OpenAPI and generated frontend request/response types updated.
- Tests explicitly cover actual row removal, auth dependency cleanup, permission denials, deletion of linked category data, preservation of RFQ snapshots, complete edits, subcategory moves and rollback on invalid edits.

The protected `backend/IMPLEMENTATION_STATUS.md` remains unwritable; actual results are recorded here. PostgreSQL concurrency and live delivery are not established by local checks.

Browser/HTTP validation also passed (`NEXT_BUILD_DIR=.next-account-category-fixes npm.cmd run test:browser`). Real Chrome edited a prefilled category popup, changed the main category, description and unit, verified persisted material data, deleted the linked category subtree, and permanently deleted a test user with API GET returning 404. Existing procurement, tenant, cookie/origin, mobile and RTL checks passed. All writes targeted the test harness disposable SQLite database; no external messages were sent. Screenshots: `frontend/.test-runtime/screenshots/admin-complete-category-edit-popup.png`, `admin-complete-category-edit.png`, and `admin-user-permanently-deleted.png`.
