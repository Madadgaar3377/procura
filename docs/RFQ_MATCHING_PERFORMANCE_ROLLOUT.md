# RFQ matching performance: verification and rollout

## What was measured locally

The benchmark uses a disposable SQLite database with synthetic suppliers and two RFQ materials. Thirty suppliers have eligible capabilities at each scale. It executes the durable `run_matching_job` path, counts SQL statements, records candidate query and write time, and leaves invitation events queued. It never runs an email or SMS provider worker. This is a functional scale check, not a PostgreSQL production latency result. No pre-change PostgreSQL baseline was available.

Run it from `backend`:

```bash
python -m scripts.benchmark_rfq_workflow --tiers 100 1000 10000 --repeats 5 --batch-size 2500
```

The checkout's untracked `backend/.env` sets `MATCHING_BATCH_SIZE=5` twice. That is a page size, not a supplier cap, but it creates repeated candidate queries and commits. The tracked `.env.example` recommends 2500. Confirm the **deployed** effective value before attributing the 79-second production wait to page size.

Five local runs per tier (28 November 2026, same process, synthetic SQLite data):

| Suppliers | Page size 5 p50 / p95 | Page size 2500 p50 / p95 | Matching SQL calls |
| ---: | ---: | ---: | ---: |
| 100 | 477 / 781 ms | 82 / 525 ms | 150–153 → 28 |
| 1,000 | 255 / 295 ms | 95 / 292 ms | 147–150 → 28 |
| 10,000 | 438 / 630 ms | 186 / 203 ms | 150–153 → 28 |

At 10,000 records with page size 2500, the five candidate-query measurements were 87–110 ms and write measurements were 40–51 ms. Process CPU during matching was 125–172 ms; process RSS after matching was 221–235 MiB (includes the seeded dataset and earlier runs, not a per-match peak). Thirty eligible suppliers and 30 invitation jobs persisted in every sample. Automatic submission remained zero because the synthetic supplier policy requires supplier approval; saved-rate drafts were created separately. The benchmark has only 30 eligible candidates at each tier and does not simulate concurrent buyers. These numbers must not be represented as production p95 or a complete end-to-end acceptance result.

## Production rollout

The last VPS output supplied by the operator showed Alembic at `zn57d4e5f6a7`; migrations `zo58e5f6a7b8`, `zp59f6a7b8c9`, and `zq60a7b8c9d0` must be applied in order. A recurring idle transaction previously blocked `rfq_invitations` DDL. Stop every database-writing service and use `scripts/diagnose_portal_migration.py` before retrying. Do not stamp a revision that has not run.

```bash
cd /var/www/procura/backend
systemctl list-units --all --type=service 'procura*' --no-pager
sudo systemctl stop procura-backend.service procura-material-import-worker.service \
  procura-worker@matching.service procura-worker@email.service \
  procura-worker@sms.service procura-worker@quotations.service \
  procura-worker@events.service
/var/www/procura/venv/bin/python -m alembic current
/var/www/procura/venv/bin/python scripts/diagnose_portal_migration.py
/var/www/procura/venv/bin/python -m alembic upgrade head
/var/www/procura/venv/bin/python -m alembic current
```

Install the updated code, dependencies, and `deploy/systemd/procura-worker@.service` first. Set `MATCHING_BATCH_SIZE=2500` in the server environment unless a measured PostgreSQL plan justifies another value. Keep `MATCHING_WORKER_CONCURRENCY` bounded (default 2), and size `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` against the database connection limit **across all processes**. Install `deploy/systemd/procura-import-worker-lane.conf` as the drop-in for the actual `procura-material-import-worker.service` so Excel imports run only in the imports lane.

```bash
sudo mkdir -p /etc/systemd/system/procura-material-import-worker.service.d
sudo cp deploy/systemd/procura-import-worker-lane.conf \
  /etc/systemd/system/procura-material-import-worker.service.d/lane.conf
sudo cp deploy/systemd/procura-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now procura-worker@matching.service
sudo systemctl start procura-material-import-worker.service procura-backend.service
sudo systemctl status procura-worker@matching.service --no-pager
```

Start the quotations, events, email, and SMS lanes according to the existing notification rollout. Starting those lanes can process real queued supplier notices; do not use them for the performance benchmark.

## Affected RFQ and safe recovery

The Windows checkout could not inspect the configured PostgreSQL database: the sandbox blocked TCP, and the approved network attempt failed server certificate validation (`certificate contains IP address with invalid length 16`). The affected RFQ's current status, job state, and migration revision therefore remain unverified here. On the VPS, run the read-only snapshot first:

```bash
cd /var/www/procura/backend
/var/www/procura/venv/bin/python -m scripts.inspect_rfq_matching 91a675a5-1b12-4d58-9fce-da135e4c3b65
```

If the RFQ is still open and its matching job is failed, missing, or stalled, the recovery command reuses a current pending job or creates one current-revision job. It retains matches and invitations; outbox dedupe keys prevent duplicate preparation notices. Run it only after the matching worker is healthy:

```bash
/var/www/procura/venv/bin/python -m scripts.requeue_rfq_matching 91a675a5-1b12-4d58-9fce-da135e4c3b65
/var/www/procura/venv/bin/python -m scripts.inspect_rfq_matching 91a675a5-1b12-4d58-9fce-da135e4c3b65
```

Confirm the job reaches `processed`, results and invitations persist, and no duplicate invitation or delivery dedupe key appears. Capture `EXPLAIN (ANALYZE, BUFFERS)` for the candidate query on the production PostgreSQL version, plus worker logs and queue age, before claiming the five-second target. Use only synthetic recipients or leave delivery workers stopped during load tests.
