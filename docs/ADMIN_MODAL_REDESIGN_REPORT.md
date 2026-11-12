# Admin modal redesign

Frontend-only implementation. No backend processing, database migrations, API contracts, permissions, taxonomy rules, or public landing-page files were changed by this task. Other matching/RFQ changes already present in the shared workspace are separate work.

## Changes

- Shared native dialog frame: white/navy/blue styling, appropriate modal sizes, rounded borders, fixed header/footer, one scrolling body, form association for footer submit buttons, explicit Tab/Shift+Tab wrapping, focus retention when upload controls disappear, guarded Escape/backdrop dismissal, and short reduced-motion-aware transitions.
- Supplier bulk upload: Excel dropzone, filename/size, replacement and type/size validation, download buttons with loading/error states, compact backend-derived progress/counters, collapsible warnings and separate failed rows, review-aware completion, existing-batch retry, and persisted supplier-job recovery.
- Sequential polling stops on terminal status, close, or unmount. Closing a background job does not start another job. Duplicate upload/retry clicks are guarded before state updates.
- Unknown percentages remain indeterminate; absent counters show an em dash. Source row totals are not substituted for identified-company totals. Material-stream completion uses its existing completion event and preserves the last supplied progress percentage.
- Existing supplier, review, contact inquiry, category, material, import, user and membership dialogs use the shared frame. Existing native admin confirmations/prompts use the shared confirmation dialog. Supplier deletion now asks for confirmation before calling the unchanged archive endpoint.

## Changed files

- `frontend/app/app/layout.tsx`
- `frontend/app/admin-modals.css` (new)
- `frontend/features/admin/bulk-upload.tsx`
- `frontend/features/admin/bulk-upload-modal.tsx` (new)
- `frontend/features/admin/workspace.tsx`
- `frontend/features/admin/categories.tsx`
- `frontend/features/admin/category-import.tsx`
- `frontend/features/admin/imports.tsx`
- `frontend/features/admin/users.tsx`
- `frontend/features/admin/plan-management.tsx`
- `frontend/features/shared/admin-modal.tsx` (new)
- `frontend/features/shared/admin-confirm.tsx` (new)
- `frontend/features/shared/admin-upload.tsx` (new)
- `frontend/features/shared/ui.tsx` (admin confirmation integration; other roles retain their existing confirmation)
- `frontend/tests/ui-translation.test.cjs` (mock the new confirmation dependency)
- `frontend/tests/admin-modals-browser.mjs` (new)
- `frontend/tests/fixtures/admin-modal-api.mjs` (new, local test fixture only)
- This report.

## Validation

- Frontend regression suite: **61/61 passed**, with `SITEMAP_TEST_ORIGIN=http://127.0.0.1:13098` pointing to the local production frontend required by the existing sitemap test. This includes tests introduced concurrently by the separate matching work.
- Typecheck passed. An intermediate run caught unrelated concurrent RFQ/translation errors; a subsequent full run passed after those external changes were corrected.
- Production build passed, including its full TypeScript check.
- Final Chrome run against the production bundle: **22/22 modal/responsive checks passed**, with **0 console or hydration errors**. This includes forward/backward keyboard focus wrapping, focus retention through upload state changes, reduced motion, fixed-footer supplier submission, safe cancellation, existing import previews and reprocessing preview. Earlier test failures found focus loss when controls disappeared and native Tab leaving the dialog; both were fixed and verified in this final run.
- Browser checks use isolated loopback API fixtures, not production records. They cover file selection/type rejection, `.xls` drag/drop replacement, template/export loading, actual supplied percentages, missing percentage, processing/failure/retry/review/completion, duplicate-click prevention, job recovery, polling cleanup, modal labels/focus/geometry, and existing desktop form dialogs.
- Desktop English screenshots and Arabic screenshots at **320, 375, 430, 768, 1024 and 1920px** were captured. Upload controls were additionally checked at 320/375px. Long bodies scroll while header/footer stay fixed. Screenshot geometry and console/hydration assertions are automated.

Screenshots and detailed browser results are generated locally in `frontend/.test-runtime/admin-modals/` (ignored runtime artifacts). Run the fixture with `node tests/fixtures/admin-modal-api.mjs`, configure an isolated frontend with `NEXT_PUBLIC_API_URL=http://127.0.0.1:18084/api/v1` and `FASTAPI_INTERNAL_URL=http://127.0.0.1:18084`, and run `node tests/admin-modals-browser.mjs`. Override `ADMIN_MODAL_TEST_ORIGIN` for a different local frontend port. The browser runner uses the installed Windows Chrome.

These are frontend interaction tests. They do not establish production import persistence, actual workbook parsing, delivery of production exports, or every permission/data combination in every admin workflow. No production import or destructive production action was performed. No Lighthouse or field Core Web Vitals measurement is claimed.
