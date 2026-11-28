# Supplier invitation portal and email delivery — fix report (2026-11-28)

## Root causes

| Problem | Root cause |
|---|---|
| `https://procumaterial.com/supplier-portal#token=…` showed the About page | The backend (`portal.portal_url`) generated `/supplier-portal#token=…`, but the frontend had **no `/supplier-portal` route**. The path fell into `app/[...segments]` → `RouteView` → `PublicPage`, which renders `publicCopy[slug] \|\| publicCopy.about`, so **every unknown path silently rendered About**. Middleware and nginx were not redirecting; the catch-all fallback was. The only guest page (`/rfq-invitation`) spoke to the legacy JWT endpoints, which no notification issues any more. |
| SMTP auth succeeds but submission sometimes fails with 554 | 554 at DATA is a provider *policy* answer, not an auth failure. The local configuration audit shows **`SMTP_FROM_EMAIL` ≠ `SMTP_USERNAME`** (same domain). Spacemail rejects a From address the authenticated mailbox does not own (or is not a verified alias of) with `554 5.7.1 … not owned by authenticated user`. Spam/content rejections and sending-quota rejections (which some providers send as 554) are the other common 554 classes. **The domain has no DMARC record** (`_dmarc.procumaterial.com` → NXDOMAIN), which large receivers increasingly reject or junk for bulk mail. The live 554 text from production logs was not available in this checkout, so the exact class must be read from the new stored `provider_response`. |
| Business inbox receives many "Mail Delivery Subsystem" / "Undelivered Mail Returned to Sender" notices | The envelope sender (Return-Path) was the From mailbox, so every bounce report landed in the business inbox and nothing processed them. Invalid imported supplier addresses therefore kept being retried by later RFQs. |
| "Mailbox full" treated as invalid | The old classifier matched `mailbox … full` as `recipient_rejected` → a temporary condition permanently suppressed a valid supplier address. |
| Matching re-ran during notification | `prepare()` (per invitation job) and `deliver_procurement()` → `eligible_orgs` / `current_recipient` (per email and per SMS job) called `supplier_evaluations` → `_match_batch(evaluate_only=True)`, i.e. full supplier matching for every message. |
| Duplicate emails | Only SMS had duplicate-destination protection. A reinstated invitation (new dedupe key) or two supplier records sharing one address produced a second invitation email. |
| Automatic 24 h reminders could double a manual reminder | The automatic reminder ignored the per-invitation reminder cooldown that manual reminders use. |

## What changed

### Supplier portal (frontend)
- **New dedicated route** `frontend/app/supplier-portal/page.tsx` (`noindex`, `no-referrer`, `no-store`).
- `features/supplier-portal/portal.tsx` (+ `copy.ts`, `portal.module.css`): reads `#token`, **removes it from the address bar and history immediately**, exchanges it once, then uses `/supplier-portal?invitation=<id>` (id is not secret). States: loading, invalid, expired, revoked, withdrawn, declined, session ended, RFQ closed (read-only). Shows supplier, RFQ reference/project, buyer (per visibility permission), delivery location and date, deadline, matched materials with discipline/main/subcategory, specification, required brand (mandatory flag), quantity/unit, attachments, invitation and quotation status. Actions: accept, decline (with reason), prepare quotation, save draft, verify recipient, submit, download RFQ (EN/AR), download submitted quotation, sign out. Price-book prefilled lines are flagged; blank price = item excluded (partial quotation); no zero/fake prices. EN/AR with RTL; responsive to mobile.
- `components/route-view.tsx`: unknown paths now render "Page not found" instead of About.
- `next.config.ts` + `proxy.ts`: `/supplier-portal` gets `Cache-Control: private, no-store`, never a login redirect.

### Supplier portal (backend) — `app/modules/procurement/portal_session.py`
- `POST /supplier-portal/session` exchanges the link token (SHA-256 stored, expiring, revocable, bound to supplier org + invitation + RFQ) for a **restricted session**: random secret in an HttpOnly cookie `pp_<invitation>` scoped to `/api/v1/supplier-portal`, max `SUPPLIER_PORTAL_SESSION_HOURS` and never beyond the link expiry. Structured denial codes (`invalid_link`, `link_expired`, `link_revoked`, `invitation_withdrawn`, `invitation_declined`, `supplier_unavailable`).
- `/supplier-portal/me[...]`: view, accept, decline, draft, `verification/request`, `verification/confirm`, submit, `rfq.pdf`, `quotation.pdf`, `attachments/{id}`, logout. A session authorizes only its own invitation; no account is created; no other RFQ, supplier, price book or admin API accepts it.
- **Recipient verification before binding submission**: a 6-digit code (hashed, 5 attempts, expiring) is sent through the outbox to the same email/phone the link was delivered to.
- Revoking an invitation's links also ends its portal sessions. Suspended/inactive suppliers and cancelled invitations are refused. Existing token-in-body endpoints (`/supplier-portal/open|save|approve|decline`) and the legacy `/rfq-invitation` page keep working.
- Raw link tokens, session secrets and codes are never logged or stored.

