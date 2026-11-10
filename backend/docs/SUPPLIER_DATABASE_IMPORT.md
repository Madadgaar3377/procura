# Supplier Database Import

Supplier spreadsheet imports reuse the existing `organizations`, `organization_contacts`, `imported_supplier_trades`, `import_batches`, and `import_rows` tables. They do not create login users, verify companies, enable contact permission, or create approved capabilities.

## Admin workflow

1. Open **Admin → Data Management → Bulk Import** and choose Material Master or Supplier Database.
2. Upload XLS, XLSX, or UTF-8 CSV (maximum 8 MB). The request scans rows to prepare the preview; XLS uses `xlrd` and XLSX is parsed read-only with cached formula values. Confirmed writes run in the worker.
3. Review detected columns and worksheet names. For multi-sheet workbooks, choose a data worksheet or **Import all eligible worksheets**. Reference/lookup worksheets are excluded.
4. Review the supplier preview, duplicates, invalid contact values, and source worksheet/row references. Confirm explicitly.
5. The existing Material Master worker applies confirmed supplier records in bounded 500-row transactions. Job status and progress are available from import history and the live preview.
6. Download the complete failed-row CSV from the job preview. It includes source row information and escapes formula-leading cell values.

Blank supplier names are carried forward only within one worksheet and only when a row contains contact or trade details. Original phone and email text remains in the stored source row; invalid contact values are omitted from the usable contact fields and flagged for review. Multiple contact rows remain associated with the normalized imported organization. Imported source trades retain their descriptions and remain `unreviewed` until an administrator maps them to existing taxonomy and reviews a capability.

## API

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /api/v1/imports/suppliers?dry_run=true` | Admin | Validate a supplier workbook and create a confirmable preview |
| `POST /api/v1/imports/suppliers/{batch_id}/confirm` | Preview owner admin | Queue the reviewed supplier import (`202 Accepted`) |
| `GET /api/v1/imports/suppliers/{batch_id}` | Admin | Read counts, progress, source-aware sample, and review issues |
| `GET /api/v1/imports/suppliers/{batch_id}/errors.csv` | Admin | Download every row with a validation/review issue |
| `GET /api/v1/imports/supplier-trades` | Admin | Search imported source trades awaiting taxonomy mapping |
| `POST /api/v1/imports/supplier-trades/{trade_id}/map` | Admin | Map a source trade to an existing category and explicitly confirmed city; creates a capability pending separate approval |
| `GET /api/v1/imports/batches` | Admin | Review import history |

Run the existing worker for both Material Master and supplier jobs:

```powershell
cd backend
python -m app.material_import_worker
```

The admin Bulk Import page includes a supplier trade review queue. Mapping requires an existing category and a city entered/confirmed by the administrator. It creates no material IDs, and the resulting capability is created with RFQs disabled and remains pending a separate capability approval. The matching workflow continues to require a verified supplier and an eligible, reviewed category/city capability. Raw imported trade text alone never sends an RFQ invitation.

## Current operating limit

Supplier workbook preview parsing still runs during the upload request. It is bounded to 8 MB and 30,000 rows per worksheet, but parsing itself is not yet a durable worker stage. For larger/slow workbooks, move supplier upload staging and preview parsing into the worker path used by Material Master before production-scale bulk onboarding. Confirmed supplier database writes are already queued and chunked.
