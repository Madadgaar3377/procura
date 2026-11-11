# Fresh supplier import repair — 2026-11-11

## Excel parsing follow-up

The deployed upload returned generic 422 with request ID
`6f963331-cf71-41fb-a860-9fcecbe66755`. A valid streaming-generated XLSX reproduces
that error: worksheet dimensions are optional and `sheet.max_row` can be None;
the bulk parser compared it with 30,001 and raised TypeError. The exact uploaded
file was not supplied, so this reproduction does not prove its precise failure.

The parser now handles missing dimensions, resets inaccurate dimensions to avoid
silently losing rows, enforces actual row/column bounds while reading, always
closes the workbook, and safely logs unexpected failure types. Regression tests
upload 50 rows with omitted dimensions and with a false A1:A1 dimension; both save
25 companies and 50 employee responsibilities. Row/column limit tests also pass.
Full follow-up backend run: **82 passed, 5 skipped**, with opt-in database checks
disabled. This parser patch requires an API restart and no new migration.

## Deployment status

Implemented in this checkout. No code was deployed and no production rows or
schema were modified. Apply Alembic revision `j40a8c12d506` before starting the
updated API and existing import worker. Production was inspected read-only.

The original first and second split Excel workbooks are absent from the workspace.
Real-data testing used the **47 source rows retained in production import history**,
reconstructed into the existing Excel template, and the production category tree.
This is not evidence that the original 50-row workbook contains only 47 rows or
that the missing three rows were read correctly. The two original files are still
needed to verify their complete contents and actual cross-file company overlap.

## Confirmed production state and causes

Read-only queries against the configured PostgreSQL database found:

| Record | Count |
| --- | ---: |
| Active supplier organizations | 0 |
| Inactive supplier organizations | 44 |
| Organization contacts | 167 |
| Supplier capabilities | 61 |
| Contact-capability relationships | 27 |
| Imported supplier trades | 74 |

Production revision was `g39d7e5b2c84`. Contact → organization,
capability → organization/category, contact-capability → contact/capability, and
source trade → organization/import batch foreign keys exist. Import history is
retained: one completed durable supplier job, five completed legacy supplier
imports and two legacy supplier imports completed with errors. Other material and
category history was also present and was not changed.

The existing Delete selected control calls archive: it sets `is_active=False` and
preserves contacts, capabilities and audit history. Import organization lookups
did not filter inactive organizations, so they reused hidden suppliers. Further,
same-file upload deduplication searched all historical jobs and returned an old
completed response without doing new persistence work.

The retained job exactly reproduces the reported metrics: 28 supplier groups,
5 saved capabilities, 45 skipped capability/material rows, 19 pending mappings.
Several legitimate Electrical rows were ambiguous to the old flat name lookup
because both a root and a child category were named Electrical.

Unresolved source trades had only company-level storage; their employee
relationship was lost. Material assignment existed only on the shared company
capability, so employees could inherit a colleague's materials in the detail API.
Unknown materials were never created in the contact/category path. The other
mapping path swallowed arbitrary material exceptions. Completion status ignored
skipped and pending relationships. Missing optional contact channels also broke
name-only contact idempotency. Real replay found a further crash: conflicting
employee names sharing an email could dereference a nonexistent contact and roll
back their entire company group.

## Changes

- Fresh identity lookup considers active suppliers and nondeleted contacts only.
  A supplied company name is authoritative; shared office email/phone values do
  not merge different named companies or reject an otherwise valid company.
- Only queued/processing identical uploads share a job. Completed-file uploads
  run again, with business identity and relationship deduplication preventing
  duplicate companies, contacts and responsibilities. Import history is preserved.
- Existing source-format headers are accepted directly, including Mobile/Phone
  and Verification Notes. Blank-company continuation includes material-only rows.
  Unidentified source rows are retained for validation instead of silently dropped.
- Each exact employee-to-raw-trade/material responsibility is persisted before
  taxonomy resolution. The existing supplier detail view displays these values,
  including pending mappings. Company-level source descriptions remain retained.
- Employee capability links now store their own material IDs. Company capability
  material IDs remain the union; the detail API uses each employee's own IDs.
  Legacy NULL scopes retain their previous behavior and are not rewritten.
- Existing approved categories/materials are reused first. Explicit discipline
  paths can create missing categories. Electrical/Civil/Mechanical source labels
  select the discipline root despite similarly named child entries.
- Clearly countable equipment labels (for example Panel Board, Transformers,
  Lighting Fixture) can create materials using the existing `No.` unit convention.
  Explicit source units take priority. Broad acronyms, mixed products and unknown
  quantities/units remain reviewable; no unit is guessed for cables or mixed trades.
