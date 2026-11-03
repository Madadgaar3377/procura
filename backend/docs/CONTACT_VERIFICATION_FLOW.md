# Contact verification flow

Validation (2026-10-28): TypeScript passed; 8 frontend routing/API-client tests passed. Full backend run: 301 passed, 6 skipped, 1 failure from the old correct-password unverified-login 401 expectation. Updated the expectation to the intended 403 contract; subsequent identity/signup suites: 39 passed. Updating IMPLEMENTATION_STATUS.md was blocked by filesystem write permissions, so these results are recorded here. Live message delivery and browser end-to-end behavior were not tested.

Both `POST /api/v1/auth/login` and `POST /api/v1/auth/browser/login` return HTTP 403 after a correct password for an active account whose phone is unverified:

```json
{"detail":{"code":"phone_verification_required","phone":"+966511111119","email":"pending@example.test"}}
```

No access tokens or browser cookies are issued for this response. Incorrect passwords, missing accounts, and inactive accounts retain the generic HTTP 401 response.

The frontend saves these destinations in session storage and opens `/verify-mobile`. Existing `POST /auth/otp/request?phone=...` and `POST /auth/otp/verify` endpoints send/resend and confirm the six digit code. Phone confirmation returns to sign-in.

After sign-in, an existing email that is unverified redirects to `/verify-email`. This authenticated page shows the registered address and reuses `POST /auth/email/verification/request` and `POST /auth/email/verification/confirm`. Email confirmation continues to workspace selection. Accounts without an email are not required to verify a missing address.

Restored login sessions, workspace selection, and protected workspace pages also check contact status. These are frontend navigation requirements; existing backend authorization and successful token response contracts remain unchanged. Phone and email verification remain separate proofs. Delivery uses the existing configured notification services; local tests do not establish live SMS/email delivery.
