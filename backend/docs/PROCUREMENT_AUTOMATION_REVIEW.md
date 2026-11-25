# Procurement automation implementation and verification

Date: 2026-11-25. Status: core verified locally on PostgreSQL; live deployment and provider delivery are **not signed off**.

## Findings and root causes

1. The working tree already contained a publication-after-commit matching repair. It was retained. The documented `app.notification_worker` still used its older matching path: RFQ locking and draining every supplier page in one transaction. It now calls the same bounded, independently committed page processor as `app.worker`. Cursor continuation remains durable in the publication outbox row. An ORM refresh transaction is ended before entering the independent matching session.
2. Invitation processing only called `prepare_authorized_quote`. With automation disabled, that function returned without checking prices. A separate `prepare_invitation_quote` now prepares private drafts for eligible invitations regardless of submission authorization. No eligible prices means a manual quotation request; some prices means a partial draft. Existing drafts are retained.
3. The worker swallowed quotation preparation exceptions. A failed SQL transaction could then reach commit, or a non-SQL failure could permanently lose draft preparation. Exceptions now reach the existing rollback/retry handler; notification jobs and quotation preparation commit together.
4. Prefill previously checked material/version/unit but omitted MOQ, description/specification compatibility and a complete source-rate snapshot. Drafts now reject incompatible saved prices and retain a JSON snapshot plus source ID/version. Editing an offer retains its original price provenance. No historic snapshot is fabricated for old quotations.
5. Contact delivery already had responsibility mappings, normalized destinations, deduplication and restrictions. Material-scoped assignments could fall through to category routing. That fallback is now prohibited when material scope exists. In-app matching previously alerted every company member; it now selects responsible account destinations with owner/manager fallback and preference checks.
6. Imported suppliers received links into an authenticated workspace. New signed links use a separate JWT audience/type and authorize one invitation only. The server rechecks expiry, RFQ state, current matching eligibility and destination authorization on every use. Tokens travel in the URL fragment and POST body, are removed from browser history and never become general account credentials. Explicit supplier confirmation is required for guest submission.
7. The original quotation arithmetic used a quotation-wide VAT rate only. Saved rates can now carry an optional VAT rate; generated lines retain it. Missing rate VAT uses the quotation VAT. Supported private currencies are SAR, USD, EUR, AED and GBP. A draft uses one currency; mixed currencies produce a partial review draft without conversion. Existing automatic-submission policy remains restricted to SAR.

No production blocker PID or deployed idle timeout was observed: the configured remote connection failed certificate validation (`certificate contains IP address with invalid length 16`). It would be inaccurate to attribute a specific deployed blocker to a process without that evidence. The code paths above explain excessive transaction duration and the local PostgreSQL lock/retry test reproduces the contention behavior. Certificate verification was not disabled.

## Existing architecture retained

FastAPI, SQLAlchemy/PostgreSQL, RFQ matches/invitations, contact capability assignments, private price book, quotation lines, transactional outbox, SMTP and SMS adapters, retry/suppression handling and the existing buyer comparison workspace are reused. No directory rebuild, microservice or AI service was introduced. Existing supplier/material rows were not removed. Existing uncommitted changes were preserved.

Publication commits before matching. Matching commits before its invitation events can be consumed. The delivery worker commits before SMTP/SMS calls and captures provider arguments before the commit so an expiring ORM session cannot reopen a read transaction while making the call. SMTP outcome uncertainty remains `uncertain` for reconciliation; it is not blindly retried and described as exactly-once email delivery.

## Changed files

Changes made for this task:

- `app/models.py`, `app/schemas.py`, `app/response_contracts.py`: additive private tax, original-rate snapshot, commercial terms and response contracts.
- `app/modules/quotations/automation.py`, `router.py`, new `document.py`: safe price eligibility, automatic private preparation, guarded submission, provenance, tax arithmetic and layout-independent quotation data.
- `app/notification_worker.py`, `app/worker.py`: bounded matching, transaction separation, preparation retry and provider I/O boundaries.
- `app/modules/notifications/recipients.py`, `procurement.py`, `branded_templates.py`: employee scope, in-app routing, missing-price requests and actionable SMS/email links.
- `app/modules/matching/service.py`, `router.py`, new `guest.py`: scoped in-app dispatch and invitation-scoped guest access.
- `alembic/versions/zg50c723b118_procurement_price_snapshots.py`: new additive migration.
- `tests/test_procurement_automation.py`, `tests/test_rfq_publish_matching_fallback.py`: workflow coverage and optional migrated PostgreSQL fixture.
- `../frontend/features/suppliers/price-book-entry.tsx`: MOQ, VAT and currency controls in the existing form.
- `../frontend/features/quotations/workspace.tsx`, new `guest-invitation.tsx`, new `app/rfq-invitation/page.tsx`: quotation source/terms visibility and private guest response form.
- `../docs/openapi.json`, `../frontend/lib/api/generated/schema.ts`, `routes.json`: regenerated contracts.

Already modified on arrival and retained: RFQ publication/router and matching recovery work, matching page query changes, matching queue lookup migration `zf49b612a007`, worker/config batch tuning, `material_import_worker.py`, matching response fields, matched-supplier frontend, recovery documentation and `scripts/{diagnose_rfq_locks,requeue_rfq_matching}.py`. These are not claimed as wholly new work.

## Migrations and worker configuration

`zf49b612a007` adds the partial PostgreSQL RFQ matching outbox lookup index. `zg50c723b118` adds nullable `price_book_items.vat_rate`, nullable `quotation_lines.vat_rate`, nullable `quotation_lines.source_rate_snapshot` and non-null `quotations.commercial_terms` with an empty-object default. No table replacement, price invention or destructive backfill is performed. Normal existing quotation/invitation/match uniqueness constraints are reused.

