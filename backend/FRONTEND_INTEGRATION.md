## Current integration update (2026-10-20)

The Next.js application now proxies existing bearer-token APIs through server-only handlers with HttpOnly access/refresh cookies and mutation Origin checks. No refresh token is returned to browser JavaScript. Direct API clients retain the existing JSON token contract. See [full integration status](../docs/FRONTEND_BACKEND_INTEGRATION_STATUS.md).

New response contracts are in app/response_contracts.py; export locally using `python scripts/export_openapi.py`. This changes the exporter process to an isolated in-memory test configuration and makes no configured database connection. Generated frontend types derive from that snapshot.

New compatible endpoints: PUT /rfqs/{id} (draft only), POST /rfqs/{id}/cancel, GET /quotations, GET /auth/sessions, DELETE /auth/sessions/{id}, GET /directory/suppliers/{id}, PUT /pricebooks/{id}, GET /admin/settings. Existing import uploads accept optional column_mapping JSON in the query. No new migration is required.

Session revocation prevents refresh; already-issued access JWTs retain their existing short lifetime. Rate edits increment versions and preserve quotation line snapshots. Quotation lists expose supplier-owned records and buyer-authorized submitted records only. Public supplier details expose only id/name/city/verified fields.

## Historical direct-client guide

# Next.js frontend integration guide

Frontend base URL for local backend: `http://localhost:8000/api/v1`.

- Use generated types from `http://localhost:8000/openapi.json`.
- User login is `POST /auth/login` with `identifier` and `password`.
- Registration requires development mock OTP, but production will require real SMS provider.
- Send `Authorization: Bearer <access_token>` for authenticated endpoints.
- Refresh token rotation is `POST /auth/refresh` with `refresh_token` in JSON. **For production, revise token storage/transport to secure HttpOnly cookies and CSRF protections.**
- Admin creates material categories and materials via `/taxonomy/*`.
- Buyer creates RFQ via `/rfqs`, then calls `/rfqs/{id}/publish`.
- Local worker performs automatic matching asynchronously. The buyer can retrieve `/matching/rfqs/{id}/results`.
- Supplier sees `/matching/invitations`, accepts, creates `/quotations/invitations/{id}/auto-draft`, edits lines, submits.
- Buyer sees `/quotations/rfqs/{id}/comparison`.
- Real notifications should not be simulated as delivered in a production UI.
- Arabic and English UI labels are frontend responsibilities; controlled material names support both.
- Never expose supplier private prices in buyer pages prior to supplier-submitted quotation.


### Signup verification and admin supplier contacts (2026-10-22)
Registration returns HTTP 409 with `This phone number is already registered` for an existing normalized phone. `POST /auth/otp/request?phone=...` queues the same five-minute signup code to SMS and the account email when supplied. Resend invalidates previous signup codes. `POST /auth/otp/verify` accepts that shared code once; login still requires verification. Email ownership verification remains a separate authenticated flow. The verification page displays the submitted phone and email and retains them across refresh.
`POST /organizations/admin/suppliers` additionally accepts optional `contact_name`, `phone`, and `email`, stored as an organization contact without notification consent. Admin authorization remains mandatory; creation sends no OTP and creates no user login. Existing company claiming and company verification requirements remain applicable.
