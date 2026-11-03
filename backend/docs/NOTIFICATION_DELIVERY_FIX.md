# OTP SMS and Hostinger email delivery repair

## Confirmed audit findings

- The workspace loads live Android Gateway mode, enabled=true, the official messages endpoint, both gateway credentials, Hostinger smtp.hostinger.com:465 with TLS, and SMTP credentials. No credential values were printed or changed.
- The original SMS normalizer rejects the Pakistani 03 + nine-digit mobile shape shown in signup. This failure was reproduced locally before provider IO. The worker delivery adapter now converts that shape to +92, keeps valid international numbers and Saudi mobile normalization, and supports 00 international prefixes.
- Original worker polling stopped at Sent; the adapter requests delivery reports and continues status lookup until confirmation or the configured bound. HTTP 202 is saved as Pending, never Delivered.
- SMTP recipient-refusal dictionaries were ignored by the original adapter. The corrected adapter treats refusal/authentication errors accurately and does not call SMTP acceptance final delivery.
- Original SMTP retry handling could resend after an ambiguous DATA or worker restart. The corrected worker commits a sending marker before IO and requires manual reconciliation for ambiguous SMTP outcomes. SMS reserves a deterministic message ID and GETs its state before retrying an uncertain submission.
- Original batch leases could expire before later jobs began processing. The corrected worker claims and leases each job as it begins, preserving the initial batch snapshot.
- Existing signup conflicts returned one generic message. They now specifically identify duplicate phone, username or email, including the concurrent-insert failure path. Existing records are not overwritten or merged. The frontend offers sign-in and original-number verification links.

## Files and runtime

The OS denies writes to `app/worker.py`, `app/modules/notifications/providers.py`, `app/modules/notifications/service.py` and `IMPLEMENTATION_STATUS.md`, despite their normal file attributes. Both normal writes and apply_patch were denied for the provider file. No filesystem permissions were changed. The fix therefore uses two compatible extension modules:

- `app/modules/notifications/delivery.py`: corrected SMS and SMTP operations, reusing existing configuration, sender, error and stable-ID helpers.
- `app/notification_worker.py`: corrected delivery processing while reusing the original procurement event handlers, OTP expiry checks, encryption, templates and notification service.

Stop any legacy `python -m app.worker` process before starting the documented worker in a second backend terminal:

    python -m app.notification_worker

Read-only diagnosis (no sends):

    python -m app.notification_worker --diagnose

This adapter does not rebuild authentication or RFQ logic. Queues, encryption and schema are retained; no new migration is needed. API requests still only queue delivery jobs. Existing pending jobs survive restarts. The worker does not send used/expired OTPs. Accepted SMS status checks remain possible after expiry without resubmitting the code.

`GET /notifications/deliveries` is user-scoped and now includes OTP outbox jobs as well as business notices. The UI polls current statuses: Queued, Sending, Accepted by provider, Sent, Delivered, Failed, Skipped and Outcome uncertain. `POST /notifications/admin/outbox/{id}/retry` requires admin authorization, only accepts failed/uncertain jobs, requires explicit not-accepted confirmation for uncertain outcomes, and refuses terminal provider failures. Expired OTPs still cannot be resent. Queued ciphertext never appears in these responses.

## Provider contracts

SMS: https://docs.sms-gate.app/features/sending-messages/ and https://docs.sms-gate.app/features/status-tracking/ ? authenticated POST and GET message state, delivery reports, and Processed/Sent ambiguity.
SMTP: https://www.hostinger.com/support/1575756-how-to-get-email-account-configuration-details-for-hostinger-email/ ? SSL port 465 or STARTTLS port 587 with the full mailbox username.

Postman tools/CLI are unavailable in this workspace. Orbit discovery was attempted and failed with a network ConnectError. Official provider documentation was consulted, and tests run against a local HTTP simulator plus mocked SMTP. Provider requests preserve authentication, JSON, timeouts and response handling; simulator logs do not contain message bodies or credentials.

## Controlled live test

`python scripts/live_test.py` prepares only. It confirms credentials and live mode as booleans, but found no configured test recipients. No live SMS or email was sent.

After the user explicitly approves one SMS OTP and one email to specified recipients, set NOTIFICATION_TEST_PHONE and NOTIFICATION_TEST_EMAIL in the ignored .env and run:

    python scripts/live_test.py --approved

The runner creates an isolated local test database, calls the actual register/OTP APIs, processes exactly those two outbox jobs using the corrected worker, polls SMS state with GET only, verifies the code via the real OTP endpoint, and reports safe job IDs, provider response/state, SMTP acceptance and verification status. It never processes the hosted queue or supplier contacts. A durable per-recipient-pair ledger prevents accidental repeat sends. The legacy hardcoded live pytest tests are disabled in favor of this explicit-approval runner.

## Environment limits

Hosted database/outbox inspection and worker diagnosis failed with OperationalError because outbound PostgreSQL access is blocked (previously confirmed WinError 10013). Windows process inspection was denied, and no local worker log file was found. Whether a hosted worker is running and which hosted jobs are pending/failed therefore remains unconfirmed. Live provider responses and inbox/device delivery remain unverified pending approval, recipients and connectivity.

`IMPLEMENTATION_STATUS.md` could not be updated due to the OS file restriction; confirmed results are recorded here instead.

## Confirmed checks

- Full backend suite: 81 passed, 2 skipped (live sends intentionally disabled), with existing Starlette/Alembic/SQLite warnings.
- Frontend transport suite: 14 passed; TypeScript and production build passed.
- The focused delivery tests exercise a real loopback HTTP server (no SMS provider traffic), with API-generated OTP -> outbox -> corrected worker -> Basic Auth JSON POST -> 202 Pending -> GET Sent -> GET Delivered, and assert one POST only. A lost POST response reconciles by GET without duplicate submission. Expired OTPs are not submitted.
- Mock SMTP checks verify SSL context, full-mailbox authentication, sender rendering, refusal handling and safe ambiguous-outcome classification. A separate worker test verifies accepted email status, ciphertext erasure, user-scoped status visibility and no repeat send.
- Prepare-only live runner confirmed live mode and credential presence but no explicit test recipients. No live POST, SMTP send, provider message ID or SMTP acceptance can be reported yet. The latest user instruction requires explicit approval first.

Final focused delivery suite: 7 passed, including the added SMTP worker acceptance/no-repeat regression. The full-suite result above predates that additional test. Modified files include auth/router.py, config.py, notifications/router.py, the two extension modules, frontend auth/notification pages, generated OpenAPI/types, README/START_HERE, controlled live_test.py and delivery regression tests. No credentials were printed, no Git actions or deployment occurred, and no live test was authorized or sent.
