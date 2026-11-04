# Feature gap analysis and implementation plan

## Audit snapshot

- The project is a single-commit Next.js App Router prototype on `main`; the clean baseline was preserved on `feature/procurement-automation-upgrade` before edits.
- Most portal screens are selected in `components/route-view.tsx`; sample records live in `lib/data.ts`; shared visual styles are in `app/globals.css`.
- Existing working flows include the role-based demo sign-in, buyer RFQ wizard, sample supplier matching, supplier quotation form, buyer quote list, verification queue, notifications, membership screens, and role dashboards.
- No backend API, database, spreadsheet parser, messaging provider, billing service, or production authentication service is configured.

## Feature matrix

| Area | Baseline status | Upgrade in this branch | Backend dependency |
|---|---|---|---|
| RFQ wizard | Implemented; material master lookup missing | Keep the existing wizard and add material code/name lookup with standard specification and unit fill | Save/publish and persistent attachments |
| Material Master | Missing | Add bilingual representative records, category filter and admin reference table | Large searchable dataset, archive/edit persistence, Excel import |
| Supplier import | Missing | Add column mapping and preview flow; never create logins or verification decisions | Workbook parsing, duplicate checks against database, durable import jobs and reports |
| Supplier Price Book | Missing | Add private rate table, validity states, local edit/revision history and explicit private-pricing labels | Secure per-company storage and immutable quote snapshots |
| Supplier quotation drafts | Partially implemented as manual form | Keep current quote form and explicit supplier submission action | Price matching, durable drafts, notifications and quote revision persistence |
| Matching | Implemented with illustrative percentage scores | Preserve existing matching page pending separate review | Rule evaluation against current verified capabilities and invitation delivery |
| Buyer comparison | Partial; quote list exists | Add side-by-side line and terms comparison, shortlist and explicit outcome controls | Persist decisions and downloadable report generation |
| Admin operations | Partial; verification, matching, notifications and reports exist | Add Automation Center with sample-only health and exception links | Job runner, delivery logs, audit records and monitoring |
| Notifications | In-app demo list exists | Preserve existing center | Per-channel preferences and actual delivery providers |
| Reporting | Admin demo exists | Add buyer and supplier demo summaries; label sample metrics | Role-scoped aggregate queries |
| Authentication and membership | Demo role access and membership records exist | Preserve current implementation | OTP/recovery/session and configurable commercial terms |

## Isolated changes

- Add `lib/materials.ts` for representative internal material records.
- Add `components/procurement-tools.tsx` for Material Master, import previews, private price book, automation, comparison, and role reports.
- Add role navigation and dispatch links in `components/route-view.tsx`; extend the existing RFQ item selector to use the material records.
- Add a small group of portal styles in `app/globals.css`.

All sample values and simulated states are labeled as such. No API, authentication, environment, branding, typography, or dependency changes are planned.
