"""Re-run supplier matching for existing published RFQs without recreating them.

Dry run (default): evaluates the current matcher in a transaction that is rolled back
and prints the persisted (before) and evaluated (after) results. Nothing is written.

--apply: runs the RFQ's rfq.published matching job from the first supplier with the same
runner that publication and the outbox worker use (run_matching_job), page by page in
short transactions. It updates rfq_matches in place and creates at most one invitation
per supplier (uq_invitation). Existing quotations, supplier responses and invitations
are never deleted or modified (an invitation only has its matched item list refreshed).
New invitations queue rfq.invitation jobs; email/SMS is sent later by the notification
worker, never by this script.

--correct-taxonomy (with --apply): when a line's subcategory conflicts with the Material
Master product family AND the deterministic classifier agrees with confidence at least
--min-confidence, the line's main/subcategory are corrected with an audit event holding
the previous values. Lines below the threshold are left for buyer confirmation.

Usage:
  python scripts/rematch_published_rfqs.py <rfq_id> [<rfq_id> ...]
  python scripts/rematch_published_rfqs.py <rfq_id> --apply [--correct-taxonomy]
  python scripts/rematch_published_rfqs.py --all-open            # dry run for every open RFQ
"""
import argparse
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uuid  # noqa: E402

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RFQ, Invitation, Material, Outbox, Quotation, RFQItem, RFQMatch  # noqa: E402
from app.modules.matching.service import (  # noqa: E402
    CATEGORY_WIDE_MIN_LINKS, CATEGORY_WIDE_SHARE, LEVEL_ALIAS, LEVEL_EXACT, LEVEL_FAMILY, LEVEL_MAIN, LEVEL_SUB,
    MATCHED_STATUSES, _match_batch, active_categories, resolve_item_scopes, run_matching_job, supersede_plan,
    taxonomy_label,
)
from app.modules.rfqs.material_matching import classify_material_description  # noqa: E402
from app.security import as_utc, audit  # noqa: E402


def persisted(db, rfq_id):
    statuses = dict(db.execute(select(RFQMatch.status, func.count()).where(RFQMatch.rfq_id == rfq_id)
                               .group_by(RFQMatch.status)).all())
    invitations = int(db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id == rfq_id)) or 0)
    quotes = int(db.scalar(select(func.count()).select_from(Quotation).where(Quotation.rfq_id == rfq_id)) or 0)
    return statuses, invitations, quotes


def taxonomy_corrections(db, rfq, min_confidence):
    """Lines whose filed subcategory conflicts with an agreeing, confident Material Master family."""
    categories = active_categories(db)
    requested = db.execute(select(RFQItem, Material).outerjoin(Material, Material.id == RFQItem.material_id)
                           .where(RFQItem.rfq_id == rfq.id).order_by(RFQItem.created_at, RFQItem.id)).all()
    scopes, _ = resolve_item_scopes(db, requested, categories)
    changes = []
    for scope in scopes:
        item = scope['item']
        if not scope['conflict'] or item.material_id or scope['family_main'] != scope['main'] or not scope['family_sub']:
            continue
        result = classify_material_description(db, item.description, specifications=item.specifications or '',
                                               unit=item.unit, ai_enabled=False, categories=categories)
        agrees = (result['main_category_id'], result['subcategory_id']) == (scope['family_main'], scope['family_sub'])
        changes.append({
            'item': item, 'confidence': result['confidence'], 'agrees': agrees,
            'apply': agrees and result['confidence'] >= min_confidence,
            'from': taxonomy_label(categories, item.main_category_id, item.subcategory_id),
            'to': taxonomy_label(categories, scope['family_main'], scope['family_sub']),
            'target': (scope['family_main'], scope['family_sub']),
        })
    return changes


