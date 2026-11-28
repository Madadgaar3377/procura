"""PostgreSQL RFQ matching acceptance benchmark (isolated, Alembic-migrated).

Unlike ``benchmark_rfq_workflow`` (which uses ``create_all`` and 30 eligible
suppliers), this harness:

* creates one disposable ``procura_benchmark_*`` database per tier on a
  **local** PostgreSQL server and migrates it with the real Alembic chain, so
  the production trigram/GIN/partial indexes exist;
* seeds a realistic supplier mix: exact-material, subcategory-only,
  broad-main-category, wrong-city, unapproved, fuzzy-text decoys and unrelated
  suppliers, plus a large outbox of historical notification rows;
* runs the durable ``run_matching_job`` path for sequential RFQs (p50/p95,
  SQL statement count, CPU, RSS) and for concurrent buyers through a bounded
  worker pool (queue wait + matching = end-to-end latency);
* verifies correctness: every expected supplier is filtered, no unrelated
  supplier is filtered or invited, invitations equal filtered suppliers, and a
  repeated job neither duplicates matches nor invitations;
* reports the slowest SQL statements of the largest tier with
  ``EXPLAIN (ANALYZE, BUFFERS)``.

Provider workers are never started; recipients use ``.invalid`` domains.

    BENCHMARK_PG_ADMIN_URL=postgresql+psycopg://bench@127.0.0.1:55433/postgres \
    python -m scripts.benchmark_rfq_matching_pg --label after --tiers 100 1000 10000
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

BACKEND = Path.cwd()


def _uid() -> str:
    return str(uuid.uuid4())


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = q * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def admin_engine():
    url = os.environ.get('BENCHMARK_PG_ADMIN_URL', '')
    parsed = make_url(url) if url else None
    if not parsed or parsed.get_backend_name() != 'postgresql' or parsed.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('BENCHMARK_PG_ADMIN_URL must point at a local PostgreSQL server')
    return parsed, create_engine(url, isolation_level='AUTOCOMMIT')


def fresh_database(parsed, admin, name: str) -> str:
    if not name.startswith('procura_benchmark_'):
        raise SystemExit('refusing to touch a non-benchmark database')
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = parsed.set(database=name).render_as_string(hide_password=False)
    env = {**os.environ, 'DATABASE_URL': url, 'MOCK_NOTIFICATIONS': 'true'}
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=BACKEND, env=env,
        capture_output=True, text=True,
    )
    if proc.returncode:
        print(proc.stdout[-3000:], proc.stderr[-3000:])
        raise SystemExit(f'alembic upgrade failed for {name}')
    print(f'  migrated {name} in {time.perf_counter() - t0:.1f}s')
    return url


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed(eng, n: int, rng: random.Random) -> dict:
    from app.models import (
        Capability, Category, Contact, ImportBatch, ImportedSupplierTrade, Material,
        MeasurementUnit, Member, Organization, Outbox, PriceBookItem, User,
    )
    now = datetime.now(UTC)
    t0 = time.perf_counter()
    with eng.begin() as conn:
        if not conn.execute(select(MeasurementUnit.id).where(MeasurementUnit.code == 'EA')).first():
            conn.execute(insert(MeasurementUnit.__table__), [{'id': _uid(), 'code': 'EA', 'name_en': 'Each', 'active': True}])
        # Migrations may seed reference taxonomy; matching must see only the synthetic tree.
        conn.execute(text('UPDATE categories SET active = false'))
        mains, subs = [], {}
        for m in range(8):
            mid = _uid()
            mains.append(mid)
            conn.execute(insert(Category.__table__), [{
                'id': mid, 'code': f'M{m}', 'name_en': f'Main Category {m}', 'name_ar': f'رئيسية {m}',
                'active': True, 'created_at': now,
            }])
            for s in range(6):
                sid = _uid()
                subs[(m, s)] = sid
                conn.execute(insert(Category.__table__), [{
                    'id': sid, 'code': f'S{m}{s}', 'name_en': f'Subcategory {m}-{s}', 'name_ar': f'فرعية {m}-{s}',
                    'parent_id': mid, 'active': True, 'created_at': now,
                }])
        nouns = ['valve', 'pump', 'fitting', 'panel', 'sealant', 'tile', 'beam', 'duct', 'lamp', 'gasket']
        materials = []
        for (m, s), sid in subs.items():
            for k in range(5):
                materials.append({
                    'id': _uid(), 'code': f'MAT-{m}{s}{k}', 'name_en': f'{nouns[(m + s + k) % 10].title()} type {m}{s}{k}',
                    'name_ar': 'مادة', 'category_id': sid, 'main_category_id': mains[m], 'subcategory_id': sid,
                    'unit': 'EA', 'active': True, 'version': 1, 'created_at': now,
                })
        cable = {**materials[0], 'name_en': 'Copper Cable 16mm', 'code': 'MAT-CABLE'}
        breaker = {**materials[5], 'name_en': 'Circuit Breaker 63A', 'code': 'MAT-BRK',
                   'category_id': subs[(0, 1)], 'subcategory_id': subs[(0, 1)]}
        materials[0], materials[5] = cable, breaker
        conn.execute(insert(Material.__table__), materials)

        buyers = []
        for b in range(16):
            uid_, oid = _uid(), _uid()
            conn.execute(insert(User.__table__), [{
                'id': uid_, 'username': f'bench_buyer_{b}', 'email': f'buyer{b}@bench.invalid',
                'phone': f'+96650000{b:04d}', 'phone_e164': f'+96650000{b:04d}', 'password_hash': 'x',
                'is_active': True, 'phone_verified': True, 'email_verified': True, 'created_at': now,
            }])
            conn.execute(insert(Organization.__table__), [{
                'id': oid, 'name': f'Bench Buyer {b}', 'name_normalized': f'bench buyer {b}', 'is_buyer': True,
                'is_supplier': False, 'is_active': True, 'verified_status': 'verified', 'city': 'Riyadh', 'created_at': now,
            }])
            conn.execute(insert(Member.__table__), [{'id': _uid(), 'organization_id': oid, 'user_id': uid_, 'role': 'owner', 'created_at': now}])
            buyers.append((uid_, oid))

        batch_id = _uid()
        conn.execute(insert(ImportBatch.__table__), [{
            'id': batch_id, 'kind': 'supplier_bulk', 'filename': 'bench.xlsx', 'status': 'completed', 'created_at': now,
            'initiated_by': buyers[0][0],
        }])

        share = lambda p, floor: max(floor, int(n * p))  # noqa: E731
        plan = (
            ['exact'] * share(0.05, 10) + ['sub_only'] * share(0.05, 5) + ['broad_main'] * share(0.10, 5)
            + ['wrong_city'] * share(0.03, 3) + ['unapproved'] * share(0.02, 2) + ['text_decoy'] * share(0.10, 5)
        )
        plan = (plan + ['unrelated'] * max(0, n - len(plan)))[:n]
        rng.shuffle(plan)
        orgs, caps, contacts, pbs, trades = [], [], [], [], []
        expected = {k: set() for k in ('exact', 'sub_only', 'broad_main', 'wrong_city', 'unapproved', 'text_decoy', 'unrelated')}

        def cap(org_id, cat, city='Riyadh', confirmed=True, links=()):
            caps.append({
                'id': _uid(), 'org_id': org_id, 'category_id': cat, 'city': city, 'qualifications': [],
                'material_ids': list(links), 'brands': [], 'trade_keywords': '', 'mapping_source': 'admin_import',
                'confirmed': confirmed, 'accepts_rfqs': True, 'eligibility_status': 'enabled', 'created_at': now,
            })

        for i, kind in enumerate(plan):
            oid = _uid()
            expected[kind].add(oid)
            orgs.append({
                'id': oid, 'name': f'Supplier {i:05d}', 'name_normalized': f'supplier {i:05d}', 'is_supplier': True,
                'is_buyer': False, 'is_active': True, 'verified_status': 'verified',
                'city': 'Dammam' if kind == 'wrong_city' else rng.choice(['Riyadh', 'Jeddah']), 'created_at': now,
            })
            other_key = (rng.randrange(2, 8), rng.randrange(6))
            other = subs[other_key]
            second = subs[(2 + (other_key[0] - 1) % 6, other_key[1])]  # always a different main
            if kind == 'exact':
                cap(oid, subs[(0, 0)], links=(cable['id'],))
                cap(oid, subs[(0, 1)], links=(breaker['id'],))
                if i % 3 == 0:
                    pbs.append({
                        'id': _uid(), 'org_id': oid, 'material_id': cable['id'], 'main_category_id': mains[0],
                        'subcategory_id': subs[(0, 0)], 'material_version': 1, 'unit': 'EA', 'currency': 'SAR',
                        'price_basis': 'supply', 'unit_price': Decimal('120.50'), 'vat_rate': Decimal('0.15'),
                        'availability': 'available', 'technical_confirmed': True, 'active': True,
                        'valid_until': now + timedelta(days=30), 'offered_description': 'Copper Cable 16mm',
                        'supplier_description': 'Copper Cable 16mm', 'created_at': now,
                    })
            elif kind == 'sub_only':
                cap(oid, subs[(0, 0)])
            elif kind == 'broad_main':
                cap(oid, mains[0])
            elif kind == 'wrong_city':
                cap(oid, subs[(0, 0)], city='Dammam', links=(cable['id'],))
            elif kind == 'unapproved':
                cap(oid, subs[(0, 0)], confirmed=False, links=(cable['id'],))
            elif kind == 'text_decoy':
                cap(oid, other, city=rng.choice(['Riyadh', 'Jeddah']))
                trades.append({
                    'id': _uid(), 'org_id': oid, 'import_batch_id': batch_id,
                    'raw_trade': 'Plastics', 'raw_products': 'cable ties; nylon straps; copper sheet',
                    'raw_brands': '', 'raw_type': '', 'raw_location': 'Riyadh', 'mapping_status': 'mapped', 'created_at': now,
                })
            else:
                cap(oid, other, city=rng.choice(['Riyadh', 'Jeddah', 'Dammam']))
                cap(oid, second, city='Riyadh')
                if i % 4 == 0:
                    trades.append({
                        'id': _uid(), 'org_id': oid, 'import_batch_id': batch_id,
                        'raw_trade': 'General trading', 'raw_products': f'{rng.choice(nouns)}; {rng.choice(nouns)} spares',
                        'raw_brands': '', 'raw_type': '', 'raw_location': '', 'mapping_status': 'mapped', 'created_at': now,
                    })
            contacts.append({'id': _uid(), 'org_id': oid, 'name': f'Sales {i}', 'email': f'sales{i}@bench.invalid',
                             'is_primary': True, 'created_at': now})
            contacts.append({'id': _uid(), 'org_id': oid, 'name': f'Accounts {i}', 'email': f'acct{i}@bench.invalid',
                             'is_primary': False, 'created_at': now})

        for table, rows in ((Organization, orgs), (Capability, caps), (Contact, contacts),
                            (PriceBookItem, pbs), (ImportedSupplierTrade, trades)):
            for start in range(0, len(rows), 5000):
                if rows[start:start + 5000]:
                    conn.execute(insert(table.__table__), rows[start:start + 5000])

        # Historical outbox volume: production tables carry many delivery rows.
        noise = []
        for k in range(n * 15):
            noise.append({
                'id': _uid(), 'event': 'notification.delivery', 'status': 'processed', 'attempts': 1,
                'payload': {'channel': 'email', 'destination': f'x{k}@bench.invalid',
                            'context': {'rfq_id': _uid(), 'notice_key': f'n{k}'}},
                'dedupe_key': f'noise:{k}', 'created_at': now - timedelta(days=k % 90),
            })
            if len(noise) == 10000:
                conn.execute(insert(Outbox.__table__), noise)
                noise = []
        for k in range(max(10, n // 10)):
            noise.append({
                'id': _uid(), 'event': 'rfq.published', 'status': 'processed', 'attempts': 1,
                'payload': {'rfq_id': _uid(), 'revision': 0, 'fingerprint': uuid.uuid4().hex},
                'dedupe_key': f'noise-pub:{k}', 'created_at': now - timedelta(days=k % 90),
            })
        if noise:
            conn.execute(insert(Outbox.__table__), noise)
    with eng.connect() as conn:
        conn.execution_options(isolation_level='AUTOCOMMIT').execute(text('ANALYZE'))
    return {
        'seed_s': round(time.perf_counter() - t0, 1), 'buyers': buyers, 'expected': expected,
        'mains': mains, 'subs': subs, 'cable': cable['id'], 'breaker': breaker['id'],
    }


def create_rfq(eng, world: dict, buyer_index: int, tag: str) -> tuple[str, str]:
    from app.models import RFQ, Outbox, RFQItem
    now = datetime.now(UTC)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    with Session() as db:
        _, buyer_org = world['buyers'][buyer_index % len(world['buyers'])]
        rfq = RFQ(reference=f'RFQ-BENCH-{tag}', buyer_org_id=buyer_org, title=f'Bench {tag}', city='Riyadh',
                  response_deadline=now + timedelta(days=7), status='published', published_at=now)
        db.add(rfq)
        db.flush()
        m0, s00, s01 = world['mains'][0], world['subs'][(0, 0)], world['subs'][(0, 1)]
        db.add_all([
            RFQItem(rfq_id=rfq.id, material_id=world['cable'], main_category_id=m0, subcategory_id=s00,
                    description='Copper Cable 16mm', specifications='', unit='EA', quantity=Decimal('10')),
            RFQItem(rfq_id=rfq.id, material_id=world['breaker'], main_category_id=m0, subcategory_id=s01,
                    description='Circuit Breaker 63A', specifications='', unit='EA', quantity=Decimal('5')),
            RFQItem(rfq_id=rfq.id, main_category_id=m0, subcategory_id=s00,
                    description='Armoured copper cable 4 core 25mm', specifications='XLPE/SWA/PVC', unit='EA',
                    quantity=Decimal('200')),
        ])
        job = Outbox(event='rfq.published', payload={'rfq_id': rfq.id, 'revision': 0},
                     dedupe_key=f'rfq-published:{rfq.id}')
        db.add(job)
        db.commit()
        return rfq.id, job.id


class SqlProbe:
    """Counts statements per thread and keeps the slowest statements."""

    def __init__(self, eng, rtt_ms: float = 0.0):
        self.rtt = rtt_ms / 1000.0
        self.local = threading.local()
        self.slow: list[tuple[float, str, object]] = []
        self.lock = threading.Lock()
        event.listen(eng, 'before_cursor_execute', self._before)
        event.listen(eng, 'after_cursor_execute', self._after)

    def _before(self, conn, cursor, statement, params, context, executemany):
        conn.info.setdefault('probe_t0', []).append(time.perf_counter())
        self.local.count = getattr(self.local, 'count', 0) + 1
        if self.rtt:
            # Models the network round trip to a remote managed database.
            time.sleep(self.rtt)

    def _after(self, conn, cursor, statement, params, context, executemany):
        elapsed = (time.perf_counter() - conn.info['probe_t0'].pop()) * 1000.0
        if statement.lstrip().upper().startswith('SELECT') and elapsed > 5:
            with self.lock:
                self.slow.append((elapsed, statement, params))
                self.slow.sort(key=lambda r: -r[0])
                del self.slow[12:]

    def reset_count(self):
        self.local.count = 0

    def count(self) -> int:
        return getattr(self.local, 'count', 0)


def run_job(eng, probe, job_id):
    from app.modules.matching.service import run_matching_job
    import psutil
    probe.reset_count()
    proc = psutil.Process()
    cpu0 = time.thread_time()
    t0 = time.perf_counter()
    summary = run_matching_job(eng, job_id, time_budget=None)
    return {
        'ms': (time.perf_counter() - t0) * 1000.0,
        'cpu_ms': (time.thread_time() - cpu0) * 1000.0,
        'queries': probe.count(),
        'rss_mib': proc.memory_info().rss / 1048576,
        'summary': summary or {},
    }


def verify(eng, world, rfq_id) -> dict:
    from app.models import Invitation, Outbox, RFQMatch
    with eng.connect() as conn:
        rows = conn.execute(select(RFQMatch.supplier_org_id, RFQMatch.status).where(RFQMatch.rfq_id == rfq_id)).all()
        invited = [r[0] for r in conn.execute(select(Invitation.supplier_org_id).where(Invitation.rfq_id == rfq_id)).all()]
        jobs = conn.execute(select(func.count()).select_from(Outbox).where(
            Outbox.event == 'rfq.invitation', Outbox.payload['rfq_id'].as_string() == rfq_id)).scalar()
        job_payload = conn.execute(select(Outbox.payload, Outbox.status).where(
            Outbox.event == 'rfq.published', Outbox.payload['rfq_id'].as_string() == rfq_id)).first()
    filtered = {org for org, status in rows if status in ('eligible', 'partial')}
    exp = world['expected']
    by_kind = {k: len(filtered & v) for k, v in exp.items()}
    status_by_kind = {}
    for org, status in rows:
        kind = next(k for k, v in exp.items() if org in v)
        status_by_kind.setdefault(kind, {}).setdefault(status, 0)
        status_by_kind[kind][status] += 1
    return {
        'persisted': len(rows), 'filtered': len(filtered), 'invited': len(invited),
        'duplicate_invites': len(invited) - len(set(invited)), 'invitation_jobs': jobs,
        'filtered_by_kind': by_kind, 'status_by_kind': status_by_kind,
        'unrelated_filtered': by_kind['unrelated'] + by_kind['text_decoy'],
        'missing_exact': len(exp['exact'] - filtered),
        'invited_not_filtered': len(set(invited) - filtered),
        'job_status': job_payload[1] if job_payload else None,
        'job_payload': job_payload[0] if job_payload else None,
    }


def explain(eng, probe, limit=3):
    out = []
    with eng.connect() as conn:
        raw = conn.connection.dbapi_connection
        for elapsed, statement, params in probe.slow[:limit]:
            try:
                with raw.cursor() as cur:
                    cur.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) ' + statement, params)
                    plan = '\n'.join(r[0] for r in cur.fetchall())
                raw.rollback()
            except Exception as exc:  # pragma: no cover - diagnostics only
                raw.rollback()
                plan = f'EXPLAIN failed: {type(exc).__name__}: {exc}'
            out.append({'observed_ms': round(elapsed, 1), 'sql': ' '.join(statement.split())[:400], 'plan': plan})
    return out


def run_tier(parsed, admin, label, n, args) -> dict:
    name = f'procura_benchmark_{label}_{n}'
    url = fresh_database(parsed, admin, name)
    os.environ['DATABASE_URL'] = url
    from app.config import get_settings
    if args.batch_size:
        get_settings().matching_batch_size = args.batch_size
    eng = create_engine(url, pool_size=max(4, args.concurrency + 2), max_overflow=4, pool_pre_ping=True)
    rng = random.Random(42 + n)
    world = seed(eng, n, rng)
    print(f'  seeded {n} suppliers in {world["seed_s"]}s')
    probe = SqlProbe(eng, args.rtt_ms)

    # Warm-up (taxonomy/scope caches, plan cache) is reported separately.
    rid, jid = create_rfq(eng, world, 0, f'{n}-warm')
    warm = run_job(eng, probe, jid)
    warm_check = verify(eng, world, rid)

    seq = []
    checks = []
    for r in range(args.repeats):
        rid, jid = create_rfq(eng, world, r + 1, f'{n}-seq{r}')
        seq.append(run_job(eng, probe, jid))
        checks.append(verify(eng, world, rid))

    # Idempotency: re-running a processed job is a no-op; a new job for the same
    # revision must not duplicate matches or invitations.
    from app.models import Outbox
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    with Session() as db:
        dup = Outbox(event='rfq.published', payload={'rfq_id': rid, 'revision': 0}, dedupe_key=f'dup:{rid}')
        db.add(dup)
        db.commit()
        dup_id = dup.id
    before = verify(eng, world, rid)
    dup_run = run_job(eng, probe, dup_id)
    after = verify(eng, world, rid)
    with Session() as db:
        dup_status = (db.get(Outbox, dup_id).status, db.get(Outbox, dup_id).delivery_status)

    # Concurrent buyers: C RFQs enqueued together, drained by W bounded workers.
    conc = []
    if args.concurrency:
        jobs = [create_rfq(eng, world, k, f'{n}-c{k}') for k in range(args.concurrency)]
        enqueued = time.perf_counter()
        def worker(job):
            started = time.perf_counter()
            res = run_job(eng, probe, job[1])
            res['queue_ms'] = (started - enqueued) * 1000.0
            res['e2e_ms'] = (time.perf_counter() - enqueued) * 1000.0
            return res
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            conc = list(pool.map(worker, jobs))
        makespan = (time.perf_counter() - enqueued) * 1000.0
        conc_checks = [verify(eng, world, rid_) for rid_, _ in jobs]
    else:
        makespan, conc_checks = 0.0, []

    plans = explain(eng, probe) if args.explain else []
    with eng.connect() as conn:
        db_size = conn.execute(text('select pg_size_pretty(pg_database_size(current_database()))')).scalar()
        outbox_rows = conn.execute(text('select count(*) from outbox_events')).scalar()
    eng.dispose()
    sample = checks[-1]
    times = [r['ms'] for r in seq]
    payload = sample['job_payload'] or {}
    return {
        'suppliers': n, 'batch_size': get_settings().matching_batch_size, 'rtt_ms': args.rtt_ms, 'db_size': db_size, 'outbox_rows': outbox_rows,
        'warm_ms': round(warm['ms'], 1), 'warm_check_ok': warm_check['missing_exact'] == 0,
        'seq_p50_ms': round(statistics.median(times), 1), 'seq_p95_ms': round(pct(times, 0.95), 1),
        'seq_max_ms': round(max(times), 1),
        'queries_p50': statistics.median(r['queries'] for r in seq),
        'cpu_p50_ms': round(statistics.median(r['cpu_ms'] for r in seq), 1),
        'rss_mib_max': round(max(r['rss_mib'] for r in seq), 1),
        'candidate_query_ms': payload.get('candidate_query_ms'), 'db_write_ms': payload.get('db_write_ms'),
        'persisted': sample['persisted'], 'filtered': sample['filtered'], 'invited': sample['invited'],
        'invitation_jobs': sample['invitation_jobs'], 'expected_exact': len(world['expected']['exact']),
        'missing_exact': max(c['missing_exact'] for c in checks),
        'unrelated_filtered': max(c['unrelated_filtered'] for c in checks),
        'invited_not_filtered': max(c['invited_not_filtered'] for c in checks),
        'duplicate_invites': max(c['duplicate_invites'] for c in checks + conc_checks),
        'filtered_by_kind': sample['filtered_by_kind'], 'status_by_kind': sample['status_by_kind'],
        'rejection_counts': payload.get('rejection_counts'),
        'rerun': {
            'job': dup_status, 'ms': round(dup_run['ms'], 1),
            'matches_before_after': (before['persisted'], after['persisted']),
            'invites_before_after': (before['invited'], after['invited']),
            'invitation_jobs_before_after': (before['invitation_jobs'], after['invitation_jobs']),
        },
        'concurrency': args.concurrency, 'workers': args.workers,
        'conc_match_p50_ms': round(statistics.median(r['ms'] for r in conc), 1) if conc else None,
        'conc_match_p95_ms': round(pct([r['ms'] for r in conc], 0.95), 1) if conc else None,
        'conc_e2e_p50_ms': round(statistics.median(r['e2e_ms'] for r in conc), 1) if conc else None,
        'conc_e2e_p95_ms': round(pct([r['e2e_ms'] for r in conc], 0.95), 1) if conc else None,
        'conc_makespan_ms': round(makespan, 1),
        'conc_all_processed': all(c['job_status'] == 'processed' for c in conc_checks) if conc_checks else None,
        'conc_missing_exact': max((c['missing_exact'] for c in conc_checks), default=0),
        'plans': plans,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--label', required=True, help='database name label, e.g. before / after')
    parser.add_argument('--tiers', nargs='+', type=int, default=[100, 1000, 10000])
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--concurrency', type=int, default=8, help='buyers publishing at once')
    parser.add_argument('--workers', type=int, default=2, help='matching worker concurrency')
    parser.add_argument('--batch-size', type=int)
    parser.add_argument('--explain', action='store_true')
    parser.add_argument('--rtt-ms', type=float, default=0.0, help='simulated DB round trip per statement')
    parser.add_argument('--json-out')
    args = parser.parse_args()
    if not args.label.replace('_', '').isalnum():
        raise SystemExit('label must be alphanumeric')
    os.environ['MOCK_NOTIFICATIONS'] = 'true'
    parsed, admin = admin_engine()
    results = []
    for n in args.tiers:
        print(f'=== tier {n} ({args.label}) ===')
        res = run_tier(parsed, admin, args.label, n, args)
        results.append(res)
        printable = {k: v for k, v in res.items() if k != 'plans'}
        print(json.dumps(printable, default=str, indent=1))
        for plan in res['plans']:
            print(f"--- slow SQL {plan['observed_ms']} ms: {plan['sql']}\n{plan['plan']}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, default=str, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
