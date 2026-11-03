# Account choice, first subcategory material and schema recovery

## Changes

Registration exposes Buyer account / Supplier account buttons and login exposes direct create-account links. Home page account buttons link to the corresponding choice. The selection survives mobile verification in session storage; after successful login a user without the corresponding company is sent to the existing buyer/supplier company form. Buyer/supplier permissions remain derived from actual organization membership and company flags; there is no client-controlled admin role, verification bypass or new account-role schema.

The Add subcategory popup shows optional first material Description and standard Unit. Leaving description empty creates only a subcategory. Providing it calls admin-only POST /api/v1/taxonomy/categories/with-material with CategoryIn fields plus required parent_id, material_description (3-250 chars), and unit. Success returns {category: CategoryOut, material: MaterialOut}. Category creation and existing four-field material service share one transaction; invalid units/descriptions roll both back. Existing category endpoints and schema are unchanged.

The reported DELETE failure was caused by scanning newer ORM foreign-key columns against a database that had not received the RFQ snapshot migration. Deletion now inspects deployed foreign keys before checking references, preserving linked categories and safely supporting the previous schema. An isolated regression test reproduces a table without those new columns.

## Missing PostgreSQL migration

The running backend still needs Alembic revision a61d9f3208bc for RFQ category snapshots. This environment cannot connect to the configured PostgreSQL server (local network permission denied). No configured database was migrated or reset.

From the backend directory on the machine that can reach PostgreSQL, stop the API/workers, run:

    python scripts/upgrade_rfq_schema.py

Then restart the API/workers. This command accepts only the expected previous head b19c4e7a1d62 or the already-upgraded target; refuses unknown/partial schemas; uses a serializable transaction, PostgreSQL advisory lock and bounded statement/lock timeouts; applies only the tested RFQ migration; checks RFQ, quotation line, material and category record counts; rolls back unexpected changes. It never resets, downgrades or stamps a database. Do not use alembic stamp to hide the missing columns.

## Verification

Backend full suite: 54 passed, using disposable SQLite only. Includes atomic subcategory/material rollback, admin/outsider authorization, legacy-schema deletion regression and the targeted recovery migration with preserved RFQ and quotation references. Warnings: existing Starlette/httpx deprecation, SQLite expression-index reflection and Alembic path-separator deprecation.
Frontend typecheck and 8 API transport tests passed; production build passed. Chrome and HTTP checks passed (npm run test:browser, exit 0). The browser created a subcategory plus its first material, checked the persisted PCS unit through the API, verified supplier signup preselection and Buyer/Supplier switching, and verified login create-account links. Existing RFQ, comparison, mobile and RTL checks passed. Screenshots: frontend/.test-runtime/screenshots/admin-subcategory-with-material.png and register-supplier-account.png. The targeted migration test was rerun after adding the already-upgraded schema check and passed, including a second no-op recovery invocation.
No commit, GitHub push, deployment, database reset, or external messages.

The existing IMPLEMENTATION_STATUS.md is not writable in this workspace; actual results are recorded here.
