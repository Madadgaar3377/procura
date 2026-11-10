# Saudi B2B Procurement Backend — runnable development implementation

**An independently built FastAPI backend**, based on the user's Saudi RFQ specification and client MEP supplier spreadsheet structure. This is not a patch to the user's existing M00–M02 repository (which was not provided as source code).

## Local demo quickstart (Python 3.15)

Windows PowerShell:

```powershell
cd backend
py -3.15 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and replace JWT_SECRET with a long random development secret
python -m app.cli init-db
python -m app.cli create-admin --username admin --phone +966500000000
python -m app.cli seed-demo
uvicorn app.main:app --reload --port 8000
```

macOS / Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env: set JWT_SECRET to a random secret
python -m app.cli init-db
python -m app.cli create-admin --username admin --phone +966500000000
python -m app.cli seed-demo
uvicorn app.main:app --reload --port 8000
```

Documentation: <http://localhost:8000/docs>  |  OpenAPI: <http://localhost:8000/openapi.json>

In another terminal, to automatically dispatch RFQs in **development**:

```bash
python -m app.notification_worker
```

When `APP_ENV=development` (the local default), the API starts the Material Master and supplier import queue worker automatically. Keep the API process running while uploads are parsed and confirmed imports are applied. In deployments that run the API outside development mode, run the worker as a separate service:

```bash
python -m app.material_import_worker
```

It processes uploads and confirmed imports in bounded chunks and does not send notifications. The API returns a job ID after the private file is staged; preview and import progress are read from that durable job. Configure the Next.js BFF's server-only `FASTAPI_INTERNAL_URL` in `frontend/.env.local` (default `http://127.0.0.1:8000`).

`/health/live` checks the API process; `/health/ready` checks database access.

### Preferred PostgreSQL setup

Local SQLite is a **demo/testing convenience only**. For PostgreSQL, configure `DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/your_db` in `.env`, create the database, and run:

```bash
alembic upgrade head
python -m app.cli seed-demo
```

For an Aiven URL, keep the password in the ignored `.env`, include `sslmode=require` (or your chosen certificate verification mode), and run `python main.py` from `backend/`. The script makes a read-only connection and prints the PostgreSQL version. The API uses the same `DATABASE_URL`; `/health/ready` checks its connection. Apply Alembic migrations only after inspecting the target database and deciding to initialize it.

If the worker reports `relation "rfqs" does not exist`, run `python main.py` first. It also prints the database, schema, table count, `rfqs` presence, and Alembic revision. If the intended database has **zero tables** and no Alembic version table, initialize that empty database with `python -m alembic upgrade head`, then rerun `python main.py` and `python -m app.notification_worker --once`. If it already has tables, inspect the database and migration history before applying this project's migrations.

Do not run `create_all` against a production database. Use Alembic migrations. The ZIP includes a development-only Docker Compose definition for PostgreSQL/Redis, but it was **not executed or load-tested here**.

### Tests

```bash
python -m pytest -q
python -m compileall -q app alembic tests
alembic heads
```

The provided integration tests create an isolated in-memory SQLite database and mock OTP; they do **not** call live SMS, email, R2 or real Saudi CR verification.

### SMS and email delivery

The outbox worker supports SMS Gateway for Android's Public Cloud API and Hostinger SMTP. Keep `MOCK_NOTIFICATIONS=true` for local development and automated tests. In mock mode the adapters deliberately make no provider network requests. OTP and password recovery messages are queued only after the corresponding user action; the FastAPI request does not send them inline. Start the worker in a separate terminal from the `backend` directory:

```powershell
python -m app.notification_worker
```

Real delivery requires `MOCK_NOTIFICATIONS=false`, provider credentials, and a worker process. Restart both the API and worker after changing `.env`; settings are loaded when each process starts. OTP delivery targets the registered phone/email after the user requests verification. RFQ/quotation/company/deadline alerts additionally require verified destinations and explicit per-user channel opt-in. Store credentials only in environment variables or `.env`, never in source control. Starting a real worker can deliver any eligible messages already waiting in the outbox.

Hostinger Email uses `smtp.hostinger.com`: port `465` uses implicit TLS; port `587` uses STARTTLS. `SMTP_USE_TLS=true` enables the configured secure mode. Use the mailbox address as `SMTP_USERNAME` and `SMTP_FROM_EMAIL`.