- Material lookup errors are no longer swallowed as success. Category review and
  material review counters are distinct from saved/reused capabilities. An import
  with unresolved mapping work ends `completed_with_errors`, even with zero
  supplier failures. 100% means all source groups were processed, not approved.
- Existing ten-company transactions, per-company savepoints, transaction locks,
  durable checkpoints and infrastructure retries remain. Normalized name lookup
  now uses its index directly. Contact identity channel reads are batched instead
  of repeatedly selecting channels for each candidate employee.
- The existing Suppliers table refresh clears search, status and page filters.
  Retry failed rows is shown only when failed suppliers exist.

## Migration

`backend/alembic/versions/j40a8c12d506_supplier_import_responsibilities.py`:

1. Adds nullable `material_ids` to `supplier_contact_capabilities`.
2. Adds `contact_supplier_trades`, with contact/trade foreign keys, a composite
   primary key preventing duplicate responsibilities, and a trade lookup index.
3. Narrows the supplier-file unique index to queued/processing jobs.
4. Narrows verified CR uniqueness to active suppliers while retaining its prior
   scope for nonsupplier organizations, so an archived supplier's CR cannot block
   a fresh verified import.

No production records are deleted, reactivated or backfilled. Downgrading after
fresh retries can conflict with the former broader unique indexes; it fails safely
rather than deleting history or supplier records to force the downgrade.

## PostgreSQL replay results

Tests initialize new isolated local PostgreSQL databases through Alembic. No
production business records were imported or changed. Retained source data stays
in ignored `.test-runtime` artifacts and is not included in this report or Git.

| Record | First replay | Reupload: new records |
| --- | ---: | ---: |
| Companies | 28 | 0 |
| Employee contacts | 46 | 0 |
| Raw employee responsibilities | 47 | 0 |
| Company capabilities | 26 | 0 |
| Employee-category links | 43 | 0 |
| Materials | 9 | 0 |
| Employee-material assignments | 21 | 0 |

The admin Suppliers API returns all 28 companies. First replay processed the
47 retained rows in approximately 2–4 seconds on local PostgreSQL. There were
zero supplier failures. Three category rows and 23 material rows remain pending;
all retain their employee/source association. This is correctly reported as
partial completion, not a fully successful taxonomy import. Production network
latency and deployed worker throughput were not benchmarked.

Synthetic original-format tests additionally import two 50-row files: first
25 companies/50 employees; second 20 new companies, 5 reused companies and
50 additional employees. Reuploading either creates zero duplicate companies,
contacts, assignments or responsibilities. Another test verifies two employees
sharing one company category retain different material sets in PostgreSQL and the
existing admin detail endpoint. Archive/fresh import, archived CR reuse, missing
optional fields, malformed workbooks, concurrent jobs, real PostgreSQL timeout,
worker exit/restart and transaction retry coverage are retained.

Full backend suite with PostgreSQL enabled: **82 passed, 1 skipped** (the unrelated
hosted-database smoke test). A subsequent focused run covering the final phone
preservation and mapping-status changes plus real PostgreSQL replay: **13 passed**.
Frontend typecheck passed. Frontend API/UI suite: 53 passed; its unrelated sitemap
test failed because no Next.js server was listening on port 3000. No new browser
session against the deployed admin UI was run; visibility was verified through
the actual admin API contract and the existing table's refresh implementation.

## Files changed

- `backend/app/models.py`
- `backend/alembic/versions/j40a8c12d506_supplier_import_responsibilities.py`
- `backend/app/modules/imports/bulk.py`
- `backend/app/modules/imports/supplier_jobs.py`
- `backend/app/modules/imports/supplier_mapping.py`
- `backend/app/modules/organizations/contact_import.py`
- `backend/app/modules/organizations/router.py`
- `backend/tests/test_supplier_bulk_jobs.py`
- `backend/tests/test_supplier_source_replay.py`
- `backend/tests/test_bulk_upload_batches.py`
- `backend/scripts/inspect_supplier_source.py` (read-only private replay extraction)
- `backend/scripts/prepare_supplier_test_db.py` (loopback-only test setup)
- `frontend/features/admin/bulk-upload.tsx`
- `frontend/features/admin/workspace.tsx`
- `frontend/features/shared/server-list.tsx`
- `docs/openapi.json` and `frontend/lib/api/generated/schema.ts`
- `backend/IMPLEMENTATION_STATUS.md` and this report

For private replay, set `DATABASE_URL=sqlite:///:memory:` (to disable unrelated
hosted smoke tests), `SUPPLIER_BULK_TEST_DATABASE_URL` to a loopback PostgreSQL test
database with CREATEDB permission, and `SUPPLIER_SOURCE_REPLAY_PATH` to the ignored
recovered source JSON. Run `python -m pytest -q` from `backend/`.
