# Admin user management and password recovery — 2026-10-22

The admin sidebar now includes **Users** at `/app/admin/users`. It lists all active and inactive accounts with server-side search and pagination. Admins can add, view, edit, permanently delete users, change passwords and assign administrator access. Password hashes never appear in API responses. All operations require an authenticated administrator; ordinary organization members cannot access or change users through these endpoints.

## API contract

- `GET /api/v1/admin/users?q=&limit=25&offset=0`: returns `{items: UserOut[], total, limit, offset}`. Limit is 1–100. Search matches username, phone and email, including literal search characters.
- `GET /api/v1/admin/users/{user_id}`: returns a user or HTTP 404.
- `POST /api/v1/admin/users`: requires username, phone and a password of 12–128 characters; optional email and is_admin. Admin-created accounts bypass signup OTP. Email ownership is not automatically verified.
- `PATCH /api/v1/admin/users/{user_id}`: updates supplied username, phone, email, password, is_admin or is_active. Explicit null can clear email. Other supplied fields cannot be null. Changing contact details clears the corresponding verification and notification preference. Updates revoke refresh sessions. Duplicate identifiers return HTTP 409.
- `DELETE /api/v1/admin/users/{user_id}`: returns `{deleted:true}`; permanently removes the user, sessions, memberships, account notifications, queued deliveries, verification/recovery codes and claims. Nullable reviewer/audit references are cleared. Import batches are reassigned to the deleting admin so imported company/material data remains usable. Shared organizations and procurement history remain. Deleted accounts cannot be restored; inactive accounts can still be reactivated through PATCH. Admins cannot deactivate/delete themselves or remove their own admin role.

## Recovery flow

1. `POST /api/v1/auth/password/forgot` accepts exactly one of `identifier`, legacy `email`, or `phone`. The value must identify a verified account channel. The response remains a generic HTTP 202, whether or not an eligible active account exists.
2. For eligible accounts, queue the **same ten-minute OTP** to the verified phone and verified email, when available. A phone-only account receives SMS. Resending invalidates the previous challenge and any reset session derived from it. Rate limits apply by IP, recipient and account.
3. `POST /api/v1/auth/password/verify` accepts the same identifier and a six-digit code. Correct verification returns `{verified:true, reset_token:"..."}`. Incorrect attempts are capped at five. The reset token is restricted to password reset, expires in at most five minutes, and is bound to the challenge and current password.
4. The frontend then shows new-password and confirmation fields. `POST /api/v1/auth/password/reset` accepts `{reset_token, new_password}`. The reset consumes the challenge and revokes existing refresh sessions. Reusing the token fails. Existing direct clients may continue using `{email, code, new_password}` or the equivalent phone/identifier fields.

Unverified contact channels never receive password-recovery secrets. Recovering an account does not mark its email or phone as verified. Recovery tokens are held in component state and cannot authenticate API requests as access tokens. The frontend proxy allows anonymous recovery verification while preserving origin checks, rate-limit responses and stripping development-only OTP fields.

## Verification

- `python -m pytest -q`: **68 passed, 2 skipped**, using disposable SQLite. Existing warnings concern Starlette/httpx, SQLite expression-index reflection and Alembic configuration.
- Targeted recovery and admin tests cover ordinary-user and anonymous denials for every admin endpoint, safe deletion/history preservation, restoration, self-protection, duplicates, pagination/search, both recovery identifiers, matching encrypted OTP delivery payloads, phone-only recovery, expiry, failed-attempt exhaustion, resend invalidation, token replay and refresh revocation. Existing email recovery clients continue passing.
- OpenAPI and frontend request/response types regenerated.
- Live SMS/SMTP delivery and PostgreSQL concurrency are not established by these local tests.

`backend/IMPLEMENTATION_STATUS.md` is not writable in this workspace. Actual results for this change are recorded here.


Frontend validation: `npm.cmd run typecheck` passed; `npm.cmd run test:api` passed all 10 tests, including anonymous recovery verification with stale cookies and admin authorization forwarding.

Production frontend compilation passed using `NEXT_BUILD_DIR=.next-user-management-check npm.cmd run build` (PowerShell environment assignment). The default output directory was locked; the isolated build completed compilation, type checking, static generation and build tracing. Browser interaction and real provider delivery were not exercised.
