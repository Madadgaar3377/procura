# Backend workflow and API map

All API paths are prefixed with `/api/v1`. See `/docs` for the current OpenAPI schema.

## Access and entities

- `POST /auth/register`, `POST /auth/otp/request`, `POST /auth/otp/verify`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`.
- Authenticated email ownership flow: `POST /auth/email/verification/request`, `POST /auth/email/verification/confirm`.
- Email recovery: `POST /auth/password/forgot` and `POST /auth/password/reset`. The request response is generic; recovery is available only for an active account with a verified email.
- Authenticated delivery consent: `PATCH /auth/notification-preferences` with `sms_enabled` and/or `email_enabled`. The channel must already be verified. Transactional RFQ and company alerts are queued only for opted-in channels.
- `POST /organizations`, `GET /organizations`, `PATCH /organizations/{org_id}`, `POST /organizations/{org_id}/verification`, `POST /organizations/{org_id}/claims`.
- `POST /organizations/admin/review/verification/{id}` and claim decision, admin-only.
- `GET /directory/suppliers` lists verified companies only; imported unverified company profiles are not listed.

## Materials and supplier data

- `GET /taxonomy/categories`, `GET /taxonomy/materials?q=CAM`, `GET /taxonomy/materials/{id}`.
- Admin: `POST /taxonomy/categories`, `POST /taxonomy/materials`, `PATCH /taxonomy/materials/{id}/archive`.
- Admin: `POST /imports/suppliers?dry_run=true|false` and `/imports/materials`, multipart with XLSX or CSV.
- Imported suppliers remain unverified, unclaimed and not automatically entitled to RFQ invitations. Empty names are errors; trade/brand source data is preserved.
- Supplier: `POST /capabilities`; admin: `POST /capabilities/{id}/confirm`.
- Supplier private prices: `POST /pricebooks`, `GET /pricebooks/{org_id}`, `POST /pricebooks/{org_id}/bulk-import`. Reads and writes require a supplier owner, manager, supplier role, or platform admin.

## RFQ and supplier matching

1. Buyer calls `POST /rfqs` with buyer_org_id, title, city, deadline, items.
2. Each item has material_id, description, specifications, quantity and unit. Material references are validated and specification text is snapshotted.
3. Buyer calls `POST /rfqs/{rfq_id}/publish`; creates `rfq.published` outbox event.
4. Worker (`python -m app.worker`) leases outbox events, selects verified suppliers with confirmed capabilities and matching service city, creates invitations and in-app messages, and queues outbound messages for opted-in, verified members.
5. For manual debugging, buyer may call `POST /matching/rfqs/{id}/dispatch`; repeated calls do not create duplicate invitations.
6. Supplier opens `GET /matching/invitations`, and accepts or declines.
7. Supplier calls `POST /quotations/invitations/{id}/auto-draft` to draft from valid private rates (otherwise starts blank).
8. Supplier may `PUT /quotations/{id}/lines` and `POST /quotations/{id}/submit`.
9. Buyer views `GET /quotations/rfqs/{id}/comparison` and may `POST /rfqs/{id}/close`.

Automatic *drafts* are not automatically submitted. Exact product compliance is never inferred from a category match.

## Example request body: create CCTV RFQ

```json
{
  "buyer_org_id":"<verified buyer organization UUID>",
  "title":"Hospital CCTV procurement",
  "city":"Riyadh",
  "response_deadline":"2027-01-15T18:00:00+03:00",
  "items":[{
    "material_id":"<material UUID for CAM-5MP-IP68>",
    "description":"Outdoor fixed colour IP camera",
    "specifications":"5MP, PoE, IP68, IR > 30m",
    "unit":"No.",
    "quantity":"79"
  }]
}
```

Use an actually future deadline rather than copying this example after it expires.

## Access control rules

- Buyer private RFQs are available only to buyer organization members, invited suppliers, and admins.
- Private supplier rates are visible only to that supplier organization and privileged platform admins.
- Supplier offers are visible to buyers only after supplier submission. Other suppliers do not see competing quotations.
- Migrations and admin bootstrap are local/manual operations, not exposed as general HTTP endpoints.

## Delivery and status contracts

- `GET /db/connect/status` (full path `GET /api/v1/db/connect/status`) performs a read-only `SELECT 1` against the configured database. It returns `connected`, `status`, `message`, `error`, and `checked_at`; the HTTP response is 200 when the diagnostic runs, including when the database is disconnected. The endpoint is public like the existing readiness check and only returns a generic error class/message, never the connection URL or raw database exception.
- SMS uses `POST https://api.sms-gate.app/3rdparty/v1/messages` with Basic Authentication, a stable provider message ID, one E.164 phone number, a text message and a one-hour TTL. The worker polls `GET /3rdparty/v1/messages/{id}` until the gateway reports `Delivered`, `Sent`, `Failed` or `Cancelled`.
- SMTP uses `smtp.hostinger.com`, implicit TLS on port 465 or STARTTLS on port 587. Configuration keys are `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, and `SMTP_USE_TLS`.
- `MOCK_NOTIFICATIONS=true` is the default. In development/test, authentication routes may include a `development_otp` or `development_code`; production responses never include those values.
- `GET /notifications/deliveries` returns only the authenticated user's email/SMS delivery state. `GET /notifications/admin/outbox` is admin-only and excludes the encrypted short-lived secret.
- Failed deliveries use bounded exponential retry and a dead-letter state after the configured attempt limit. SMS retries reuse the same provider message ID and status polling stops at `DELIVERY_STATUS_MAX_POLLS`. SMTP has no standard idempotency extension; the worker uses a stable `Message-ID` and unique outbox key, but SMTP acceptance followed by a process crash can still be delivered twice.
- The migration `c2a1b7345e6f` adds delivery metadata and verified-email state without deleting existing records. It backfills one refresh-token family ID per existing session. Its partial unique indexes will fail if the target already has duplicate normalized emails or verified CR numbers; inspect those first. Do not apply it to a hosted database without the requested review and approval.

## Reporting API

- `GET /api/v1/reports/summary?workspace=buyer|supplier|admin[&org_id=...]` returns six-month RFQ, invitation and submitted quotation activity plus current counts. Buyer and supplier results are scoped through organization memberships, and an organization filter is separately verified against the caller's membership and role. Requests for the global admin workspace require the platform administrator permission. Buyer reports include submitted quotations only, never supplier drafts or private price-book rates.
- The OpenAPI snapshot is generated from the FastAPI application and the frontend's generated TypeScript contracts and proxy route allowlist are refreshed with `npm run api:generate`.

- `POST /api/v1/contact` accepts an enquiry only with explicit response consent, normalizes its email address, and stores the enquiry without sending email/SMS. Anonymous submission is limited to five per normalized email per hour; only a SHA-256 digest is used as the rate-limit identity. Admin-only `GET /api/v1/contact/admin/inquiries` and `PATCH /api/v1/contact/admin/inquiries/{id}` support review and resolution.
