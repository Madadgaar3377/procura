# Admin supplier details and review API

These endpoints extend the existing `/organizations` API. They reuse `organizations`, `organization_contacts`, `supplier_capabilities`, `imported_supplier_trades`, `stored_files`, `verifications`, and `audit_events`. Every new endpoint requires `admin_user` authorization.

| Method and path | Purpose |
|---|---|
| `GET /organizations/admin/suppliers?q=&status_filter=&limit=&offset=` | Supplier list. `status_filter` is `all`, `verified`, `unverified`, `incomplete`, `imported`, or `pending_verification`. Search covers company, city, CR, business email/phone, and contact email/phone. |
| `GET /organizations/admin/suppliers/{org_id}` | Company fields, all contacts, profile/verification/onboarding statuses, capabilities and materials, pending trade mappings, document links, and verification history. |
| `PATCH /organizations/admin/suppliers/{org_id}` | Update company profile fields. `null` clears optional fields; company name cannot be cleared. The update is audited and does not change verification. |
| `POST /organizations/admin/suppliers/{org_id}/contacts` | Add a contact with optional `name`, `role`, `email`, `phone`, and `fax`. Invalid contact values are retained as raw values and flagged for review. |
| `PATCH /organizations/admin/suppliers/{org_id}/contacts/{contact_id}` | Update a contact owned by the supplier. |
| `POST /organizations/admin/suppliers/{org_id}/verify` | Explicitly approve an organization. Optional JSON body: `{"reason":"..."}`. CR, email, and phone are not mandatory. A duplicate verified CR is rejected. |
| `POST /organizations/admin/suppliers/{org_id}/request-information` | Mark review pending and record the required request in `reason` (4–2000 characters). |
| `POST /organizations/admin/suppliers/{org_id}/reject` | Reject while retaining supplier, contact, and capability data. `reason` is required (4–2000 characters). |

Review decisions are saved as `Verification` rows (`reviewer_id`, `reviewer_notes`, and `reviewed_at`), with the organization `verified_status` updated independently from `profile_status`. Each profile/contact/review action creates an `AuditEvent`. Existing verification documents use the existing admin document URL route; private stored files use the established file download URL. `reviewed_at` and nullable verification CR plus contact role require Alembic revision `c3d11a9b72e4`.

The admin UI remains on the existing Suppliers page. Its list search is server-side, and the detail view renders missing values as “Not provided”.

## Validation

Local isolated SQLite verification: `python -m pytest -q -p no:cacheprovider` — 38 passed, 1 skipped. The focused supplier management tests cover admin authorization, reading multiple contacts/capabilities, incomplete supplier verification with persistence, editing and preserving invalid raw contact values, and rejection without deleting related records. Frontend `npm run typecheck` passed. The configured remote PostgreSQL database was not modified or tested because its non-production status is unconfirmed. Applying Alembic revision `c3d11a9b72e4` is required before deploying these schema-dependent endpoints.