### Automatic and manual quotations
- Reuses the existing Private Price Book prefill (`quotations/automation.py`): drafts match material/brand/unit/availability, VAT/totals are computed by `invoice()`; automatic submission still happens **only** under an explicit, valid supplier authorization policy. Submission queues `quotation.submitted` → buyer in-app + email/SMS notice. Quotations keep `template_key='pending_client_approval'`; no commercial terms are invented.

### Email delivery and bounces
- `smtp_diagnostics.py`: enhanced-status-code-first classification with a stable operator `category`: `authentication_failure`, `invalid_recipient`, `permanent_rejection`, `temporary_rejection`, `rate_limit`, `spam_rejection`, `mailbox_full`. Mailbox full is soft. Sender/relay wording outside RCPT TO is never treated as a recipient problem.
- `delivery.send_email`: adds `X-Procura-Delivery-Id` (outbox id) and, for RFQ solicitations, `List-Unsubscribe`; optional `SMTP_ENVELOPE_FROM`; soft-bounce resends use a new Message-ID.
- Status vocabulary: `accepted` (provider accepted — never "delivered"), `delivered` (only from a DSN/webhook/SMS report), `hard_bounced`, `soft_bounced`, `suppressed`, plus the final SMTP category on failed jobs.
- `bounces.py`: RFC 3464 DSN parser (+ plain-text fallback), correlation by `X-Procura-Delivery-Id` and Message-ID, recipient must match the job, idempotent per report (`email_delivery_events`). Hard bounce with confirmed invalid recipient → address marked `invalid_recipient`, future automatic sends skipped, matching contact channels flagged `needs_review`; **supplier records are never deleted**. Provider-level bounces (reputation/policy/quota) never suppress. Soft bounce → exponential backoff (`EMAIL_SOFT_BOUNCE_BASE_DELAY_SECONDS × 2ⁿ`), at most `EMAIL_SOFT_BOUNCE_MAX_RETRIES`, then failed.
- `app/bounce_worker.py` (+ `deploy/systemd/procura-bounce-worker.service`): IMAPS poller for a dedicated return-path mailbox; processed reports moved to `BOUNCE_IMAP_PROCESSED_FOLDER`; non-reports left unread.
- `POST /api/v1/notifications/webhooks/email`: provider-neutral signed webhook (HMAC-SHA256 of `"<timestamp>.<body>"`, 5-minute window).

### Notification queue (`notification_worker.py`)
- Eligibility at notification time reads the **persisted final matching result** (`persisted_eligibility`): current `eligible`/`partial` match, active invitation, active non-suspended supplier. Recipient routing uses the invitation's persisted scope and new `match_evidence`. Matching is no longer executed by notification jobs (test asserts zero `_match_batch` calls).
- Provider sending limits counted in the database (shared by all lane processes/hosts); over budget jobs stay queued without consuming attempts. OTP/password-reset/verification codes bypass the bulk budget.
- Configurable batch size; per-organization fair batching (`OUTBOX_ORG_BATCH_SHARE`).
- Duplicate prevention per RFQ + notification type + destination (`notice_key`) for email and SMS.
- Existing behaviour kept: lane separation (matching/email/sms/quotations/events/imports), bounded retries with backoff, permanent failure, lease recovery after restart, `submitting` email → `uncertain` (never blindly resent), provider-level pause.
- Closed/cancelled/expired RFQs and unqualified suppliers are not notified.

