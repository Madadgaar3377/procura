# RFQ email and SMS repair — 2026-11-12

Release acceptance is **pending controlled production delivery evidence**. No supplier
invitations, production RFQs, DNS changes, live schema changes or live messages were
created during implementation. Provider authentication checks were read-only.

## Behavior

Company verification, email verification, phone verification, contact names, roles
and profile completion no longer gate transactional RFQ invitations. Supplier
activity, approved enabled capabilities, explicit service coverage, requested
qualifications, RFQ deadline and declined invitations still govern eligibility.
Broad discipline membership still does not match an unrelated requested item.
Quotation, import and authentication verification policies are otherwise preserved.

For each item actually supported by the supplier, each channel resolves separately:

1. Contact assigned to the exact material, preferring the assignment's primary.
2. Contact assigned to the exact subcategory.
3. Unscoped primary supplier contact, then active owner/manager/supplier accounts.
4. Company business email or mobile.

Specific material restrictions on contact assignments are preserved by the admin
assignment endpoint. Scoped contacts do not become general recipients for other
materials. Arbitrary employees, viewer accounts, deleted and merged contacts are
excluded. Missing names and roles are allowed. Raw import text is not treated as
a parsed address or phone. ContactImportSource/ContactSupplierTrade retain source
evidence, not independently approved procurement scope; ContactCapability contains
the mapped assignments. Company business fields are treated as the company's
procurement fallback, not personal employee information.

The legacy `permission_to_notify=false` default on imported records is not an
explicit opt-out. An assignment or primary designation establishes procurement
authorization; the flag alone never authorizes unrelated employees. No existing
permission flags are changed. Destination restrictions and account notification
preferences override fallbacks. Opt-outs must be recorded explicitly using the
restriction endpoint rather than inferred from verification or import defaults.

Destinations are normalized and deduplicated within an invitation and channel.
Saudi national, +966, 966 and 00966 mobile forms normalize to E.164. Landlines,
faxes, extensions and invalid numbers are not SMS destinations. Missing channels
produce skipped jobs, including **No phone available**, never delivery failures.

## Durability and results

Publishing commits the RFQ and the publication outbox event together. Existing
bounded initial matching is retained for buyer feedback; it does not contact any
provider. The publication worker continues matching. Invitations, preparation
events and destination jobs have unique deduplication keys. Contact preparation and
delivery happen in `python -m app.notification_worker`, independently by channel.
Both missing and usable channels have persistent records; skipped rows need no user
account. Authorization, eligibility and suppression are checked again before send.

The worker commits a submission marker before provider I/O. Bounded retries use
exponential delays. Ambiguous SMTP submissions become uncertain and do not resend
automatically. SMS retains a deterministic provider ID and reconciles through GET;
an inconclusive missing submission becomes uncertain, requiring provider review.
An admin must attest that an uncertain submission was not accepted before retry.
Reviewing an uncertain SMS as not accepted clears its submission marker.

SMTP success means **Provider accepted**, not inbox delivery. Stable Message-ID,
multipart plain text/HTML, Date, configured branded From and validated Reply-To are
submitted over authenticated TLS. Message-ID uses the configured sender domain.
Production RFQ email requires a configured monitored `SMTP_REPLY_TO`; the code
cannot establish whether an address is actually monitored. The existing sender is
retained; `notifications@procumaterial.com` is not invented or provisioned.

SMS POST 202 always records Pending even if its body claims Delivered. Delivery
reports are requested and processed through bounded status polling. Pending, Sent,
Delivered and Failed remain distinct. Provider IDs and HTTP codes are retained.
Mock RFQ jobs are explicitly **Simulated**, never actual acceptance or delivery.
Permanent SMTP recipient refusals suppress the normalized email. Reviewed bounce,
complaint and opt-out evidence also creates durable destination restrictions.

The existing admin notification table displays RFQ reference, supplier, selected
contact/source, channel, masked destination, channel result, provider response/ID,
last submission attempt, reason and the existing guarded retry action. Each channel
has its own row. Raw destinations remain available only through the existing
admin-authorized outbox API; ordinary users see only their delivery summaries.

## API additions

Both additions use the existing platform `admin_user` guard; organization owners
cannot administer global restrictions or provider evidence. Cross-organization
contact assignments still return 404. OpenAPI and frontend contracts are refreshed.

`POST /api/v1/notifications/admin/restrictions`

```json
{"channel":"email","destination":"controlled@example.com","reason":"opted_out","detail":"Recorded recipient request"}
```

Channel: email/sms. Reason: opted_out/suppressed. Destination is validated and
normalized. Upserts the restriction and audits the action. No automatic expiry.

`POST /api/v1/notifications/admin/outbox/{event_id}/feedback`

```json
{"outcome":"bounced","evidence":"Reviewed permanent failure DSN from SMTP logs"}
```

