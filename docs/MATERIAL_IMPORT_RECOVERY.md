# Material import preview recovery and worker deployment

The material validator accepts optional Discipline plus SN, Main Category,
Sub Category, Description/Pacification, UNIT and Expenditures. Supported category
labels/prefixes infer Civil, Electrical or Mechanical without editing category
names. Existing unambiguous category mappings precede the upload fallback.
Explicit unsupported values and genuine category conflicts affect only their row.

## Saved previews

The legacy message `Row discipline conflicts with the selected import discipline`
is no longer emitted by the current validator. A saved preview can still contain
that historical result. Current code marks previews generated before validation
version 4 as needing review and prevents confirmation of their old row outcomes.
Choose **Recheck preview**, or upload the workbook again. Re-upload bypasses the
stale-preview recovery shortcut and creates a fresh parsing job. The old preview
and its source rows remain intact. Current previews and confirmed jobs are still
recovered without duplication. Confirmed work is never automatically reparsed.

HTTP 202 means parsing was queued, not that workbook validation passed. Poll
`GET /api/v1/imports/materials/{batch_id}` for the status, valid/invalid/duplicate
counts, errors, and persisted progress. Confirm the fresh preview to queue writes.
Application commits at most 30 source rows per transaction and at most 300 per
job/poll. A 5,118-row workbook requires 18 application jobs. `completed_rows`
counts processed source rows; `created`, `updated_count`, `unchanged_count`,
`invalid`, `conflicts` and `duplicate_rows` distinguish their outcomes.

## One dedicated worker on the Linux API host

After deploying the updated checkout to `/var/www/procura`, run:

```bash
cd /var/www/procura/backend
sudo /var/www/procura/venv/bin/python3 scripts/install_import_worker.py --activate
sudo systemctl status procura-import-worker.service --no-pager
sudo journalctl -u procura-import-worker.service -n 60 --no-pager
```

The installer reuses the API's user, Python and `.env`. It configures only the
existing `app.material_import_worker` runner, disables embedded import polling
in the API via a systemd drop-in, restarts the API, and enables/restarts one
`procura-import-worker.service`. It refuses to overwrite an unmanaged worker
service or launch beside a custom/manual runner for the same checkout. Inspect
and reuse any such existing service explicitly. It does not stop unknown workers,
change database/R2 settings, run migrations, or delete business data.

Dedicated and embedded pollers share a host-local file lock; duplicate dedicated
instances exit with status 75. The unit does not restart on that status. Queue
transaction/advisory locks remain in place across hosts. Keep the lock file in
place; never remove a held lock to force another worker to start. An exception
in RFQ, pricing or supplier processing cannot skip the material queue: each queue
is polled independently using a fresh database session.

On a custom installation, pass `--backend-dir`, `--python`, and `--api-service`.
Omit `--activate` to write and verify the unit/drop-in without starting them;
activation and the API restart are still required. No remote service is installed
by merely changing files in a local checkout.

## Disposable PostgreSQL verification

The 5,118-row regression can target an empty PostgreSQL database initialized by
`alembic upgrade head`. Set `MATERIAL_IMPORT_TEST_DATABASE_URL` to that disposable
database and run only:

```bash
python -m pytest -q tests/test_material_category_bulk.py::test_5118_failed_export_rows_preview_and_queue
```

For safety the fixture requires a loopback host, a database name beginning with
`procura_material_test_`, and zero categories/materials. It never creates the
PostgreSQL schema with `create_all` or deletes records afterward. The test imports
all 5,118 rows across fresh worker sessions, checks persisted counts and category
relationships, and verifies that re-upload matches all existing material IDs.
Use a newly migrated disposable database for each run. Tests in the ordinary
suite use isolated SQLite databases. Generated fixtures reproduce the reported
Excel format; the user's actual workbook must be supplied to measure its own
genuine row errors.
