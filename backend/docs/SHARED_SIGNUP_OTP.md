# Shared signup code

`POST /api/v1/auth/otp/request?phone=...` now generates one six-digit signup code
and queues that same code to SMS and, when an unverified registered email exists,
email. Both challenges expire five minutes after issuance. The response remains
generic and never exposes the code.

`POST /api/v1/auth/otp/verify` accepts the signup code from either message. Its
existing phone-verification behavior remains unchanged; it does not also mark
email verified. The existing email-confirmation action remains separate.
Because the code is available through email, signup completion no longer proves
that the recipient received an SMS. This is an intentional consequence of the
requested fallback behavior, not independent verification of both contacts.

Purpose/recipient-bound hashes, encrypted outbox secrets, resend cooldowns,
attempt limits, single-use verification, and resend invalidation are retained.
Password recovery and standalone email verification are unchanged. No schema,
provider configuration, endpoint shape, or credential changes are required.

Previously issued messages still contain different codes. After restarting the
backend, request a fresh code and use the latest message; old requests are not
retroactively changed.

Validation: targeted signup/recovery/dispatch tests **27 passed**; full backend
suite **302 passed, 6 skipped, 18 warnings** using disposable SQLite and
mocked/loopback providers. Frontend TypeScript and Ruff F checks passed. Live
SMS/SMTP delivery was not tested; Postman CLI was unavailable. Full results are
in `.test-runtime/shared-signup-full-results.txt`.
