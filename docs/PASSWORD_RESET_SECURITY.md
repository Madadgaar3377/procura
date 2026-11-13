# Single-use password recovery

## Scope and source findings

The existing `backend/app/modules/auth/router.py` already attempted to consume
`EmailOTP.used` and revoke refresh sessions on reset. This checkout did not simply
omit every consumption check. The reported deployed replay must also be checked
against its deployed revision and migration state. This patch makes the final
single-use decision explicit in PostgreSQL and tests each credential path.

| Surface | Audit result and resulting behavior |
| --- | --- |
| `POST /auth/password/forgot` | Existing records were associated with the current recovery contact string. A destination change could leave other records outstanding. Reset records now carry immutable `user_id` ownership and the credential stamp at issuance. Issuing a new request revokes every outstanding recovery row for that user, across email and SMS. |
| `POST /auth/password/verify` | Successful verification already changed the purpose, but locked ORM reads could retain preloaded values. All recovery locking reads now refresh existing objects. Only one verification may transition the original OTP to `password_verified`, recording `verified_at`; its purpose-bound JWT expires within five minutes and still requires the original database row. |
| `POST /auth/password/reset` | Previously the final consume decision relied on ORM state, with inconsistent refresh behavior between branches. An atomic conditional UPDATE now claims exactly one unconsumed, unrevoked, unexpired, correctly owned credential before updating the password in the same transaction. Affected row count must be exactly one. |
| Email link | Previously derived from the OTP using a server HMAC. New links use independent 256-bit cryptographic randomness; only SHA-256 verifiers are stored in recovery rows. Account ownership, destination, credential stamp, expiry and consumption/revocation state are mandatory. |
| SMS/email OTP | Both existing contact delivery paths remain supported. Six-digit codes use a purpose/account-bound keyed HMAC, a maximum of five incorrect guesses and a lifetime capped at 15 minutes. Login/registration/email-verification purposes cannot authorize recovery. The identifier+code API cannot reissue a historical recovery code for that account, including across destination changes; bounded retries fail closed on repeated RNG collisions. |
| `POST /auth/password/change` | Also revokes reset records by user ID, including credentials stored against previous contact strings. |
| Login and refresh | Existing user-first locks are preserved. Reset, login and refresh serialize on the user, so a refresh/session creation cannot race past a committed reset. |
| Access/session checks | Existing `credential_stamp` and refresh-family checks in `app/security.py` immediately reject old access tokens on subsequent protected requests after the password changes. Already executing requests are not retroactively cancelled. |
| Logout and active sessions | Existing refresh-family revocation, listing and session deletion behavior retained; normal login, registration, contact verification, refresh, listing and logout are regression tested. |
| Notifications | Both `app.worker` and `app.notification_worker` use the hardened recovery delivery implementation. Revoked/expired/consumed or unbound legacy recovery jobs are skipped. Password-changed notices are queued transactionally to verified contacts and contain no password. |

The existing `email_otps` table is extended instead of introducing a second
credential store. It already persists `id`, `created_at`, `expires_at`, `purpose`,
`code_hash`, `attempts`, and `used`. Revision `zb18e437c569` adds nullable `user_id`
(foreign key with account-delete cascade), `credential_hash`, `verified_at`,
`consumed_at` and `revoked_at`, plus account and partial link-verifier indexes.
Ordinary verification rows remain compatible with NULL recovery fields.

The temporary JWT is bound to the original OTP row through its `otp` claim. A link
has its own durable row belonging to the same user and credential version. Final
reset consumes the chosen row and revokes all outstanding siblings, including
every OTP-derived temporary authorization. These decisions survive restarts and
do not depend on process-local locks or browser state.

## Transaction and concurrency

Every recovery path locks the user first, then the selected credential with
`SELECT ... FOR UPDATE`, refreshing any existing SQLAlchemy identity-map entries.
The token-to-user lookup before locking is advisory only; the credential is read
and validated again after acquiring the user lock. Final reset:

1. Validates ownership, purpose, verification state where required, expiry,
   attempts, trusted destination and credential version.
