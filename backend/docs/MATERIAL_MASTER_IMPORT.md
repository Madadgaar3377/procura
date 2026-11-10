# Material Master workbook import

## Admin flow

1. Upload an XLS, XLSX or UTF-8 CSV (maximum 8 MB).
2. The API detects sheets, header rows, recognized columns and likely reference sheets. An administrator selects one material worksheet or imports all eligible data worksheets. Sheets whose names indicate reference, suggested, lookup, classification mapping, or expenditures are excluded.
3. Select or confirm Civil, Electrical or Mechanical, adjust column mapping if needed, and review valid rows, invalid rows, categories and create/update/unchanged classifications.
4. Confirm the staged preview. In local development (`APP_ENV=development`), the API runs the durable import worker automatically. Other deployments run the worker as a separate service. Confirmed rows are applied in bounded 500-row chunks.

Formulas are never evaluated. XLSX files are read-only with cached values, bounded compressed/uncompressed size, bounded columns and rows, and maximum cell lengths. Legacy XLS uses `xlrd` and is subject to the same row/column limits. Valid unlisted unit tokens are added to the existing measurement-unit table and preserved as supplied. The import does not create Arabic translations, infer specifications, send notifications, or import supplier data.

## API

All endpoints are under `/api/v1` and pass through the same-origin Next.js BFF when called from the browser.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /imports/materials/preview` | Admin | Privately stage the workbook and return a durable job ID (`202 Accepted`) |
| `POST /imports/materials/{batch_id}/preview-options` | Preview owner admin | Reparse a staged workbook using selected worksheet (or all eligible sheets), discipline, or column mapping |
| `POST /imports/materials/{batch_id}/confirm` | Preview owner admin | Queue the reviewed import for bounded worker application |
| `GET /imports/materials/{batch_id}` | Admin | Read status, progress, row sample and bounded error report |
| `GET /imports/materials?q=&discipline=&category_id=&limit=&offset=` | Active buyer/supplier member or admin | Paginated material search |
| `GET /taxonomy/categories` | Existing contract | Read category hierarchy |
| `GET /taxonomy/materials?q=&discipline=&category_id=&limit=&offset=` | Existing contract | Material lookup used by RFQ flows |
| `GET /imports/batches` | Admin | Import history |

Material source identity is separate from internal material code, source SN, and expenditure classification. Repeated SN and expenditure values are permitted. Exact repeated material identities are skipped; different descriptions, units, or specifications remain distinct and existing materials are never silently overwritten. Existing RFQ item snapshots are not modified.

Civil/Electrical aliases include `Description`, `Main Category`/`Main`/`Category`, `Sub Category`, `UNIT`, `SN`, and `Expenditure(s)`. `SN` is stored as a source identifier; expenditure classification remains non-unique. Filenames may identify discipline even when words are separated by underscores.

## Large import worker

The FastAPI request only streams the workbook to private local staging and records the job. Start this process alongside the API and notification worker:

```powershell
cd backend
python -m app.material_import_worker
```

The worker parses with read-only XLSX mode or the legacy XLS reader, records preview rows and commits confirmed materials and suppliers in 500-row chunks. It updates durable batch/row status for polling, retries stale processing jobs after a restart, and sends no email or SMS. `--once` processes one bounded stage/chunk and exits. Local disk staging assumes the API and worker share one machine; use a shared storage adapter for multi-host deployments.

## Database and verification limits

Alembic revisions `e83c1a9d5b24` and `b19c4e7a1d62` add material hierarchy/unit/revision fields, durable private import job metadata, PostgreSQL `pg_trgm` indexing, and optional exact-material supplier capabilities. Existing RFQ item snapshots remain untouched. Do not apply it to a hosted database without the operator's explicit approval.

The client-provided Civil, Electrical, and supplier workbooks were not found in this workspace, so their worksheet names, actual column mappings and real row counts remain unverified. Synthetic Civil-like and Electrical `AG` fixtures cover parsing and hierarchy; a 15,000-row fixture exercises upload, staging, queue processing and search on SQLite. PostgreSQL `EXPLAIN ANALYZE`, query latency and throughput on PostgreSQL remain unverified.
