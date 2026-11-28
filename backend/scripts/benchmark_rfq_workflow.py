"""Synthetic isolated RFQ matching, result, and saved-rate draft benchmark.

The default tiers are 100 / 1,000 / 10,000 suppliers. Each run has 30 eligible
suppliers, two RFQ materials, and queued invitation jobs. It measures the durable
matching runner and independent quotation draft preparation. Provider workers
are never invoked. This script does not exercise concurrent buyers or generate
PostgreSQL EXPLAIN plans. BENCHMARK_DATABASE_URL must point at an empty,
disposable procura_benchmark_* database; otherwise a temporary SQLite file is used.
"""
from __future__ import annotations

import argparse
import os
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    RFQ,
    Capability,
    Category,
    Contact,
    Invitation,
    Material,
    MeasurementUnit,
    Member,
    Organization,
    Outbox,
    PriceBookItem,
    Quotation,
    QuoteLine,
    RFQItem,
    RFQMatch,
    User,
)
from app.modules.matching.service import match_and_invite, run_matching_job
from app.modules.quotations.automation import prepare_invitation_quote
from app.modules.quotations.document import build_quotation_pdf, quotation_document


def _create_isolated_engine():
    pg_url = os.environ.get('BENCHMARK_DATABASE_URL')
    if pg_url:
        database_name = make_url(pg_url).database or ''
        if not database_name.startswith('procura_benchmark_'):
            raise RuntimeError('BENCHMARK_DATABASE_URL must name a disposable procura_benchmark_* database')
        eng = create_engine(pg_url, pool_pre_ping=True)
        if inspect(eng).get_table_names(schema='public'):
            eng.dispose()
            raise RuntimeError('Benchmark PostgreSQL database must have an empty public schema')
        Base.metadata.create_all(eng)
        return eng, None
    tmp = tempfile.NamedTemporaryFile(prefix='procura_bench_', suffix='.db', delete=False)
    tmp.close()
    eng = create_engine(f'sqlite:///{tmp.name}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(eng)
    return eng, tmp.name


def run_scale_tier(num_suppliers: int) -> dict:
    eng, tmp_path = _create_isolated_engine()
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    try:
        with Session() as db:
            db.add(MeasurementUnit(code='EA', name_en='Each', active=True))
            main_cats = []
            sub_cats = []
            for m in range(5):
                mc = Category(code=f'MAIN-{m}', name_en=f'Main Category {m}', name_ar=f'فئة رئيسية {m}', active=True)
                db.add(mc); db.flush()
                main_cats.append(mc)
                for s in range(4):
                    sc = Category(code=f'SUB-{m}-{s}', name_en=f'Subcategory {m}-{s}', name_ar=f'فئة فرعية {m}-{s}', parent_id=mc.id, active=True)
                    db.add(sc); db.flush()
                    sub_cats.append((mc, sc))

            target_main, target_sub = sub_cats[0]
            mat1 = Material(code='MAT-001', name_en='Copper Cable 16mm', name_ar='كابل نحاس 16مم', category_id=target_sub.id, main_category_id=target_main.id, subcategory_id=target_sub.id, unit='EA', active=True, version=1)
            mat2 = Material(code='MAT-002', name_en='Circuit Breaker 63A', name_ar='قاطع كهربائي 63 أمبير', category_id=target_sub.id, main_category_id=target_main.id, subcategory_id=target_sub.id, unit='EA', active=True, version=1)
            db.add_all([mat1, mat2]); db.flush()

            buyer_user = User(username='bench_buyer', email='buyer@bench.invalid', phone='+966500000001', phone_e164='+966500000001', password_hash='x', is_active=True, phone_verified=True, email_verified=True, email_notifications_enabled=True, sms_notifications_enabled=True)
            buyer_org = Organization(name='Bench Buyer Co', name_normalized='bench buyer co', is_buyer=True, is_supplier=False, is_active=True, verified_status='verified', city='Riyadh')
            db.add_all([buyer_user, buyer_org]); db.flush()
            db.add(Member(organization_id=buyer_org.id, user_id=buyer_user.id, role='owner'))

            # Bulk insert num_suppliers with multiple categories each
            # 30 eligible suppliers in target_sub + Riyadh:
            #   - 10 with COMPLETE saved prices + auto-submission enabled
            #   - 10 with PARTIAL saved prices (only mat1)
            #   - 10 with NO saved prices (manual quote)
            # Remaining suppliers spread across other subcategories/cities
            now = datetime.now(UTC)
            supplier_orgs = []
            for i in range(num_suppliers):
                org = Organization(
                    name=f'Supplier Org {i:05d}',
                    name_normalized=f'supplier org {i:05d}',
                    is_supplier=True,
                    is_buyer=False,
                    is_active=True,
                    verified_status='verified',
                    city='Riyadh' if i % 2 == 0 else 'Jeddah',
                )
                supplier_orgs.append(org)
            db.add_all(supplier_orgs)
            db.flush()

            caps = []
            contacts = []
            pricebooks = []
            auto_users = []
            for i, org in enumerate(supplier_orgs):
                if i < 30:
                    # Target eligible suppliers (2 categories each)
                    caps.append(Capability(org_id=org.id, category_id=target_sub.id, city='Riyadh', confirmed=True, accepts_rfqs=True, eligibility_status='enabled', material_ids=[mat1.id, mat2.id]))
                    caps.append(Capability(org_id=org.id, category_id=sub_cats[1][1].id, city='Riyadh', confirmed=True, accepts_rfqs=True, eligibility_status='enabled', material_ids=[]))
                    if i < 10:
                        # Complete saved prices + auto-submission authorization
                        u = User(username=f'auto_sup_{i}', email=f'auto{i}@bench.invalid', phone=f'+9665010000{i:02d}', phone_e164=f'+9665010000{i:02d}', password_hash='x', is_active=True, phone_verified=True, email_verified=True, email_notifications_enabled=True, sms_notifications_enabled=True)
                        auto_users.append((u, org))
                        pricebooks.append(PriceBookItem(org_id=org.id, material_id=mat1.id, main_category_id=target_main.id, subcategory_id=target_sub.id, material_version=1, unit='EA', currency='SAR', price_basis='supply', unit_price=Decimal('120.50'), vat_rate=Decimal('0.15'), availability='available', technical_confirmed=True, active=True, valid_until=now + timedelta(days=30), offered_description=mat1.name_en, supplier_description=mat1.name_en))
                        pricebooks.append(PriceBookItem(org_id=org.id, material_id=mat2.id, main_category_id=target_main.id, subcategory_id=target_sub.id, material_version=1, unit='EA', currency='SAR', price_basis='supply', unit_price=Decimal('85.00'), vat_rate=Decimal('0.15'), availability='available', technical_confirmed=True, active=True, valid_until=now + timedelta(days=30), offered_description=mat2.name_en, supplier_description=mat2.name_en))
                    elif i < 20:
                        # Partial saved prices (only mat1)
                        pricebooks.append(PriceBookItem(org_id=org.id, material_id=mat1.id, main_category_id=target_main.id, subcategory_id=target_sub.id, material_version=1, unit='EA', currency='SAR', price_basis='supply', unit_price=Decimal('118.00'), vat_rate=Decimal('0.15'), availability='available', technical_confirmed=True, active=True, valid_until=now + timedelta(days=30), offered_description=mat1.name_en, supplier_description=mat1.name_en))
                    # Multiple responsible employees
                    contacts.append(Contact(org_id=org.id, name=f'Sales Lead {i}', email=f'sales{i}@bench.invalid', phone=f'+96650200{i:04d}', phone_normalized=f'+96650200{i:04d}', is_primary=True))
                    contacts.append(Contact(org_id=org.id, name=f'Unrelated Rep {i}', email=f'other{i}@bench.invalid', is_primary=False))
                else:
                    # Non-matching categories (2 categories per supplier)
                    sc1 = sub_cats[1 + (i % (len(sub_cats) - 1))][1]
                    sc2 = sub_cats[1 + ((i + 3) % (len(sub_cats) - 1))][1]
                    caps.append(Capability(org_id=org.id, category_id=sc1.id, city='Jeddah' if i % 3 else 'Riyadh', confirmed=True, accepts_rfqs=True, eligibility_status='enabled', material_ids=[]))
                    caps.append(Capability(org_id=org.id, category_id=sc2.id, city='Dammam', confirmed=True, accepts_rfqs=True, eligibility_status='enabled', material_ids=[]))

            db.add_all(caps)
            db.add_all(contacts)
            db.add_all(pricebooks)
            db.add_all([u for u, _ in auto_users])
            db.flush()

            for u, org in auto_users:
                db.add(Member(organization_id=org.id, user_id=u.id, role='owner'))
                org.quote_automation = {
                    'enabled': True,
                    'authorized_by': u.id,
                    'authorized_at': (now - timedelta(hours=1)).isoformat(),
                    'valid_until': (now + timedelta(days=30)).isoformat(),
                    'confirm_terms': True,
                    'service_cities': ['Riyadh'],
                    'material_ids': [mat1.id, mat2.id],
                    'max_quantity': 1000,
                    'max_total': 100000,
                    'price_basis': 'supply',
                    'vat_rate': '0.15',
                    'revision': 1,
                }

            # Create & Publish RFQ
            t_pub_start = time.perf_counter()
            rfq = RFQ(
                reference=f'RFQ-BENCH-{num_suppliers}',
                buyer_org_id=buyer_org.id,
                title=f'Electrical Supply Benchmark ({num_suppliers} suppliers)',
                city='Riyadh',
                response_deadline=now + timedelta(days=7),
                status='published',
                published_at=now,
            )
            db.add(rfq); db.flush()
            db.add(RFQItem(rfq_id=rfq.id, material_id=mat1.id, main_category_id=target_main.id, subcategory_id=target_sub.id, description=mat1.name_en, specifications='', unit='EA', quantity=Decimal('10')))
            db.add(RFQItem(rfq_id=rfq.id, material_id=mat2.id, main_category_id=target_main.id, subcategory_id=target_sub.id, description=mat2.name_en, specifications='', unit='EA', quantity=Decimal('5')))
            publish_job = Outbox(event='rfq.published', payload={'rfq_id': rfq.id, 'revision': 0}, dedupe_key=f'rfq-published:{rfq.id}')
            db.add(publish_job)
            db.commit()
            publish_ms = (time.perf_counter() - t_pub_start) * 1000.0
            rfq_id = rfq.id
            job_id = publish_job.id

        # Measure the same durable runner used by publish and the matching worker.
        query_count = 0
        def count_query(*_):
            nonlocal query_count
            query_count += 1
        event.listen(eng, 'before_cursor_execute', count_query)
        try:
            import psutil
            process = psutil.Process()
            rss_before = process.memory_info().rss
        except ImportError:
            process = None
            rss_before = None
        t_match_start = time.perf_counter()
        cpu_start = time.process_time()
        try:
            match_res = run_matching_job(eng, job_id, time_budget=None)
        finally:
            event.remove(eng, 'before_cursor_execute', count_query)
        match_ms = (time.perf_counter() - t_match_start) * 1000.0
        match_cpu_ms = (time.process_time() - cpu_start) * 1000.0
        rss_after = process.memory_info().rss if process else None
        with Session() as db:
            job_payload = db.get(Outbox, job_id).payload

        # Measure Automatic Quotation Generation for all 30 invitations
        with Session() as db:
            inv_ids = db.scalars(select(Invitation.id).where(Invitation.rfq_id == rfq_id)).all()
            t_quote_start = time.perf_counter()
            for inv_id in inv_ids:
                prepare_invitation_quote(db, inv_id)
            db.commit()
            auto_quote_ms = (time.perf_counter() - t_quote_start) * 1000.0

            auto_submitted = db.scalar(select(func.count()).select_from(Quotation).where(Quotation.rfq_id == rfq_id, Quotation.status == 'submitted', Quotation.submission_source == 'automatic'))
            partial_drafts = db.scalar(select(func.count()).select_from(Quotation).where(Quotation.rfq_id == rfq_id, Quotation.status == 'draft', Quotation.submission_source == 'automatic_draft'))
            total_quotes = db.scalar(select(func.count()).select_from(Quotation).where(Quotation.rfq_id == rfq_id))

            # Measure Quotation PDF generation (EN + AR)
            sample_quote = db.scalar(select(Quotation).where(Quotation.rfq_id == rfq_id, Quotation.status == 'submitted').limit(1))
            pdf_en = pdf_ar = b''
            pdf_ms = 0.0
            if sample_quote:
                sample_lines = db.scalars(select(QuoteLine).where(QuoteLine.quotation_id == sample_quote.id)).all()
                t_pdf_start = time.perf_counter()
                doc = quotation_document(db, sample_quote, sample_lines)
                pdf_en = build_quotation_pdf(doc, lang='en')
                pdf_ar = build_quotation_pdf(doc, lang='ar')
                pdf_ms = (time.perf_counter() - t_pdf_start) * 1000.0

        # Measure Results Query response time (simulating GET /matching/rfqs/{id}/results)
        with Session() as db:
            from app.modules.matching.router import results as get_results
            buyer = db.scalar(select(User).where(User.username == 'bench_buyer'))
            t_res_start = time.perf_counter()
            res_payload = get_results(rfq_id, limit=25, offset=0, user=buyer, db=db)
            results_api_ms = (time.perf_counter() - t_res_start) * 1000.0

        # Never run provider workers from a performance benchmark. The isolated
        # database contains only synthetic recipients, and queued events suffice
        # to verify that matching did not wait for delivery.
        with Session() as db:
            queued_notifications = int(db.scalar(select(func.count()).select_from(Outbox).where(
                Outbox.payload['rfq_id'].as_string() == rfq_id,
                Outbox.event == 'rfq.invitation',
            )) or 0)

        # Verify no duplicate matches/invitations/quotations on re-run
        with Session() as db:
            rfq = db.get(RFQ, rfq_id)
            match_and_invite(db, rfq, bounded=True, limit=50)
            db.commit()
            inv_count_after = db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id == rfq_id))
            match_count_after = db.scalar(select(func.count()).select_from(RFQMatch).where(RFQMatch.rfq_id == rfq_id))

        return {
            'suppliers': num_suppliers,
            'publish_ms': round(publish_ms, 2),
            'match_ms': round(match_ms, 2),
            'match_cpu_ms': round(match_cpu_ms, 2),
            'matching_queries': query_count,
            'rss_delta_bytes': rss_after - rss_before if rss_before is not None else None,
            'rss_after_bytes': rss_after,
            'candidate_query_ms': job_payload.get('candidate_query_ms'),
            'db_write_ms': job_payload.get('db_write_ms'),
            'results_api_ms': round(results_api_ms, 2),
            'auto_quote_ms': round(auto_quote_ms, 2),
            'pdf_en_ar_ms': round(pdf_ms, 2),
            'pdf_en_bytes': len(pdf_en),
            'pdf_ar_bytes': len(pdf_ar),
            'notifications_queued': queued_notifications,
            'eligible_suppliers': match_res['eligible_suppliers'],
            'invitations': inv_count_after,
            'matches': match_count_after,
            'auto_submitted': auto_submitted,
            'partial_drafts': partial_drafts,
            'manual_only_suppliers': inv_count_after - total_quotes,
            'results_quotations_received': res_payload['quotations_received'],
        }
    finally:
        if tmp_path is None:
            # Only the guarded, initially empty procura_benchmark_* database
            # reaches this branch. Remove tables created by this run so the
            # next tier starts from the same isolated state.
            Base.metadata.drop_all(eng)
        eng.dispose()
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def main():
    from app.config import get_settings
    parser = argparse.ArgumentParser(description='Synthetic RFQ workflow benchmark on an isolated database')
    parser.add_argument('--tiers', nargs='+', type=int, default=[100, 1000, 10000])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--batch-size', type=int, help='Override matching candidate page size for this process')
    args = parser.parse_args()
    if args.batch_size is not None:
        get_settings().matching_batch_size = args.batch_size
    print(f'=== Procumaterial Scale & Workflow Benchmark (batch_size={get_settings().matching_batch_size}) ===')
    for tier in args.tiers:
        samples = []
        for run in range(max(1, args.repeats)):
            metrics = run_scale_tier(tier)
            samples.append(metrics)
            print(
                f"[{metrics['suppliers']:5d} suppliers run={run + 1}] "
                f"publish={metrics['publish_ms']}ms | "
                f"match={metrics['match_ms']}ms | "
                f"cpu={metrics['match_cpu_ms']}ms | queries={metrics['matching_queries']} | "
                f"candidate_query={metrics['candidate_query_ms']}ms | db_write={metrics['db_write_ms']}ms | "
                f"rss_after={round((metrics['rss_after_bytes'] or 0)/1048576, 1)}MiB | "
                f"results_api={metrics['results_api_ms']}ms | "
                f"auto_quote(30)={metrics['auto_quote_ms']}ms | "
                f"notification_jobs_queued={metrics['notifications_queued']} | "
                f"matched={metrics['eligible_suppliers']} inv={metrics['invitations']} "
                f"auto_sub={metrics['auto_submitted']} partial={metrics['partial_drafts']} manual={metrics['manual_only_suppliers']}"
            )
        if len(samples) > 1:
            times = sorted(row['match_ms'] for row in samples)
            rank = 0.95 * (len(times) - 1)
            lo = int(rank)
            p95 = times[lo] + (times[min(lo + 1, len(times) - 1)] - times[lo]) * (rank - lo)
            print(f"[{tier:5d} suppliers] matching p50={statistics.median(times):.2f}ms p95={p95:.2f}ms n={len(times)}")


if __name__ == '__main__':
    main()
