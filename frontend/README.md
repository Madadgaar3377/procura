> Integration update (2026-10-20): the default application now uses the real FastAPI API through a same-origin HttpOnly-cookie proxy. See [integration status](../docs/FRONTEND_BACKEND_INTEGRATION_STATUS.md) for startup, tests and incomplete scope. The historical prototype notes below apply only to explicit development demo mode (`NEXT_PUBLIC_DEMO_MODE=true`); production builds disable that mode. PostgreSQL connectivity and dependency installation are currently blocked in this workspace.

# Procura — Saudi B2B Procurement Marketplace

A responsive Next.js App Router prototype for buyer procurement teams, supplier businesses and marketplace administrators in Saudi Arabia.

## Run locally

```bash
npm install
npm run dev
```

Open the local URL printed by Next.js. For a production build:

```bash
npm run build
npm start
```

## Demo routes

- `/` — public homepage
- `/suppliers` — searchable supplier capability directory
- `/industries`, `/how-it-works`, `/solutions`, `/pricing`, `/about`, `/contact`, `/faq`, `/privacy`, `/terms`
- `/demo` — guided presentation walkthrough
- `/login`, `/register`, `/forgot-password`, `/verify-email`
- `/app/buyer/dashboard` — buyer workspace
- `/app/buyer/rfqs/new` — RFQ creation flow
- `/app/buyer/matches` — eligibility-based supplier matches
- `/app/buyer/compare` — line-by-line quotation comparison
- `/app/buyer/reports` — buyer activity summary
- `/app/supplier/dashboard` — supplier workspace
- `/app/supplier/inbox` — RFQ invitations
- `/app/supplier/price-book` — private supplier pricing
- `/app/supplier/reports` — supplier activity summary
- `/app/supplier/quote/RFQ-1026-001` — quotation form
- `/app/admin/dashboard` — operations overview
- `/app/admin/materials` — internal Material Master
- `/app/admin/material-import` and `/app/admin/supplier-import` — spreadsheet preview and validation
- `/app/admin/automation` — operational exception and integration status
- `/app/admin/verification` — company review queue
- `/app/admin/matching` — supplier matching oversight
- `/app/admin/reports` — sample operational reports

Use the sample-workspace shortcuts on the sign-in page to open buyer, supplier or admin views without credentials.

## Stack

- Next.js App Router and TypeScript
- Tailwind CSS with shadcn/ui-style reusable primitives
- Tabler icons and Recharts
- Client-side sample data and simulated actions; no production backend is connected

## Feature upgrade notes

The procurement workflow additions and backend dependencies are listed in [docs/feature-gap-analysis.md](docs/feature-gap-analysis.md). Material records, metrics, import history, matching activity and quotation preparation shown in the prototype are sample or local demo data. CSV files can be previewed locally; Excel workbook parsing, durable imports and all production integrations require backend support.

## Scope notes

This prototype focuses on company capabilities, RFQs, supplier invitations, quotations, verification and administration. It has no consumer catalog, inventory, cart or checkout. Matching checks category, technical capability, coverage and required qualifications. Email and SMS events are simulated; no external messages are sent. Membership prices and automated billing are not configured.

## Project map

```text
app/                  App Router routes, layout and shared styles
components/           Marketing, portal, workflow and UI components
components/ui/        Reusable button primitive
lib/data.ts           Sample companies, RFQs, quotations and categories
public/images/        Riyadh skyline hero image
docs/                 Client walkthrough and QA notes
```
