# RFQ matching incident recovery

## Findings

Publication already committed before its fallback, but the fallback reused the
request session, locked the RFQ row, and ran supplier discovery with a 500-org
page and optional Gemini call while that transaction stayed open. The outbox
worker repeated the full scan in one transaction. That can block concurrent RFQ
updates until PostgreSQL `lock_timeout` (55P03) or
`idle_in_transaction_session_timeout`, while a failed fallback is logged and
the API can still expose zero matches. The material import worker did not poll
RFQ outbox jobs. The existing `rfq_matches` and `rfq_invitations` unique
constraints and contact capability assignments already provide the right
identity model; they must be preserved.

The current production blocking PID and PostgreSQL query plans could not be
verified locally: the configured server's certificate fails TLS verification
(`certificate contains IP address with invalid length 16`). Do not weaken TLS
verification to bypass this. Run the diagnostic from the VPS using its valid
connection configuration before interpreting live blockers or plans.

## Deployment on the documented VPS layout

Use the repository's documented `/var/www/procura/backend` and
`procura-backend.service` names. Check them against the actual VPS before
executing. Deploy the reviewed code, then run:

```bash
cd /var/www/procura/backend
/var/www/procura/venv/bin/python3 -m alembic current
/var/www/procura/venv/bin/python3 -m alembic upgrade head
/var/www/procura/venv/bin/python3 -m scripts.diagnose_rfq_locks 68ae4317-9284-4a25-8579-6bb51a25f4ec
sudo systemctl restart procura-backend.service
sudo /var/www/procura/venv/bin/python3 scripts/install_import_worker.py --backend-dir /var/www/procura/backend --python /var/www/procura/venv/bin/python3 --api-service procura-backend.service --activate
sudo systemctl status procura-backend.service procura-import-worker.service --no-pager
/var/www/procura/venv/bin/python3 -m scripts.requeue_rfq_matching 68ae4317-9284-4a25-8579-6bb51a25f4ec
journalctl -u procura-import-worker.service -n 100 --no-pager
```

The migration `zf49b612a007` adds a PostgreSQL partial expression index for
RFQ outbox status lookup. Earlier migrations already supply unique
`(rfq_id, supplier_org_id)` match and invitation constraints, capability
eligibility/category indexes, and contact assignment indexes. Confirm the
actual production plan using `EXPLAIN (ANALYZE, BUFFERS)` on a read-only
representative candidate query before adding further indexes.

The recovery command keeps existing matches/invitations. It leaves an active
job alone, retries a prior recovery job, or inserts one deduplicated recovery
job. It never invokes a provider. Check `GET
/api/v1/matching/rfqs/68ae4317-9284-4a25-8579-6bb51a25f4ec/results`
with an authorized buyer/admin session until `matching_status` is
`completed`; inspect `failed` via the admin diagnostics route. Keep the worker
running across restarts. The result's zero count is conclusive only after
completion.