def provenance(db, rfq):
    """Supplier-to-material links for the RFQ's selected materials, by source and breadth.

    A capability linked to most active materials of its category was assigned the whole
    category; its links are not counted as exact product evidence by the matcher.
    """
    from app.models import Capability
    material_ids = [mid for mid in db.scalars(select(RFQItem.material_id).where(
        RFQItem.rfq_id == rfq.id, RFQItem.material_id.is_not(None)))]
    for mid in dict.fromkeys(material_ids):
        material = db.get(Material, mid)
        caps = [cap for cap in db.scalars(select(Capability).where(Capability.category_id.in_(
            {material.category_id, material.subcategory_id, material.main_category_id} - {None})))
            if mid in (cap.material_ids or [])]
        in_category = int(db.scalar(select(func.count()).select_from(Material).where(
            Material.active.is_(True), Material.category_id == material.category_id)) or 0)
        wide = [cap for cap in caps if len(cap.material_ids or []) >= CATEGORY_WIDE_MIN_LINKS
                and in_category and len(cap.material_ids or []) >= CATEGORY_WIDE_SHARE * in_category]
        print(f"  provenance '{material.name_en}': {len({c.org_id for c in caps})} supplier(s) link it; "
              f"by source {dict(Counter(c.mapping_source or 'portal/admin' for c in caps))}; "
              f"category-wide assignments (not exact evidence)={len({c.org_id for c in wide})} "
              f"(category has {in_category} active materials)")