### Admin dashboard (`/app/admin/notifications`)
- `features/notifications/admin-dashboard.tsx` + `admin_deliveries.py`: totals for total, queued, processing, accepted by provider, confirmed delivered, failed, hard/soft bounced, suppressed, pending SMS, retry scheduled, uncertain; filters by RFQ, supplier, channel, status, date; failure category and provider response; per-job delivery history (attempts, provider acceptance, DSN/webhook events, admin actions); retry of eligible failures (blocked while the address is suppressed or the RFQ is closed); revalidate a suppressed address; correct a bounced supplier email (updates only that supplier's records) and resend.

### Buyer matched-suppliers page
- Each invitation now shows **Invitation recorded**, **Email status**, **SMS status** (with safe reason codes), **Supplier response** and **Quotation status** separately. Manual reminders: unchanged RFQ-locked endpoint (no duplicate on double-click, 409 for closed/cancelled/expired); the automatic reminder now honours the same per-supplier cooldown.

## Email configuration audit (read-only; no credentials changed)

| Item | Finding |
|---|---|
| SMTP host / port / TLS | `mail.spacemail.com:465`, implicit SSL/TLS — correct. |
| Authentication | Credentials configured; production logs report successful login. Not re-tested from this checkout (would contact the live provider). Run `--check-providers` on the server. |
| Authorized From | **From differs from SMTP_USERNAME** (same domain). It must be the authenticated mailbox or a verified alias; otherwise expect `554 5.7.1 … not owned`. Verify in Spacemail and align `SMTP_FROM_EMAIL` if it is not an alias. |
| Envelope sender | Currently the From mailbox → bounces reach the business inbox. Optional `SMTP_ENVELOPE_FROM` provided; only set it if Spacemail accepts that address for the authenticated account. |
| SPF | `v=spf1 include:spf.spacemail.com ~all` present (softfail). |
| DKIM | `spacemail._domainkey` present. |
| DMARC | **Missing** (`_dmarc.procumaterial.com` NXDOMAIN). Add e.g. `v=DMARC1; p=none; rua=mailto:dmarc@procumaterial.com` first, then move to `quarantine` after reviewing reports. |
| Sending limits | Not published per mailbox in this repo. Defaults are conservative (20/min, 250/h); set `EMAIL_SEND_LIMIT_*` to Spacemail's limits for your plan. |
| Domain reputation | Not measurable from this checkout; check Google Postmaster Tools and Microsoft SNDS. |
| Bounce handling | Spacemail offers no delivery webhooks; use the dedicated return-path mailbox + `app.bounce_worker`. |

**Recommendation (no migration performed):** Spacemail is mailbox hosting, not a transactional/bulk sending service. RFQ invitations to hundreds of imported suppliers per RFQ will hit mailbox sending limits and reputation filters. For that volume use a transactional provider with dedicated bounce/complaint webhooks and DKIM on `procumaterial.com` — e.g. **Amazon SES** (low cost at volume; bounce/complaint notifications via SNS) or **Postmark** (focused on transactional mail, built-in bounce webhooks). Confirm regional and data-residency requirements before choosing. Both work through the existing SMTP code path (change host/port/credentials only) and their bounce events can be forwarded to the signed webhook. Keep Spacemail for the human business inbox.

## Migration

Additive Alembic revision **`zo58e5f6a7b8`** (after `zn57d4e5f6a7`): `rfq_invitations.match_evidence`, `outbox_events.submitted_at` + index `(event, submitted_at)`, bounce columns on `notification_delivery_restrictions` (`bounce_count`, `last_event_at`, `cleared_at`, `cleared_by_user_id`), new tables `email_delivery_events` and `supplier_portal_sessions`. No row is changed or deleted. Existing invitations without `match_evidence` route as before (evidence-less fallback); existing suppressions keep blocking (`cleared_at` NULL). Apply before starting the new API/workers.

## Deployment

```bash
# 0. Backup first (adjust to your DB settings)
pg_dump --format=custom --file=/var/backups/procura-$(date +%F-%H%M).dump "$DATABASE_URL"

# 1. Code
cd /var/www/procura && git pull

# 2. Maintenance window: identify and stop the API and every database worker
# before DDL. Remove unit names below that are not installed on this VPS.
systemctl list-units --all --type=service 'procura*'
sudo systemctl stop procura-backend.service procura-material-import-worker.service \
  procura-import-worker.service \
  procura-worker@matching.service procura-worker@email.service \
  procura-worker@sms.service procura-worker@quotations.service \
  procura-worker@events.service procura-bounce-worker.service

# 3. Backend: confirm no transaction holds rfq_invitations before upgrading.
cd backend
/var/www/procura/venv/bin/pip install -r requirements.txt
/var/www/procura/venv/bin/python scripts/diagnose_portal_migration.py
/var/www/procura/venv/bin/python -m alembic upgrade head      # zo58e5f6a7b8
/var/www/procura/venv/bin/python -m alembic current

# 4. Configuration (.env): review the new optional keys in .env.example.
#    Set EMAIL_SEND_LIMIT_PER_MINUTE / _PER_HOUR to the provider's real limits.
#    Make sure SMTP_REPLY_TO is set in production.

# 5. Frontend (NEXT_PUBLIC_API_URL must be your deployed API URL)
cd ../frontend && npm ci && npm run build

# 6. Start the API and only the worker units that were running before maintenance.
sudo systemctl start procura-backend.service procura-frontend.service
# Start each installed worker unit from step 2 after the migration succeeds.

# 7. Nginx: confirm no rewrite sends /supplier-portal elsewhere; add the
#    `location = /supplier-portal` block from docs/nginx-procumaterial.conf.example
sudo nginx -t && sudo systemctl reload nginx

# 8. Verify (read-only)
curl -sI https://procumaterial.com/supplier-portal | grep -i cache-control   # private, no-store
cd /var/www/procura/backend
/var/www/procura/venv/bin/python scripts/diagnose_delivery.py --check-providers --check-dns
/var/www/procura/venv/bin/python scripts/diagnose_delivery.py --recent-email-failures 20

# 9. Optional, after creating a dedicated bounce mailbox and confirming Spacemail accepts it
#    as envelope sender: set SMTP_ENVELOPE_FROM and BOUNCE_IMAP_*, then
sudo cp deploy/systemd/procura-bounce-worker.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now procura-bounce-worker
/var/www/procura/venv/bin/python -m app.bounce_worker --diagnose
```

The service names above follow `deploy/systemd`; adjust them to the units on your server.
