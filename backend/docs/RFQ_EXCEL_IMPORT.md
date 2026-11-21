# Buyer RFQ Excel import

Implemented on `/app/buyer/rfqs/new`, Step 2, beside Add Material Row. The existing manual/grouped entry, request details, RFQ review, draft creation, publication, supplier matching and invitation services remain in use.

## Audit and architecture

- Audited `Category`, `Material`, `RFQ`, `RFQItem`, `ImportBatch`, `ImportedRow`, their indexes and existing migrations; taxonomy search; master and supplier import parsers; local/R2 import storage; the import worker; RFQ creation/publication; and supplier matching/outbox dispatch. Schema inspection was from the checkout, without modifying or querying a live database.
- No Gemini integration or `GEMINI_API_KEY` setting existed in this checkout. Added one server-only HTTP adapter using the documented [Gemini generateContent API](https://ai.google.dev/api/generate-content). No new AI SDK or other dependency is required. Provider discovery returned an older experimental model, so the model is configurable rather than derived from that result.
- Reuses `ImportBatch` (`kind=rfq_materials`) and `ImportedRow`, existing XLSX/XLS/CSV parsing primitives, private `get_import_storage()` (R2 when enabled), existing indexes, and `app.material_import_worker`. No database migration or new tables.
- Source organization, uploading user, browser draft scope, optional existing RFQ ID, AI preference, mapping and worker lease are stored in existing JSON options. Every operation checks the uploading user and current buyer organization membership; existing RFQ jobs additionally require that the RFQ remains a draft in that organization.
- No master materials/categories, supplier invitations, or notifications are created during upload, matching or review. RFQ quantities belong to RFQ lines. Import confirmation returns reviewed lines to the wizard; existing RFQ POST/PUT saves them with all manual lines. Only the existing explicit publish endpoint initiates supplier matching.
- The old 100-line RFQ request limit was raised to 500, allowing a 300-row import alongside manual entries. Explicitly empty optional delivery cities now honor the existing RFQ form contract.

## Interface and parsing

The responsive English/Arabic RTL modal contains a real Excel template download, drag/drop/browse upload, default-enabled AI option, background progress and a review table. The template has Materials and Instructions worksheets, numeric quantities and example rows clearly marked for replacement. Instructions list actual active category terminology. Category/subcategory are optional; no material IDs are required.

Supports XLSX, legacy XLS through existing `xlrd`, and UTF-8 CSV. Limits: 5 MB, 300 actual material rows; the existing workbook expansion/column safety limits also apply. Frontend checks extension, signature, CSV text, size, physical CSV row limits and confirmation row count. Backend independently checks file contents, size, parsing safety and actual material count. CSV delimiter detection supports comma, semicolon and tab. Extra nonempty CSV fields are rejected rather than silently truncating quantities.

Aliases cover Description, Description / Specification, Material, Item, Unit/UOM, Qty/Quantity, Category/Main Category/Subcategory and Arabic equivalents. Merged section headings, empty rows and repeated headers are excluded. Categories can carry forward within a group. Unknown column layouts enter `needs_mapping`, with buyer-selectable column mapping. Original descriptions, decimal quantities and buyer edits are retained. `nr` is normalized to the existing number unit `NO.`; arbitrary conversions are not performed.

The preview supports accepting high-confidence rows in bulk, individual acceptance, category changes, catalog search with debouncing and server pagination, description/specification/unit/quantity edits, custom RFQ items, selection/removal and retries. Uncertain suggestions are never automatically accepted. Bulk actions preserve unsaved edits in other rows. Accepted row edits are stored on the server with optimistic row versions. Confirmation uses deterministic line keys, and the wizard filters existing keys to prevent double insertion. RFQ details and manual materials survive import and browser draft recovery.

## Matching and worker behavior

- Database exact and bounded shortlist retrieval use active catalog records and existing category/description/unit indexes, including existing PostgreSQL trigram indexes. One UNION retrieval query covers at most 30 rows, followed by one material fetch. Technical size filtering happens before the shortlist limit. Each row exposes at most five candidates. English/Arabic material type anchors and Arabic numeric normalization support bilingual input.
- Existing RFQ POST/PUT preloads references for the entire request. A regression checks that creating 300 RFQ lines fetches Material Master once. Imported rows do not trigger one browser material-detail request per row; normal catalog search/selection remains available.
- Local extraction covers material type, diameter, dimensions, grade, standards, material composition, pressure ratings, electrical ratings and explicitly labeled brand/model. Known conflicts, or missing evidence for specified attributes, exclude a candidate. Units and category scope must agree. Custom rows remain available when safe catalog evidence is absent.
- Exact description/unit matches with compatible evidence can be high confidence. Ambiguous candidates are optionally reranked by Gemini, in one bounded request for a 30-row chunk. Gemini receives only provided candidates; returned IDs are checked against the same row's allowlist. Model suggestions are capped below high confidence and need buyer acceptance. Displayed confidence is a matching indicator, not a calibrated probability.
- No candidate or an unusable provider response never creates a master record. Missing keys, timeouts, quota errors, malformed responses and unavailable models fall back to database suggestions, with an honest fallback notice. Unknown/incompatible results remain custom items requiring category/unit/quantity review.
- Workers claim one job chunk with a short row lock and a recoverable 90-second lease. Parsing/storage I/O and Gemini calls run after releasing the SQL transaction and connection. Committed rows survive interruption; retries process pending/selected rows, and failed/expired worker leases can resume. Writes use a bounded transaction of at most 30 rows. Progress-only polling omits row payloads until review is ready.
- Matching is application-side code in the existing import worker process, not a second Redis/Celery import queue. Existing Redis-backed rate limiting/cache settings continue to apply. Uploads are limited to six per minute per user; retries and edits have separate existing rate-limit scopes.

## API contracts

All paths are under `/api/v1/rfqs/imports`, with current-user authentication and buyer ownership checks. Contracts are in `docs/openapi.json` and generated frontend schema/route files.

| Method | Path | Purpose |
|---|---|---|
| GET | `/template?org_id=…` | Real XLSX template and current category terminology |
| POST | `/` | Multipart `file`, `org_id`, `draft_key`, optional `rfq_id`, `ai_enabled`; stage a durable job (202) |
| GET | `/recent?org_id=…&draft_key=…` | Recover this user's imports for this browser draft scope |
| GET | `/{batch_id}` | Progress and parsed rows/candidates; `include_rows=false` gives lightweight progress |
| PUT | `/{batch_id}/options` | Set column mapping before matching starts (202) |
| PUT | `/{batch_id}/rows/{row_id}` | Validate/edit/accept a row with `expected_version` |
| POST | `/{batch_id}/review` | Bulk `accept_high_confidence` or `remove`, optionally scoped by `row_ids` |
| POST | `/{batch_id}/retry` | Retry failed parse jobs or pending/selected row matching (202) |
| POST | `/{batch_id}/confirm` | Revalidate accepted rows and return deterministic RFQ lines; replay is idempotent |

Upload idempotency is scoped to uploader, organization, browser draft, file hash and AI preference. Confirmed previews are frozen. Confirmation stages lines in the browser form; it does not save or publish an RFQ behind the buyer's back. Existing `/rfqs`, `/rfqs/{id}`, `/rfqs/{id}/publish`, supplier matching and notification routes remain the persistence/publication path.

## Configuration and deployment

Set these only in the backend environment or deployment secret store:

```dotenv
GEMINI_API_KEY=<server-side secret, or empty for database-only matching>
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TIMEOUT_SECONDS=10
```

Choose a model available to the deployed Gemini account. Do not add the key to a `NEXT_PUBLIC_` variable. Timeout is bounded to 0.1–30 seconds. Model names are validated and the provider HTTPS host is fixed. Keys, provider bodies and buyer descriptions are not logged by the adapter.

Keep existing `DATABASE_URL`, `REDIS_URL` and private R2 settings. `R2_ENABLED=true` uses the existing private bucket adapter; when false, API and worker must share the same private upload directory. The API and worker must use the same database and storage configuration. Deploy the updated API and import worker together.

Commands below are deployment instructions, not actions performed on the live site:

```powershell
# Backend, after activating the existing virtual environment
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Run under the existing process manager as a separate process
python -m app.material_import_worker --interval 1
# Keep the existing notification/outbox worker running according to current deployment policy

# Frontend; set the real NEXT_PUBLIC_API_URL before building
cd ../frontend
npm.cmd ci
npm.cmd run api:generate
npm.cmd run typecheck
npm.cmd run build
npm.cmd run start
```

There is no new Alembic revision to apply. Existing baseline migrations must already be installed. Do not run the test-only browser server against a live database; it creates only its own temporary test database and simulated identities/provider.

## Verification and limits

- Full isolated backend suite: `DATABASE_URL=sqlite:///:memory: python -m pytest -q` — one run had **284 passed, 12 skipped**. The latest full run had **283 passed, 12 skipped and one intermittent failure** in the existing R2/SQLite concurrent-avatar test (`test_db_failure_cleans_up_orphan_r2_object_and_concurrent_replace`); an isolated retry passed. That test and the avatar/R2 implementation were not changed by this feature.
- RFQ import, material lookup and publication matching regression selection — **34 passed**, including Arabic headers/sizes, malformed CSV protection, composite/grade incompatibility and recovery of an invalid quantity. The selection including the existing R2-test retry had **35 passes**.
- Existing RFQ material-row frontend tests — **3 passed**. Frontend typecheck and production build passed using `.next-rfq-import`, avoiding the locked default build directory.
- Browser QA runs the built Next.js page against a real isolated FastAPI server, private local upload storage, durable background worker, SQLite database and an actual local HTTP Gemini simulator. It verifies template download, real CSV upload/parsing, headings/repeated headers, AI reranking, explicit review, manual-item preservation, edited descriptions/quantities, deterministic double-click handling, Step 3, draft creation, explicit publication and exactly one eligible supplier invitation. It checks desktop/tablet/mobile widths and Arabic RTL. Commands:

```powershell
cd frontend
$env:NEXT_BUILD_DIR='.next-rfq-import'
npm.cmd run build
npm.cmd run test:rfq-import
```

The browser driver starts only local test processes and uses synthetic data; no real messages are sent. Screenshots are under `frontend/.test-runtime/rfq-import/`. Set `PYTHON_BIN` or `CHROME_PATH` if the default executables differ.

Live Gemini account/model/quota behavior, live PostgreSQL plans/concurrent leases, private R2 deployment access, Redis production availability, production load and actual notification delivery remain unverified. Existing platform production gates remain in force. This implementation is **not a production-readiness claim**; staging should exercise the complete workflow with the actual configured providers before rollout. Review source-file retention policy for the new buyer job kind; no existing files or database data were deleted as part of this change.

## Files changed for this feature

- Backend: `app/config.py`, `.env.example`, `app/material_import_worker.py`, `app/modules/imports/materials.py`, `app/modules/rfqs/router.py`, `app/modules/rfqs/imports.py`, `app/modules/rfqs/import_schemas.py`, `app/modules/rfqs/material_matching.py`, `app/schemas.py`.
- Frontend: `features/rfqs/workspace.tsx`, `features/rfqs/material-import.tsx`, `features/rfqs/material-import.css`, `features/shared/grouped-material-entry.tsx`, `package.json`, generated API schema/routes.
- Contracts/tests/docs: `docs/openapi.json`, `tests/test_rfq_material_import.py`, `tests/rfq_import_browser_server.py`, `frontend/tests/rfq-import-browser.mjs`, this guide and `IMPLEMENTATION_STATUS.md`.

Prior grouped-entry edits present in the workspace were retained.