def run(db, rfq_id, *, apply, correct_taxonomy, min_confidence):
    # Accept the database ID or the buyer-visible reference (e.g. RFQ-F14D3711EC55).
    rfq = db.get(RFQ, rfq_id) or db.scalar(select(RFQ).where(RFQ.reference == rfq_id))
    if rfq:
        rfq_id = rfq.id
    if not rfq:
        print(f'{rfq_id}: not found')
        return
    if rfq.status not in {'published', 'collecting_quotes'} or as_utc(rfq.response_deadline) <= datetime.now(UTC):
        print(f'{rfq_id}: skipped (status={rfq.status}, deadline={rfq.response_deadline})')
        return
    before = persisted(db, rfq_id)
    print(f'\nRFQ {rfq.reference} {rfq_id} | {rfq.project_name or rfq.title} | {rfq.city}')
    print(f'  before: matches={dict(before[0])} invitations={before[1]} quotations={before[2]}')

    changes = taxonomy_corrections(db, rfq, min_confidence)
    for change in changes:
        verdict = 'will correct' if change['apply'] and correct_taxonomy and apply else (
            'eligible for --correct-taxonomy' if change['apply'] else 'needs buyer confirmation')
        print(f"  taxonomy: '{change['item'].description}' filed {change['from']} -> Material Master {change['to']} "
              f"(classifier {change['confidence']:.3f}, agrees={change['agrees']}): {verdict}")

    started = time.monotonic()
    if not apply:
        evaluated = _match_batch(db, rfq, evaluate_only=True)
        elapsed = time.monotonic() - started
        new_matched = set(evaluated.get('eligible_ids', []))
        active = set(db.scalars(select(Invitation.supplier_org_id).where(
            Invitation.rfq_id == rfq.id, Invitation.status != 'cancelled')))
        old_matched = set(db.scalars(select(RFQMatch.supplier_org_id).where(
            RFQMatch.rfq_id == rfq.id, RFQMatch.status.in_(MATCHED_STATUSES))))
        descriptions = dict(db.execute(select(RFQItem.id, RFQItem.description).where(RFQItem.rfq_id == rfq.id)).all())
        # Strongest evidence per final supplier: exact and fuzzy product evidence are separate
        # from the category fallback a line may use only when it names no specific product.
        strongest = Counter((values or ['none'])[0] for oid, values in evaluated.get('match_levels', {}).items())
        print(f"  OLD (stored): matched suppliers={len(old_matched)} active invitations={len(active)}")
        print(f"  NEW (dry run): candidates evaluated={evaluated.get('evaluated_suppliers', 0)} "
              f"final eligible suppliers={len(new_matched)} in {elapsed:.1f}s; nothing written")
        print(f"    exact material={strongest.get(LEVEL_EXACT, 0)} "
              f"fuzzy/alias product={strongest.get(LEVEL_ALIAS, 0) + strongest.get(LEVEL_FAMILY, 0)} "
              f"category fallback (line names no product)={strongest.get(LEVEL_SUB, 0) + strongest.get(LEVEL_MAIN, 0)}")
        for item_id, counts in evaluated.get('item_levels', {}).items():
            print(f"    item '{descriptions.get(item_id, item_id)}': {sum(counts.values())} supplier(s) {counts}")
        print(f"    not matched, by classification: {evaluated.get('ineligible_by_status', {})}")
        for reason, count in evaluated.get('ineligible_reasons', {}).items():
            print(f'      {count} x {reason[:160]}')
        removed = old_matched - new_matched
        plan = supersede_plan(db, rfq, removed)
        print(f"  CHANGE if applied: newly matched={len(new_matched - old_matched)} no longer matched={len(removed)} "
              f"new invitations={len(new_matched - active)} invitations superseded (never delivered)={len(plan['cancel'])} "
              f"invitations preserved (delivered/answered)={len(plan['preserve'])} unsent messages cancelled={plan['unsent_events']}")
        provenance(db, rfq)
        db.rollback()
        return

    if correct_taxonomy:
        for change in changes:
            if not change['apply']:
                continue
            item = change['item']
            previous = {'main_category_id': item.main_category_id, 'subcategory_id': item.subcategory_id}
            item.main_category_id, item.subcategory_id = change['target']
            audit(db, None, 'rfq.item_taxonomy_corrected', rfq.buyer_org_id, item.id, rfq_id=rfq.id,
                  previous=previous, corrected={'main_category_id': item.main_category_id, 'subcategory_id': item.subcategory_id},
                  confidence=change['confidence'], reason='Material Master product family and classifier agree')
        db.flush()
    db.commit()  # Taxonomy corrections (if any) before matching reads the lines.
    job = db.scalar(select(Outbox).where(Outbox.event == 'rfq.published', Outbox.payload['rfq_id'].as_string() == rfq.id,
                                         Outbox.status.in_({'pending', 'retry', 'processing'}))
                    .order_by(Outbox.created_at.desc()).limit(1))
    if job is not None and job.status == 'processing':
        print("  the worker is running this RFQ's matching job now; rerun after it finishes")
        return
    if job is None:
        job = Outbox(event='rfq.published', payload={'rfq_id': rfq.id, 'repaired': True},
                     dedupe_key=f'rfq-repair:{rfq.id}:{uuid.uuid4().hex}')
        db.add(job)
        db.commit()
    # Same runner as publication and the worker; restart evaluates every supplier again.
    result = run_matching_job(db.get_bind(), job.id, time_budget=None, restart=True)
    if result is None:
        print('  matching job is locked by another process; rerun after it finishes')
        return
    audit(db, None, 'rfq.matching_repair_rerun', rfq.buyer_org_id, rfq.id,
          eligible=result['eligible_suppliers'], new_invitations=result['new_invitations'])
    db.commit()
    after = persisted(db, rfq_id)
    print(f"  after: matches={dict(after[0])} invitations={after[1]} (new {result['new_invitations']}) "
          f'quotations={after[2]} in {time.monotonic() - started:.1f}s')
    if after[2] != before[2]:
        raise SystemExit('Quotation count changed unexpectedly; investigate before continuing')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('rfq_ids', nargs='*')
    parser.add_argument('--all-open', action='store_true', help='Every published/collecting RFQ before its deadline')
    parser.add_argument('--apply', action='store_true', help='Write results (default is a rolled-back dry run)')
    parser.add_argument('--correct-taxonomy', action='store_true')
    parser.add_argument('--min-confidence', type=float, default=0.8)
    parser.add_argument('--tls-require-only', action='store_true',
                        help='Encrypt without verifying the server certificate (like the inspect_* scripts). '
                             'Only for a trusted network when verify-full fails in the local client library.')
    args = parser.parse_args()
    if args.correct_taxonomy and not args.apply:
        parser.error('--correct-taxonomy requires --apply')
    sessions = SessionLocal
    if args.tls_require_only:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.config import get_settings, normalize_database_url
        sessions = sessionmaker(bind=create_engine(normalize_database_url(get_settings().database_url),
                                                   connect_args={'connect_timeout': 10}, hide_parameters=True),
                                autoflush=False, expire_on_commit=False)
    with sessions() as db:
        ids = list(args.rfq_ids)
        if args.all_open:
            ids += db.scalars(select(RFQ.id).where(RFQ.status.in_({'published', 'collecting_quotes'}),
                                                   RFQ.response_deadline > datetime.now(UTC)).order_by(RFQ.created_at)).all()
        if not ids:
            parser.error('Provide RFQ IDs or --all-open')
        for rfq_id in dict.fromkeys(ids):
            run(db, rfq_id, apply=args.apply, correct_taxonomy=args.correct_taxonomy, min_confidence=args.min_confidence)


if __name__ == '__main__':
    main()
