"""Read-only: why a matched supplier's public profile link resolves or returns 404.

The public directory (GET /api/v1/directory/suppliers/{id} and /products, /related) only
lists organizations that are suppliers, active and verified_status='verified'. Matching
deliberately does NOT require verification (imported suppliers are 'unverified'), so a
matched supplier may legitimately have no public profile. The buyer results payload then
returns profile_url=null and the page shows the name without a link. This script lists,
for one RFQ, every matched/invited supplier with the fields that decide visibility.

Usage (from backend/):
  python scripts/diagnose_supplier_profile_links.py <rfq_id or RFQ reference>
  python scripts/diagnose_supplier_profile_links.py --favorites <buyer_org_id>
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import RFQ, Capability, FavoriteSupplier, Invitation, Organization, RFQMatch  # noqa: E402
from app.modules.matching.service import MATCHED_STATUSES  # noqa: E402


def visibility(org):
    reasons = [label for ok, label in ((org.is_supplier, 'not marked as supplier'), (org.is_active, 'inactive'),
                                       (org.verified_status == 'verified', f'verified_status={org.verified_status}'))
               if not ok]
    return not reasons, '; '.join(reasons) or 'listed'


def report(db, orgs, label):
    counts = Counter()
    approved = dict(db.execute(select(Capability.org_id, func.count()).where(
        Capability.org_id.in_([o.id for o in orgs]), Capability.confirmed.is_(True),
        Capability.eligibility_status == 'enabled', Capability.accepts_rfqs.is_(True)).group_by(Capability.org_id)).all())
    print(f'\n{label}: {len(orgs)}')
    for org in orgs:
        visible, why = visibility(org)
        counts['public profile' if visible else f'404: {why}'] += 1
        print(f"  {org.id}  {org.name[:50]:50}  source={org.source or '':<14} verified={org.verified_status or '':<10} "
              f"active={org.is_active} supplier={org.is_supplier} approved_caps={approved.get(org.id, 0)} "
              f"-> {'/suppliers/' + org.id if visible else 'no link (' + why + ')'}")
    print(f'  summary: {dict(counts)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('rfq', nargs='?')
    parser.add_argument('--favorites')
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.favorites:
            orgs = db.scalars(select(Organization).join(FavoriteSupplier, FavoriteSupplier.supplier_org_id == Organization.id)
                              .where(FavoriteSupplier.buyer_org_id == args.favorites).order_by(Organization.name)).all()
            report(db, orgs, 'Saved (favorite) suppliers')
            return 0
        rfq = db.get(RFQ, args.rfq) or db.scalar(select(RFQ).where(RFQ.reference == args.rfq))
        if not rfq:
            print('RFQ not found');return 1
        print(f'RFQ {rfq.reference} ({rfq.id}) status={rfq.status}')
        matched = db.scalars(select(Organization).join(RFQMatch, RFQMatch.supplier_org_id == Organization.id).where(
            RFQMatch.rfq_id == rfq.id, RFQMatch.status.in_(MATCHED_STATUSES)).order_by(Organization.name)).all()
        report(db, matched, 'Matched suppliers')
        invited = db.scalars(select(Organization).join(Invitation, Invitation.supplier_org_id == Organization.id).where(
            Invitation.rfq_id == rfq.id, Invitation.status != 'cancelled').order_by(Organization.name)).all()
        report(db, invited, 'Invited suppliers')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