Outcomes: delivered, bounced, complaint, opted_out. Evidence must contain 5–500
characters. The job must have an actual submission identity and cannot be simulated.
Delivered is allowed only for reviewed email evidence; SMS confirmation comes from
provider status. Bounces/complaints suppress the destination; opt-outs restrict it.
This is an authenticated operator reconciliation endpoint, **not an unauthenticated
provider webhook**. Spacemail automated DSN/complaint ingestion is not configured;
operators must reconcile provider reports here. SMS receipts use the provider's
[documented status lookup](https://docs.sms-gate.app/features/status-tracking/),
with `withDeliveryReport=true`; no public callback endpoint is required.

## Production audit evidence

Read-only checks on 2026-11-12:

| Check | Observed result |
| --- | --- |
| SPF | `v=spf1 include:spf.spacemail.com ~all` published |
| DKIM DNS | Public key at `spacemail._domainkey.procumaterial.com` |
| DKIM signing/alignment | Unverified until a received message's headers are inspected |
| DMARC | `_dmarc.procumaterial.com` returned DNS NXDOMAIN; no policy observed |
| MX | mx1.spacemail.com, mx2.spacemail.com |
| SMTP | Configured provider authenticated over certificate-validated TLS; no DATA sent |
| SMS gateway | Authenticated GET devices returned HTTP 200, one registered device; no POST sent |
| Reply-To | Not configured in this checkout; a real monitored mailbox is required |
| From address | Configured sender retained; mailbox provisioning/alias permission not proved by login |
| Reverse DNS | Actual outbound sending IP unavailable without a received message; submission host PTR is insufficient |
| Reputation | Not established; requires provider reputation/postmaster evidence and controlled samples |
| Bounce/complaints | Durable restrictions and operator reconciliation implemented; automatic production feed unconfigured |
| Inbox/device receipt | Not tested; controlled recipients not yet supplied |

SPF/DKIM DNS presence alone does not prove pass/alignment for an actual message.
Obtain the provider's exact DKIM selector and outbound IP from received headers,
check SPF and DKIM against the visible From domain, configure an approved DMARC
policy and reporting mailbox, then review alignment before tightening enforcement.
No DNS record, mailbox, sender identity or reputation claim was fabricated.
Primary inbox placement cannot be guaranteed.

## Deployment and controlled acceptance

Apply `za07d326b458_delivery_restrictions.py` with Alembic after reviewing the target
database's revision history. It follows `z96c2d15a347`; the repository already has a
separate `s29a5d04e781` index branch. Both heads are deliberately preserved. No
unreviewed migration merge or automatic live upgrade was performed. The isolated
migration test checks creation, destination uniqueness and downgrade.

Deploy API, frontend and `app.notification_worker` together; stop the legacy worker.
Set a real monitored SMTP_REPLY_TO and retain the correctly provisioned From sender.
Run the updated worker as a supervised service. Review existing pending invitations
before starting live dispatch because authorized imported contacts can now receive
RFQ messages. Do not drain historical jobs merely to test delivery.

The controlled runner defaults to preparation, with **zero sends**:

```powershell
python scripts/test_rfq_delivery.py
```

After explicit recipient-owner approval, one invocation can exercise one or two
mailboxes and one device against the actual providers using a synthetic RFQ:

```powershell
python scripts/test_rfq_delivery.py --approved --email <controlled-gmail-address> --email <controlled-outlook-address> --phone <controlled-mobile>
```

This creates only an isolated local SQLite RFQ, synthetic buyer and supplier
records. It never reads or processes the configured production supplier queue.
At most one email per mailbox and one SMS are submitted; absent channels are skipped.
The database/evidence ledger under `backend/data/private/rfq-delivery-tests/` persists
by target fingerprint, so reruns reconcile existing jobs rather than submit again.
The synthetic buyer uses a dependency override; this test does not prove real
production buyer authentication, deployment or production database concurrency.

Record each provider ID, SMTP acceptance, SMS receipt and actual mailbox placement
(Inbox / Promotions / Spam), timestamps and received Authentication-Results headers.
Then publish an approved real RFQ from a real buyer account in the deployed
environment and confirm exact eligible suppliers, assigned contacts, channel
results and admin retry behavior. This last live acceptance step is outstanding.

## Validation

`tests/test_rfq_contact_delivery.py` covers verification/import states, nameless
contacts, email/mobile/both/neither, invalid mobile and landline, material/category/
primary/channel fallback, unrelated employee exclusion, normalization/deduplication,
independent failures, bounded retry, invitation idempotency, late suppression and
opt-out, uncertain SMTP, reviewed bounce, admin permissions, material assignment
scope, migration, TLS/multipart formatting and HTTP 202 semantics.

The local end-to-end test uses actual TLS SMTP and HTTP sockets: publication API →
matching → durable invitation → worker preparation → separate jobs → SMTP AUTH/DATA
acceptance and SMS POST 202 → status GET Delivered. Exactly one submission per
channel is observed. Those are local synthetic providers, not Gmail, Outlook,
Spacemail inboxes or an actual handset. Live checks establish authentication only.

Final full-suite and frontend results are recorded in `IMPLEMENTATION_STATUS.md`.
Production rollout, controlled delivery, inbox placement, DKIM alignment and
reputation remain unverified; the business acceptance criteria are not yet signed off.
