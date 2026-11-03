# Simple procurement workflow - 2026-10-22

## Contract

Materials retain four business fields: main category, subcategory, description and standard unit. Codes and legacy metadata remain accepted by existing APIs. Manual creation now calls the same material creation service as confirmed Excel imports. Description identity is normalized independently of the original uploaded text; the XLSX/CSV description is retained verbatim. Workbook discipline is provenance, not an extra category level. Category-only imports now also create at most main category/subcategory and do not prepend discipline. Existing category trees and IDs are never rewritten.

Admin-only `GET /api/v1/taxonomy/categories/manage` returns existing CategoryOut fields plus `materials_count` (active materials including descendants). Requires administrator authorization. Existing category edit/archive/restore/delete contracts are unchanged; parent changes remain prohibited, used categories cannot be deleted, and archive requires active dependent materials/subcategories to be moved or archived first.

`GET /api/v1/taxonomy/materials?autocomplete=true&q=pi%2032&main_category_id=...&subcategory_id=...` performs case-insensitive description token search, ranks exact/prefix matches, and caps results to 20 even when a larger limit is requested. Uses existing category B-tree indexes, normalized description pg_trgm index and RAM/Redis versioned catalogue cache. Legacy general search limits and response shape remain compatible.

`POST /api/v1/rfqs` and draft PUT accept optional main_category_id/subcategory_id per item. Existing callers using material_id alone are supported: scope is derived and snapshotted server-side. A selected material must belong to that exact pair. A custom item can omit material_id when valid category IDs and description/unit/quantity are provided. Custom items require a subcategory if the main category has active children. Specifications remain an optional legacy field. Units are validated against existing standards while preserving legacy spelling in RFQ snapshots. Description edits persist solely on RFQItem; Material Master is never updated by RFQ APIs.

RFQ requests may optionally specify required_qualifications (default empty). These are matched against approved capability qualifications together with existing company verification, active/supplier role and service city rules. No mandatory qualification field was added.

## Eligibility and delivery

Exact matching uses RFQ item snapshots. A capability stores the selected leaf category ID; its parent is the main category. Main-category declarations do not imply every subcategory. Existing material_ids and trade_keywords metadata remain stored for compatibility but do not determine eligibility. Free-text description similarity never determines eligibility.

Coverage is recorded per RFQ item ID in existing match reasons (including repeated material references). Status is eligible for full coverage and partial otherwise. Partial suppliers follow the existing bounded invitation policy. Existing unique invitation/match constraints, RFQ row locking, outbox dedupe and the worker are reused. Reasons explicitly separate category eligibility from technical compliance.

Matching results add supplier_name, covered_items (buyer descriptions), and coverage. Supplier invitation list adds rfq_reference, title and item_descriptions from the RFQ. Existing fields remain. RFQ details, quote requested items and comparison continue to read RFQ item descriptions, never replace them with master text. Notification delivery templates and consent controls remain unchanged; suppliers open the invitation to see the customized item descriptions.

## Local migration and verification

Additive Alembic revision a61d9f3208bc follows b19c4e7a1d62. It adds nullable category references to RFQ items, allows a nullable master reference for custom requests, and adds optional qualification storage. It backfills the new IDs from existing materials without changing descriptions, quantities, IDs, RFQs or quotation links. The downgrade refuses when free-text requests would lose their material-less representation.

A temporary SQLite migration test runs the complete migration chain to the previous head, inserts an existing RFQ and quotation line, then upgrades and verifies unchanged references and text. No migration was applied to the configured PostgreSQL database. Apply this reviewed migration through the existing local migration process before running the updated backend against an existing database. Live PostgreSQL migration execution, query plans and live Redis remain unverified.

The existing workers remain required: `python -m app.worker` for matching/invitations and `python -m app.material_import_worker` for staged Excel processing. No external notifications were sent by tests.

## Results

Backend: python -m pytest -q: 52 passed (isolated SQLite); existing Starlette/httpx deprecation warning and SQLite expression-index reflection warning. Frontend: typecheck passed, API transport tests 8 passed, production webpack build passed with NEXT_BUILD_DIR=.next-procurement-simple-20261022. Chrome browser and HTTP procurement checks passed (npm run test:browser, exit 0): real login, backend-connected category/main-subcategory creation, category count table without duplicate search, keyboard description selection, automatic unit fill, edited RFQ description publication and unchanged master record, comparison, four-field material editor, 390px mobile and RTL. Screenshots are under frontend/.test-runtime/screenshots. The browser suite uses an isolated temporary SQLite file to support simultaneous browser requests; backend pytest uses isolated in-memory SQLite. No external delivery occurred. Chrome required its isolated test profile with --no-sandbox in this managed environment.

The filesystem denied updates to backend/IMPLEMENTATION_STATUS.md. This document records the actual implementation and validation results instead.
