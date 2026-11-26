"""Re-run supplier matching for existing published RFQs without recreating them.

Dry run (default): evaluates the current matcher in a transaction that is rolled back
and prints the persisted (before) and evaluated (after) results. Nothing is written.

--apply: re-evaluates every candidate in one transaction, updates rfq_matches in place,
creates at most one invitation per supplier (uq_invitation), and marks the RFQ's open
rfq.published jobs processed. Existing quotations, supplier responses and invitations
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

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RFQ, Invitation, Material, Outbox, Quotation, RFQItem, RFQMatch  # noqa: E402
from app.modules.matching.service import (  # noqa: E402
    _match_batch, active_categories, match_and_invite, resolve_item_scopes, taxonomy_label,
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


def run(db, rfq_id, *, apply, correct_taxonomy, min_confidence):
    rfq = db.get(RFQ, rfq_id)
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
        levels = Counter(level for values in evaluated.get('match_levels', {}).values() for level in values)
        print(f"  after (dry run): eligible={len(evaluated.get('eligible_ids', []))} levels={dict(levels)} "
              f'evaluated in {time.monotonic() - started:.1f}s; nothing written')
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
    result = match_and_invite(db, rfq, bounded=True, limit=100)
    for job in db.scalars(select(Outbox).where(Outbox.event == 'rfq.published', Outbox.payload['rfq_id'].as_string() == rfq.id,
                                               Outbox.status.in_({'pending', 'retry'})).with_for_update(skip_locked=True)):
        job.payload = {**{k: v for k, v in job.payload.items() if k != 'cursor'},
                       'matching_duration_ms': round((time.monotonic() - started) * 1000, 1),
                       'evaluated_suppliers': result['evaluated_suppliers'], 'repaired': True}
        job.status = 'processed'
        job.next_attempt_at = None
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
