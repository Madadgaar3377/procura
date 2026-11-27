"""Repair category-wide supplier-to-material assignments (reversible, no row deletion).

Background: some imported/admin capabilities were linked to (nearly) every active
Material Master record of their category. Such a link records "this supplier is in this
category", not "this supplier offers this product". The matcher already refuses to treat
those links as exact product evidence (CATEGORY_WIDE_MIN_LINKS / CATEGORY_WIDE_SHARE in
app.modules.matching.service), but the stored links still show suppliers as offering
hundreds of products on profiles and in admin screens, and feed automation policies.

What this script does for each category-wide capability:
- keeps every material link backed by real product evidence for that supplier:
  an active private price-book rate for the material, or the material name appearing in
  the supplier's own imported product/trade wording or capability keywords;
- removes the other links from supplier_capabilities.material_ids, and intersects the
  per-contact material lists (supplier_contact_capabilities.material_ids) with what is kept;
- never deletes a capability, contact link, supplier, material, invitation or quotation.

Safety:
- dry run by default; nothing is written without --apply;
- --apply requires --backup PATH: the full previous link lists are written there BEFORE
  any change, and an audit_events row per capability stores the removed ids too;
- --restore PATH puts every backed-up list back exactly.

Usage (from backend/):
  python scripts/repair_category_wide_material_links.py                     # dry run report
  python scripts/repair_category_wide_material_links.py --limit 20 --verbose
  python scripts/repair_category_wide_material_links.py --apply --backup /var/backups/procura/cat-wide-links.json
  python scripts/repair_category_wide_material_links.py --restore /var/backups/procura/cat-wide-links.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Capability, ContactCapability, ImportedSupplierTrade, Material, PriceBookItem,
)
from app.modules.matching.service import CATEGORY_WIDE_MIN_LINKS, CATEGORY_WIDE_SHARE  # noqa: E402
from app.modules.rfqs.material_matching import canonical_product_text  # noqa: E402
from app.security import audit  # noqa: E402

ACTION = 'capability.category_wide_links_repaired'


def category_sizes(db, category_ids):
    """Active materials per category, counted the way the matcher counts them."""
    sizes = Counter()
    for column in (Material.category_id, Material.subcategory_id, Material.main_category_id):
        for cid, count in db.execute(select(column, func.count()).where(
                Material.active.is_(True), column.in_(category_ids)).group_by(column)).all():
            sizes[cid] = max(sizes[cid], int(count))
    return sizes


def category_wide(db):
    """Capabilities whose links cover most of their category (same rule as the matcher)."""
    if db.bind.dialect.name == 'postgresql':
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        size = func.jsonb_array_length(cast(Capability.material_ids, JSONB))
    else:
        size = func.json_array_length(Capability.material_ids)
    candidates = list(db.scalars(select(Capability).where(size >= CATEGORY_WIDE_MIN_LINKS).order_by(Capability.id)))
    sizes = category_sizes(db, {cap.category_id for cap in candidates})
    return [cap for cap in candidates
            if sizes[cap.category_id] and len(cap.material_ids) >= CATEGORY_WIDE_SHARE * sizes[cap.category_id]]


def evidence_phrases(db, cap):
    """Canonical product wording the supplier itself supplied."""
    parts = [p for p in (cap.trade_keywords or '').split('|')]
    for raw_products, raw_trade in db.execute(select(ImportedSupplierTrade.raw_products, ImportedSupplierTrade.raw_trade)
                                              .where(ImportedSupplierTrade.org_id == cap.org_id)).all():
        parts.extend((raw_products or '').replace('\n', ';').split(';'))
        parts.extend((raw_trade or '').replace('\n', ';').split(';'))
    return {canonical_product_text(p) for p in parts if p and p.strip()} - {''}


def plan(db, cap):
    ids = list(dict.fromkeys(m for m in cap.material_ids or [] if isinstance(m, str)))
    priced = set(db.scalars(select(PriceBookItem.material_id).where(
        PriceBookItem.org_id == cap.org_id, PriceBookItem.active.is_(True), PriceBookItem.material_id.in_(ids))))
    phrases = evidence_phrases(db, cap)
    names = dict(db.execute(select(Material.id, Material.name_en).where(Material.id.in_(ids))).all())
    keep = [mid for mid in ids if mid in priced or canonical_product_text(names.get(mid, '')) in phrases]
    return ids, keep


def run(apply: bool, backup: Path | None, limit: int | None, verbose: bool) -> int:
    with SessionLocal() as db:
        caps = category_wide(db)
        if limit:
            caps = caps[:limit]
        rows = []
        for cap in caps:
            ids, keep = plan(db, cap)
            contacts = db.scalars(select(ContactCapability).where(ContactCapability.capability_id == cap.id)).all()
            rows.append((cap, ids, keep, contacts))
        orgs = {cap.org_id for cap, *_ in rows}
        removed_total = sum(len(ids) - len(keep) for _, ids, keep, _ in rows)
        print(f'Category-wide capabilities: {len(rows)} across {len(orgs)} supplier(s); '
              f'links to remove: {removed_total}; links kept with product evidence: '
              f'{sum(len(keep) for *_, keep, _ in rows)}')
        print(f"By mapping source: {dict(Counter(cap.mapping_source or 'portal/admin' for cap, *_ in rows))}")
        if verbose:
            for cap, ids, keep, contacts in rows:
                print(f'  capability={cap.id} org={cap.org_id} category={cap.category_id} links={len(ids)} '
                      f'keep={len(keep)} remove={len(ids) - len(keep)} contact_links={len(contacts)}')
        if not apply:
            print('Dry run: nothing written. Re-run with --apply --backup PATH to repair.')
            return 0
        if backup is None:
            print('--apply requires --backup PATH', file=sys.stderr)
            return 2
        snapshot = {'created_at': datetime.now(UTC).isoformat(), 'action': ACTION, 'capabilities': [
            {'id': cap.id, 'org_id': cap.org_id, 'category_id': cap.category_id, 'material_ids': ids,
             'contacts': {c.id: c.material_ids for c in contacts}} for cap, ids, _, contacts in rows]}
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            print(f'Refusing to overwrite existing backup {backup}', file=sys.stderr)
            return 2
        backup.write_text(json.dumps(snapshot, indent=1), encoding='utf-8')
        print(f'Backup written: {backup}')
        changed = 0
        for cap, ids, keep, contacts in rows:
            if len(keep) == len(ids):
                continue
            kept = set(keep)
            cap.material_ids = keep
            for contact in contacts:
                if contact.material_ids is not None:
                    contact.material_ids = [m for m in contact.material_ids if m in kept] or None
            audit(db, None, ACTION, cap.org_id, cap.id, category_id=cap.category_id, previous_count=len(ids),
                  kept_ids=keep, removed_ids=[m for m in ids if m not in kept], backup=str(backup))
            changed += 1
            if changed % 25 == 0:
                db.commit()
        db.commit()
        print(f'Repaired {changed} capability(ies). Restore with --restore {backup}')
        return 0


def restore(path: Path) -> int:
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    with SessionLocal() as db:
        restored = 0
        for entry in snapshot['capabilities']:
            cap = db.get(Capability, entry['id'])
            if not cap:
                print(f"  capability {entry['id']} no longer exists; skipped")
                continue
            cap.material_ids = entry['material_ids']
            for contact_id, material_ids in entry.get('contacts', {}).items():
                contact = db.get(ContactCapability, contact_id)
                if contact:
                    contact.material_ids = material_ids
            audit(db, None, ACTION + '_restored', cap.org_id, cap.id, backup=str(path))
            restored += 1
        db.commit()
    print(f'Restored {restored} capability(ies) from {path}')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--restore', type=Path)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    if args.restore:
        raise SystemExit(restore(args.restore))
    raise SystemExit(run(args.apply, args.backup, args.limit, args.verbose))