## Roles and flow

1. Admin creates categories/material codes; imports supplier Excel/CSV with validation.
2. Users register, verify with mocked OTP locally and log in.
3. Buyer creates an organization; supplier creates/claims theirs.
4. Admin verifies supplier company and confirms its capability and geographic coverage.
5. Supplier privately maintains material rates and validity periods.
6. Buyer creates/publishes an RFQ with one or many material lines.
7. Worker picks the publication outbox event, filters verified suppliers by category and city, creates an invitation batch and in-app notifications.
8. Supplier accepts, drafts a quotation from valid private price-book rates, reviews/completes it, and submits.
9. Buyer compares submitted quotations; admin monitors exceptions and reporting.

## Project limitations — read before any live deployment

The backend **runs locally** as an end-to-end development MVP. It does not guarantee production readiness or zero crashes.

- Provider adapters are implemented. Local `.env` values are private and are not documented here. Mock mode remains enabled by default; no live SMS or email was sent during integration checks.
- Public Cloud delivery is not a production privacy approval for OTP content. Review the SMS provider's data handling and Saudi privacy requirements before enabling real OTP delivery.
- CR verification is manually entered by an admin, not checked with a Saudi government registry.
- Private storage is local-only in development; R2, malware scanning and document retention controls are not production-integrated.
- Redis-backed rate limiting and refresh-token replay-family revocation are implemented; MFA for admins and secure accepted-member invitations remain production work.
- The polling outbox worker uses database leases and bounded retries; production concurrency and failover still require staging evidence.
- Membership prices, plan billing and commercial contracts are deliberately absent; only three plan definitions and manual assignments exist.
- No online shopping catalog, inventory, consumer cart, checkout or mobile app.
- PostgreSQL migration, concurrency, sustained load, performance and security penetration tests must run in your real environment before launch.
- `APP_ENV=production` currently **fails startup intentionally** until required security and external provider integrations are implemented. It prevents accidentally exposing incomplete verification/notification controls.

See `docs/SECURITY_AND_GAPS.md` and `docs/API_AND_WORKFLOWS.md` for details.

## Folder layout

```text
backend/
  app/
    config.py            # Environment and safety settings
    database.py          # PostgreSQL / SQLite connection and sessions
    models.py            # Relational SQLAlchemy models
    schemas.py           # Pydantic request contracts
    security.py          # Auth and organization membership checks
    main.py              # API, middleware and routing
    worker.py            # Local outbox processor
    cli.py               # Bootstrap and sample seed
    modules/
      auth/ organizations/ taxonomy/ imports/ capabilities/
      rfqs/ matching/ pricebooks/ quotations/ notifications/
      memberships/ admin/
  alembic/versions/      # Upgrade/downgrade migrations
  tests/                 # API & workflow tests
  data/                  # Ignored runtime data
  infra/                 # Development Compose
  docs/                  # Contracts, security and workflows
  .env.example
  requirements.txt
```

## Existing repo compatibility

Your earlier architecture kit and the existing Codex-generated backend M00–M02 are separate. Do **not** copy this new project's `models.py` or migrations into that repository without a deliberate schema/data migration plan. Use it as a runnable reference implementation or choose to migrate your existing work after reviewing differences.


Delivery worker: `python -m app.notification_worker` (same RFQ processing, corrected SMS/SMTP adapter). Read-only diagnosis: `python -m app.notification_worker --diagnose`. API requests queue messages; the worker must run in a second terminal. Stop the legacy worker before starting this worker. Mock mode remains for tests. SMTP accepted does not mean inbox delivery.


### Recovery delivery repair ? 25 October 2026

Current behavior, validation and startup commands: [recovery delivery report](../docs/PROCURA_RECOVERY_DELIVERY_FIX.md). Settings load backend .env independently of shell working directory. Registration phone/email codes are separate; recovery email includes a one-time link. APIs never return OTPs. Policy defaults: RECOVERY_MINUTES=10, OTP_RESEND_SECONDS=60. Read-only queue diagnosis: python -m app.notification_worker --diagnose. Review/authorize queued recipients before starting continuous live delivery. python scripts/live_test.py is preparation only; --approved requires explicitly approved test recipients.