2. Claims the credential using conditional UPDATE and checks one affected row.
3. Hashes and assigns the password using the existing Argon2 system.
4. Revokes other reset credentials and every refresh session for the user.
5. Adds the audit event and password-changed notification outbox entries.
6. Commits once. Request session cleanup rolls back on any exception.

PostgreSQL row locks serialize competing transactions; the conditional update
also prevents a stale ORM snapshot from authorizing a second consumption. See
[PostgreSQL row-lock behavior](https://www.postgresql.org/docs/current/explicit-locking.html).
SQLite tests cover functional behavior; concurrency acceptance uses PostgreSQL.

## Limits and credential handling

The existing Redis Lua counter remains the shared production limiter. Production
fails closed with HTTP 503 if Redis is unavailable. The process-local fallback is
development/test only. No new rate-limit dependency or configuration was added.

| Operation | Existing/enforced quota |
| --- | --- |
| Forgot/generation | 10 requests/IP/15 minutes; 3/recipient/15 minutes; configured resend cooldown; 3/account/15 minutes and account cooldown shared across contact aliases |
| OTP verification, link validation and final reset | Shared 20/IP/15 minutes and 10/account/15 minutes; token-based submissions now also consume the account quota |
| Incorrect OTP guesses | Five durable failed attempts; expiration and quota apply in addition |

Unknown accounts receive the same forgot-password response as eligible accounts.
Invalid/used/expired link and temporary-token responses are generic. No account
details are returned by link validation. The added link-check request body for
the existing `POST /auth/password/verify` is `{"reset_token":"<64 hex chars>"}`;
it validates and returns the same token without consuming or minting authority.
The existing identifier+code request and final-reset contracts are preserved.
OpenAPI and frontend generated contracts are updated.

Recovery rows contain only verifiers. The existing transactional delivery outbox
necessarily holds an **encrypted**, short-lived `{code, link_token}` envelope
until delivery, then clears it. Neither secret appears in outbox JSON metadata,
audit metadata, logs, notification IDs or URL query parameters. Email links put
the credential in the fragment, which the frontend removes immediately. Keep
`JWT_SECRET` and `FIELD_ENCRYPTION_KEY` outside the database, strong, and stable
across API/worker processes. A database-only exposure does not reveal OTPs or
delivery envelopes without these keys.

The Next.js reset page validates links against server state when opened, shows
“This password reset link has expired or has already been used. Request a new
link.” with a request-new-link action, and clears its token, password, form,
pending recovery identifier, resend state and API cache on success before
redirecting to login with a success toast. Final submission always validates
again on the backend.

## Verification

Local results (2026-11-13): full backend suite **195 passed, 5 skipped**;
focused PostgreSQL recovery suite **39 passed**; frontend typecheck passed.
Frontend tests: **73 passed, 1 failed** because the unrelated sitemap integration
test requires an HTTP server on port 3000 and none was running. No hosted
database or real notification recipient was used. PostgreSQL/SQLite migration
up/down and preservation of accounts/revocation were exercised locally.

`backend/tests/test_password_reset_security.py` covers acceptance cases A–Q:

| Cases | Tests |
| --- | --- |
| A–D, M | OTP, link and OTP-derived token succeed once, then repeated requests fail across fresh sessions; all sibling credentials fail too |
| E–H | Expired OTP/link/temporary authorization; JWT expiration; incorrect guesses and durable five-attempt lockout |
| I | Two simultaneous PostgreSQL requests for each credential type and mixed OTP/link yield exactly `[200, 400]`; simultaneous OTP verification mints one authorization |
| J–L | Newer requests supersede older credentials; email/phone alias and OTP/link combinations; recovery-destination changes |
| N | Registration and email-verification OTPs and ordinary access tokens cannot authorize reset; reset OTP cannot verify registration |
| O–P | Old access and refresh sessions rejected; new password login works; registration, contact verification, refresh rotation, session listing, logout and password change preserved |
| Q | Injected commit failures after flushing SQL roll back password, consumption, sibling/session revocations and notices, leaving the original reset usable |
| Additional | Separate Python processes compete against PostgreSQL, then a third fresh interpreter rejects replay; stale preloaded ORM objects; account binding; historical OTP collision; shared account quotas across IPs; IP limits; both delivery workers; PostgreSQL/SQLite migration upgrade/downgrade |

Run the ordinary suite with a disposable application database, never a shared
database; some pre-existing tests use the configured engine. Example PowerShell:

```powershell
cd backend
$env:APP_ENV='test'
$env:DATABASE_URL='sqlite:///:memory:'
python -m pytest -q -p no:cacheprovider
```

For PostgreSQL coverage, additionally set `RESET_TEST_DATABASE_URL` to a disposable
loopback PostgreSQL database. The test role needs CREATE DATABASE. The fixture
creates a random `reset_test_<uuid>` database, migrates both existing heads using
Alembic, uses dummy accounts and encrypted outbox entries, and drops that database
after testing. It refuses remote database hosts. No real SMTP/SMS is invoked.
The process test exercises separate interpreters and pools, not a deployed
multi-Uvicorn/load-balancer topology. Live Redis availability and actual notice
delivery remain deployment checks.

## Deployment

No hosted database was connected to or modified for this patch. Local disposable
PostgreSQL migrations and schema behavior are validated. Preserve existing
production-readiness gates; this patch does not authorize bypassing them.

1. Identify the deployed commit, API/worker entry points and target database.
   Back up the database and record `alembic current` and `alembic heads` there.
   The repository already has two branches; do not merge or stamp over them.
2. Rehearse on a staging clone. Review whether its branch revisions match this
   checkout and verify the expected `email_otps`/`users` schema. Stop on drift.
3. Temporarily disable recovery endpoints, drain all API workers and stop both
   possible outbox workers. Avoid mixed old/new API or worker versions, because
   reset ownership requirements and encrypted delivery-envelope format change.
4. From a target already at `za07d326b458` on the notification branch, apply
   `python -m alembic upgrade zb18e437c569` using the reviewed target environment.
   Preserve its independent `s29a5d04e781` branch. For a fresh disposable install,
   `upgrade heads` is tested; do not use that command on an unknown live schema
   without reviewing all pending migrations.
5. The migration marks **all previously issued recovery rows used/revoked**,
   including previously verified OTP authorization and links. Do not backfill
   legacy rows into usable requests. Old jobs are skipped by the new workers.
   Normal verification OTPs, accounts, passwords and existing sessions are not
   changed by deployment itself. A successful reset revokes that user's sessions.
6. Deploy the backend, both applicable workers and frontend together. Confirm
   shared PostgreSQL, shared Redis throttling, consistent secrets, delivery
   encryption configuration and frontend URL. Restart all relevant processes.
7. Using an authorized staging test account, verify first success and repeated
   rejection for OTP, link and temporary token; race two submissions through
   different Uvicorn workers; restart a worker and retry; verify old access and
   refresh rejection and receipt of password-changed notices. Confirm actual
   Redis enforcement across workers and fail-closed behavior on Redis outage.
8. Re-enable recovery after those checks. Monitor sanitized status/error counts
   and queued notice outcomes. Do not log request bodies, fragments or secrets.

## Rollback

Prefer rolling forward with the additive schema intact. If application rollback
is necessary, keep recovery endpoints disabled and workers stopped: old code does
not enforce the complete new invariant or understand the new delivery envelope.
Before an older build can process any recovery traffic, mark all recovery-purpose
rows `used=true` in a reviewed transaction. Keep recovery disabled until a safe
replacement is verified; do not restore credential rows from backup.

The migration's schema downgrade is tested. With compatible code/workers stopped,
`python -m alembic downgrade za07d326b458` removes only the new columns,
foreign key and indexes on that branch, preserving the other Alembic head and
every existing account. **Used flags remain true** for credentials revoked during
deployment/reset. Downgrade does not reverse password changes, restore old
sessions, resurrect tokens, or safely enable an older recovery implementation.