Run **one** notification worker entry point, preferably:

```powershell
python -m app.notification_worker --interval 5
```

Run it as a supervised service with automatic restart and the same environment as the API. Configure `DATABASE_URL`, a valid PostgreSQL CA/hostname, `JWT_SECRET`, `FRONTEND_URL`, `MATCHING_BATCH_SIZE` (default 5), existing SMTP settings and SMS gateway settings. `MOCK_NOTIFICATIONS=true` means simulated delivery. Turn it off only for approved live delivery; starting a real worker may send already queued notices. Do not run both notification worker entry points concurrently. Keep the existing import worker service separately if needed.

## Deployment sequence

These are deployment commands, not a claim that production was deployed. Preserve the existing production startup/security gate; this work does not waive it.

```powershell
cd D:\Saudi_B2B_Procurement_Complete_Backend\backend
python -m alembic current
python -m alembic heads
# After backup, verified TLS/database identity and review of the target revision:
python -m alembic upgrade head
python -m app.notification_worker --diagnose
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Separate supervised worker process:
python -m app.notification_worker --interval 5
```

```powershell
cd D:\Saudi_B2B_Procurement_Complete_Backend\frontend
npm.cmd run api:generate
npm.cmd run typecheck
npm.cmd run build
npm.cmd run start
```

For an existing failed open RFQ, inspect first, then requeue using the retained recovery tooling:

```powershell
cd D:\Saudi_B2B_Procurement_Complete_Backend\backend
$env:PYTHONPATH='.'
python scripts/diagnose_rfq_locks.py <rfq-id>
python scripts/requeue_rfq_matching.py <rfq-id>
python scripts/requeue_rfq_matching.py <rfq-id> --prepare-existing-invitations
```

The recovery command does not delete matches. Expired processing leases are consumed by the active worker. The optional `--prepare-existing-invitations` flag replays preparation for invitations without quotations while retaining delivery deduplication keys. Existing manually edited drafts are never overwritten. These commands were not run against the remote database because its TLS connection failed.

## Verification and remaining approvals

Tests use synthetic records in real SQLAlchemy databases and real FastAPI routes. PostgreSQL tests create uniquely named databases, apply the full Alembic history, exercise the workflow, then drop only those test databases. SQLite alone is not used as evidence of PostgreSQL locking. Provider-boundary tests substitute transport and are not evidence of live SMTP/SMS delivery. See the test result addendum below for final counts.

Client decisions still required: official quotation/PDF layout; commercial default terms and applicable tax policy; which suppliers may authorize automatic submission and approved quantity/value/material/coverage limits; guest-link access policy and validity; production notification recipients and channel configuration. No permanent PDF layout was selected. `document.py` emits layout-independent supplier/buyer details, references, date, descriptions, brand/model, quantity/unit, price, VAT/totals and configurable commercial terms for the future renderer.

Production sign-off still requires a corrected remote TLS connection, migrations on the verified deployment target, approved live SMTP/SMS tests, deployed worker consumption/recovery evidence and resolution of the repository's pre-existing production startup requirements. Browser interaction with real accounts and actual inbox/handset delivery are not claimed.

## Test evidence

- `procurement-final-results.txt`: 25 passed, 1 skipped. Includes SQLite and real PostgreSQL workflows, full/partial/missing/expired/incompatible saved prices, MOQ/unit checks, source snapshots, recipient scope, explicit guest submission and revocation, authorization bounds, retries, replay and a real PostgreSQL lock conflict. PostgreSQL schemas were created by Alembic in disposable databases; no production data was used. The skip is the PostgreSQL-lock test under SQLite.
- `procurement-regression-results.txt`: 48 passed. Includes matching, the existing 2,000-supplier elapsed-time threshold, matching failure/retry, contact delivery, private price books and lifecycle notifications. Provider calls in these tests are substituted and do not establish real delivery.
- Final full suite: **336 passed, 26 skipped, 6 failed**, recorded in `procurement-full-verified-results.txt`. Remaining failures are two supplier-import capability counter expectations, one category-review matching expectation, two legacy hybrid/AI classification expectations, and the intermittent R2/SQLite avatar concurrency test. These also failed in the starting run; they are unresolved and the entire suite is not green. Starting implementation: 321 passed, 12 skipped, 9 failed. A new guest response validation issue found in an intermediate run was fixed; it does not fail in the final suite.
- Frontend typecheck passed. The frontend build compiled, typechecked and generated all pages including `/rfq-invitation`, but returned exit 1 at the installed Next.js security advisory gate. Upgrade the affected Next.js release before production deployment. Repeating the build did not clear the gate. No dependency upgrade was silently performed.
- `compileall` passed using a writable `PYTHONPYCACHEPREFIX` after the initial invocation encountered Windows permissions on existing `.pyc` files. Alembic reports one head, `zg50c723b118`. The full migration history successfully applied to the isolated PostgreSQL 18.6 instance.

The contact-provider and import-runner unit fixtures were updated to include commit/rollback/close behavior and the additional outbox queue. This verifies the retained transaction boundaries rather than weakening them to satisfy obsolete test doubles.

For PostgreSQL reproduction, set `TEST_PROCUREMENT_POSTGRES_URL` to a disposable local PostgreSQL instance whose test role can create databases, then run `python -m pytest -q tests/test_procurement_automation.py`. The fixture creates UUID-prefixed test databases and drops only those it creates. Do not point that variable at an unknown production cluster.
