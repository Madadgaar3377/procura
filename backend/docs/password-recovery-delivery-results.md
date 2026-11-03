# Procura recovery and delivery verification — 2026-10-25

## Confirmed findings

- `backend/.env` has external delivery enabled, SMS live mode enabled and SMS/SMTP credentials present. No credential values were printed or changed.
- The original settings loader resolved `.env` relative to the process working directory. Starting from another directory could silently select mock/disabled defaults. Settings now resolve the backend environment file independently of the launch directory; process environment overrides still take precedence.
- Recovery email templates had codes but no reset link. Emails now include a configured frontend URL with a one-time token in its fragment. Only a token hash is persisted in the recovery record. The browser removes the fragment after reading it. Outbox secrets remain encrypted until processing and are cleared after submission or expiry.
- The active worker now rechecks verified recovery destinations and destination snapshots before sending. Security messages bypass optional email/SMS preferences. Expired, consumed and exhausted codes are rejected before retry or reconciliation.
- Password reset invalidates outstanding recovery artifacts, revokes refresh sessions and queues password-change security notices. Registration email verification and phone verification use distinct purpose-bound codes.
- SMTP acceptance is recorded as `accepted`; SMS HTTP 202 is recorded as pending acceptance and reconciled by message ID. Neither means confirmed inbox/device receipt.
- The legacy `app.worker` entry point remains separate: managed filesystem permissions rejected an attempted edit. Run `app.notification_worker`, which is the corrected delivery path.

## Live-environment blockers

- Read-only TCP checks for SMTP and SMSGate failed with `PermissionError` in this environment.
- SMTP authentication probe failed with `PermissionError`; SMS device listing and DNS-over-HTTPS checks failed with `ConnectError`. Credentials were not proven valid or invalid. Device online status, SPF, DKIM, DMARC and real delivery remain unverified.
- Read-only Aiven connection and outbox diagnostics failed before authentication; `main.py` reported local network permission denied. No hosted database writes, resets or migrations were performed.
- No explicit `NOTIFICATION_TEST_PHONE` or `NOTIFICATION_TEST_EMAIL` is configured. No live messages were sent. Live acceptance and actual receipt must be verified separately after recipient approval on a machine with network access.
- These observations establish local execution blockers and code defects, not the exact cause of an inaccessible running deployment's incident.

## Changed implementation files

- `app/config.py`: stable environment-file resolution; recovery policy settings.
- `app/modules/auth/router.py`: purpose-bound recovery, link hashes, verified destinations, quotas/cooldowns, token invalidation, password-change notices and secret-free HTTP responses.
- `app/notification_worker.py`: destination/eligibility checks, expiring security jobs, reset-link rendering and delivery state handling.
- `app/modules/notifications/delivery.py`: SMTP recipient validation.
- `app/modules/notifications/branded_templates.py`: English/Arabic reset links, code expiry and password-change alerts.
- `../frontend/features/auth/auth-page.tsx`: fragment-token handling, resend cooldown, duplicate submission protection, password confirmation and reset success state.
- `scripts/diagnose_delivery.py`: safe configuration, network, SMTP login, SMS device-list and public DNS checks; no message submission.
- `scripts/live_test.py`: prepare-only by default; approved test uses isolated SQLite, two authorized recovery deliveries and checks password/session behavior. Automatic dispatch is disabled temporarily so the configured database queue is never processed.
- `tests/test_recovery_delivery_regression.py`: recovery-link, destination, privacy, cooldown, session and SMTP regression coverage.
- `tests/conftest.py`: restore mocked database drivers before disposable-schema teardown; observe generated test codes internally instead of HTTP disclosure.
- Concurrent auth/procurement updates were preserved and tested, including `app/modules/notifications/procurement.py`, `tests/test_recovery_regressions.py` and `tests/test_procurement_delivery_regressions.py`.

Existing routes are preserved: `/api/v1/auth/password/forgot`, `/password/verify`, `/password/reset`, `/otp/request`, `/otp/verify`, `/email/verification/request`, `/email/verification/confirm` (all under `/api/v1/auth`).

## Local commands

Run from `backend`, with the configured database accessible:

```powershell
.\.venv-auth\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Run the corrected worker in a separate terminal. With the current live configuration, this can send existing eligible outbox jobs:

```powershell
.\.venv-auth\Scripts\python.exe -m app.notification_worker
```

Read-only checks and prepare-only live test:

```powershell
.\.venv-auth\Scripts\python.exe scripts/diagnose_delivery.py --check-connections --check-providers --check-dns
.\.venv-auth\Scripts\python.exe -m app.notification_worker --diagnose
.\.venv-auth\Scripts\python.exe scripts/live_test.py
```

Only after explicit approval for the configured recipient pair, `scripts/live_test.py --approved` attempts one recovery SMS and one recovery email. It uses an exclusive local ledger to prevent accidental repeat sends, reports provider acceptance/status without secrets, and does not claim actual receipt. Password-change alerts remain in its isolated database and are not dispatched.

Frontend commands, from `frontend`:

```powershell
npm.cmd run dev
npm.cmd run typecheck
npm.cmd run test:api
$env:NEXT_BUILD_DIR='.next-auth-recovery-check'
npm.cmd run build
```

The normal `.next` build directory was locked (`EPERM`); the isolated build directory avoids changing the existing running application's output. `lint` is a TypeScript check in this repository; ESLint is not configured. Postman CLI/MCP was unavailable, so provider behavior was exercised with existing loopback HTTP and SMTP mocks, not Postman-hosted simulations.

## Executed verification

- Targeted recovery, notification and auth-dispatch suite: **27 passed** (mock providers and loopback HTTP).
- Additional combined recovery suite: **42 passed**, followed by a database-test teardown error. The fixture ordering issue was corrected; rerunning the database status tests produced **2 passed**.
- Frontend API tests: **29 passed**.
- TypeScript: passed. Next.js production build using `.next-auth-recovery-check`: passed, with the existing `metadataBase` warning.
- Python compilation of changed delivery scripts/modules: passed.
- Active procurement delivery regressions: **2 passed** (RFQ publish, eligible invitations, provider failure isolation and deduplication).
- Final full backend run: `python -m pytest -q --tb=short` — **238 passed, 2 skipped, 1 failed** in 201.89 seconds. The sole failure is `test_security_integrations.py::test_email_verification_and_password_recovery`, which still expects `development_otp` in an HTTP response and subsequently logs in by username. Both conflict with the current secure contract. The file was not changed because managed filesystem permissions rejected the edit. The test was neither skipped nor weakened. New recovery regressions exercise the actual provider adapters and recovery flow without secret disclosure.
- An attempted required update to `IMPLEMENTATION_STATUS.md` was also rejected by managed filesystem permissions. This report contains the actual follow-up results; the historical credential/default-mode observations in that older file do not describe today's environment.
- No browser automation or PostgreSQL concurrency testing was performed in this run. Frontend evidence is API tests, TypeScript and a completed production build. Live provider acceptance and physical receipt remain unverified.
