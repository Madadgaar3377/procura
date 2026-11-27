"""Reviewable repair of imported supplier profiles. Dry-run by default; nothing is deleted.

Modes (choose one or both):
  --descriptions  Move product/category lists and import notes out of organizations.description into
                  supplier_profile_details.imported_scope_text and suggested business types. Genuine
                  company prose is kept. The original text is recorded in an audit event.
  --cities        Split capability rows whose city holds several cities ("Riyadh, Al Khobar, Jubail")
                  into one row per city, copying products, brands and responsible contacts.

Usage:
  python -m scripts.repair_supplier_profiles --descriptions --report repair-preview.json
  python -m scripts.repair_supplier_profiles --descriptions --apply --supplier-id <id>
  python -m scripts.repair_supplier_profiles --cities --apply --limit 50

Review the JSON report before --apply. --apply requires migration zm56c3d4e5f6 for --descriptions.
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.models import Capability, Organization, SupplierProfileDetails
from app.modules.organizations.supplier_admin import (apply_group_cities, assess_description, build_catalog,
                                                      extension_ready, split_cities)
from app.security import audit


def plan_descriptions(db, orgs):
    for org in orgs:
        catalog = build_catalog(db, org)
        vocabulary = {p['description'] for p in catalog['products']} | {
            name for g in catalog['groups'] for name in (g['main_category'], g['subcategory']) if name}
        result = assess_description(org.description, vocabulary)
        if result['flagged']:
            yield org, result


def plan_cities(db, orgs):
    for org in orgs:
        joined = db.scalars(select(Capability).where(Capability.org_id == org.id)).all()
        categories = {}
        for cap in joined:
            if len(split_cities(cap.city)) > 1:
                categories.setdefault(cap.category_id, []).append(cap)
        for category_id, caps in categories.items():
            cities = []
            for cap in db.scalars(select(Capability).where(Capability.org_id == org.id, Capability.category_id == category_id)):
                for city in split_cities(cap.city):
                    if city.casefold() not in {c.casefold() for c in cities}:
                        cities.append(city)
            yield org, category_id, cities, [cap.city for cap in caps]


def main(argv=None):
    from app.database import SessionLocal
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--descriptions', action='store_true')
    parser.add_argument('--cities', action='store_true')
    parser.add_argument('--apply', action='store_true', help='write changes (default is a dry-run preview)')
    parser.add_argument('--supplier-id', action='append', default=[])
    parser.add_argument('--limit', type=int, default=0, help='maximum suppliers to process')
    parser.add_argument('--report', default='', help='write the preview/result as JSON to this path')
    args = parser.parse_args(argv)
    if not (args.descriptions or args.cities):
        parser.error('choose --descriptions and/or --cities')
    report = {'mode': 'apply' if args.apply else 'dry-run', 'descriptions': [], 'cities': []}
    with SessionLocal() as db:
        stmt = select(Organization).where(Organization.is_supplier.is_(True)).order_by(Organization.name, Organization.id)
        if args.supplier_id:
            stmt = stmt.where(Organization.id.in_(args.supplier_id))
        if args.limit:
            stmt = stmt.limit(args.limit)
        orgs = db.scalars(stmt).all()
        if args.descriptions:
            if args.apply and not extension_ready(db):
                raise SystemExit('Run `python -m alembic upgrade head` (zm56c3d4e5f6) before applying description repair.')
            for org, result in list(plan_descriptions(db, orgs)):
                scope = '\n'.join(filter(None, [result['scope_text'], *result['notes']])) or None
                report['descriptions'].append({'supplier_id': org.id, 'name': org.name, 'reasons': result['reasons'],
                                               'old_description': org.description, 'new_description': result['description'],
                                               'imported_scope_text': scope, 'business_types': result['business_types']})
                if args.apply:
                    details = db.scalar(select(SupplierProfileDetails).where(SupplierProfileDetails.org_id == org.id))
                    if details is None:
                        details = SupplierProfileDetails(org_id=org.id, business_types=[], business_services=[])
                        db.add(details)
                    if scope:
                        existing = details.imported_scope_text or ''
                        details.imported_scope_text = existing if scope in existing else '\n'.join(filter(None, [existing, scope]))
                    if not details.business_types and result['business_types']:
                        details.business_types = result['business_types']
                    audit(db, None, 'supplier.description_repaired', org.id, org.id,
                          old_description=org.description, new_description=result['description'], reasons=result['reasons'])
                    org.description = result['description']
        if args.cities:
            for org, category_id, cities, joined in list(plan_cities(db, orgs)):
                entry = {'supplier_id': org.id, 'name': org.name, 'category_id': category_id,
                         'joined_values': joined, 'cities': cities}
                if args.apply:
                    entry.update(apply_group_cities(db, org.id, category_id, cities))
                    audit(db, None, 'supplier.capability_cities_split', org.id, category_id, cities=cities, joined=joined)
                report['cities'].append(entry)
        if args.apply:
            db.commit()
        else:
            db.rollback()
    summary = {'mode': report['mode'], 'suppliers_scanned': len(orgs), 'descriptions_to_repair': len(report['descriptions']),
               'capability_categories_to_split': len(report['cities'])}
    print(json.dumps(summary, indent=2))
    if args.report:
        with open(args.report, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
        print(f'Report written to {args.report}')
    return report


if __name__ == '__main__':
    main()
