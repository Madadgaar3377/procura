# OTP/email delivery and six-character passwords — 2026-10-23

The current password policy is **6–128 characters**, including numeric passwords such as `123456`. This supersedes the earlier 12-character policy. Leading/trailing spaces remain invalid. Signup, reset, administrator creation/update, frontend confirmation, English/Arabic guidance and regenerated API schemas agree. Longer passwords are recommended, not required.

## Delivery fix

OTP, email-verification and recovery handlers previously only wrote outbox jobs. The normal API startup command does not start a delivery worker. New requests now schedule a background delivery attempt after committing the jobs. It uses the existing corrected notification worker and SMS/SMTP adapters, targeting only IDs created by that request. It does not drain unrelated or historical notifications.

`AUTH_DELIVERY_AUTO_DISPATCH=true` is the default. Tests disable it globally and enable it explicitly in controlled delivery tests. A dedicated worker can still process retry jobs and SMS delivery-status polling. Failed attempts remain in the durable outbox with bounded retry metadata; uncertain SMTP outcomes still require reconciliation rather than automatic resending. SMTP acceptance is not reported as inbox delivery.

Email verification resends invalidate older verification codes, preventing old queued email jobs from being delivered later. The frontend respects Retry-After on a rate-limited resend. Public responses retain enumeration-safe wording and do not expose OTP values in browser responses.

## Running the updated app

Restart the existing API from the backend directory to load the changes:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

New authentication messages receive an automatic initial delivery attempt. For retries, status polling and other procurement jobs, use the corrected worker in a separate terminal:

```powershell
python -m app.notification_worker
```

Do not run both legacy and corrected delivery workers. The legacy app/worker.py command could not be updated because that file denies writes; the automatic API dispatch explicitly uses app.notification_worker.

Read-only diagnostics, which never send a message:

```powershell
python scripts/diagnose_delivery.py --check-connections
python -m app.notification_worker --diagnose
```

Live delivery requires MOCK_NOTIFICATIONS=false, configured SMTP credentials/TLS, and SMS_GATEWAY_ENABLED=true with SMS_GATEWAY_MODE=live. The existing actual .env was not altered; it already has live SMS/SMTP settings and credential presence. The sample .env now documents the required flags while keeping mock delivery as the safe sample default. Credentials were never printed or changed.

## Actual verification

- Eight new automatic-delivery/password tests passed. They cover both channels without a manual worker, actual loopback HTTP submission to an SMS stand-in, independent channel failure, retained retry jobs, no unrelated queue draining, email verification/recovery dispatch, six-digit password creation/update/reset, and superseded queued email codes.
- Focused notification/identity regression run: 45 passed before two additional delivery cases were added; the final delivery-only run has 8 passed.
- Final full backend run: **122 passed, 2 skipped, 1 failed**. The failing existing test still expects username login. Its original file denies writes, and the prepared fixture patch from the earlier pass remains available. Authentication was not weakened to satisfy it.
- TypeScript and 17 frontend transport tests passed. The production build succeeded with isolated `.next-otp-test` output.
- Headless Chrome/HTTP procurement suite passed against that build, including actual signup using password `123456`, OTP verification page navigation, loading/error states, supplier capabilities, RFQs, quotations, comparison, mobile and RTL. All test delivery was mocked or confined to loopback.
- Focused Ruff passed for the new dispatch, diagnostic and test modules. Focused mypy passed for dispatch and identity modules. Full-project mypy still reports 78 errors in 13 files; it is not clean. The previous broader Ruff findings are not represented as fixed by this pass.
- Postman simulation skill was consulted; the Postman CLI is not installed. Verification used the existing loopback HTTP harness and SMTP test doubles, not a claimed Postman simulation run.

## Live delivery remains unverified

The read-only connectivity probe showed **PermissionError** when opening outbound TCP connections to both the configured SMS gateway and SMTP server. Database diagnostics also could not connect (OperationalError). These are restrictions in this execution environment; changing application code or showing a success toast cannot establish live delivery while those connections are blocked.

No real SMS/email was sent during tests or diagnostics, no live queue was drained, no database migration was applied, and no existing users/passwords were rewritten. Provider credentials and mailbox/device delivery have not been validated here. Run the restarted application in an environment with outbound access, then check actual delivery status; do not treat queued or SMTP-accepted as delivered.

## Changed files

- backend/app/identity.py, schemas.py, config.py
- backend/app/modules/auth/router.py and modules/admin/router.py
- backend/app/modules/notifications/auth_dispatch.py
- backend/scripts/diagnose_delivery.py
- backend/tests/conftest.py, test_auth_delivery_dispatch.py, test_identity_security.py
- backend/.env.example
- frontend/lib/validation.ts and workflow-translations.ts
- frontend/features/auth/auth-page.tsx
- frontend/tests/browser-check.mjs (six-digit signup regression)
- frontend/tsconfig.json (Next-generated isolated build includes)
- docs/openapi.json and frontend/lib/api/generated/schema.ts/routes.json

Actual test logs are in docs/otp-password-pytest-final-results.txt and docs/otp-password-mypy-results.txt. backend/IMPLEMENTATION_STATUS.md remains write-protected; this document records the results instead.
