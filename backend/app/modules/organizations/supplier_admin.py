"""Admin supplier details: capability hierarchy, products, contacts scope, locations and coverage.

Everything here reads and writes the existing matching tables (``supplier_capabilities``,
``imported_supplier_trades``, ``supplier_contact_capabilities``), so an admin edit is used by the
next RFQ match and by RFQ notification routing without a separate matching store.

Delivery coverage is one capability row per (category, city). Legacy imported rows that hold a
joined value such as ``"Riyadh, Al Khobar, Jubail"`` are still matched per city by
``matching.service.city_covered_by``; saving coverage here splits them into per-city rows.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Capability,
    Category,
    Contact,
    ContactCapability,
    ContactSupplierTrade,
    ImportedSupplierTrade,
    Material,
    Organization,
    OrganizationLocation,
    PublicProfileConsent,
    SupplierProfileDetails,
    User,
)
from app.security import admin_user, audit
from app.modules.organizations.public_profile import PublicationPreferences, consent_available

router = APIRouter(prefix='/admin/suppliers')

DISCIPLINES = ('Civil', 'Electrical', 'Mechanical')
BusinessType = Literal['manufacturer', 'distributor', 'supplier', 'contractor', 'trader',
                       'service_provider', 'authorized_agent', 'importer']
BusinessService = Literal['supply', 'delivery', 'installation', 'testing_commissioning', 'maintenance',
                          'fabrication', 'design', 'rental', 'after_sales_support']
LocationKind = Literal['head_office', 'office', 'branch', 'factory', 'warehouse', 'showroom']
STATUS_RANK = {'approved': 0, 'pending': 1, 'suspended': 2, 'revoked': 3, 'rejected': 4}
APPROVED_TRADE_STATUSES = frozenset({'mapped', 'approved'})
_BUSINESS_TYPE_WORDS = {
    'manufacturer': 'manufacturer', 'manufacturing': 'manufacturer', 'factory': 'manufacturer',
    'distributor': 'distributor', 'distribution': 'distributor', 'supplier': 'supplier',
    'contractor': 'contractor', 'contracting': 'contractor', 'trader': 'trader', 'trading': 'trader',
    'service provider': 'service_provider', 'authorized partner': 'authorized_agent',
    'authorised partner': 'authorized_agent', 'authorized agent': 'authorized_agent',
    'authorised agent': 'authorized_agent', 'agent': 'authorized_agent', 'dealer': 'authorized_agent',
    'importer': 'importer',
}
_CLAUSE = re.compile(r'(?:^|(?<=[;|\n]))\s*(type|notes?|brands?)\s*:\s*([^;|\n]*)', re.I)
_PROSE_WORDS = re.compile(r'\b(is|are|was|we|our|has|have|provides?|offers?|specializ\w*|speciali[sz]ed|'
                          r'established|founded|leading|serving|since|delivers?)\b', re.I)


# ---------------------------------------------------------------- parsing helpers

def split_discipline(root_name: str | None) -> tuple[str | None, str]:
    """``'Civil - Infrastructure Interfaces'`` -> ``('Civil', 'Infrastructure Interfaces')``."""
    name = ' '.join((root_name or '').split())
    found = re.match(r'^(civil|electrical|mechanical)\b\s*(?:[-–—:]\s*(.*))?$', name, re.I)
    if not found:
        return None, name
    return found.group(1).title(), (found.group(2) or '').strip() or name


def split_cities(value: str | None) -> list[str]:
    """Each served city once, keeping the admin's spelling; never one joined 'city'."""
    seen: dict[str, str] = {}
    for part in re.split(r'\s*(?:[,;|،]|\s/\s|\band\b)\s*', value or '', flags=re.I):
        city = ' '.join(part.split()).strip(' .')
        if city and city.casefold() not in seen:
            seen[city.casefold()] = city
    return list(seen.values())


def split_list(value: str | None, separators: str = r'[;\n|]') -> list[str]:
    seen: dict[str, str] = {}
    for part in re.split(separators, value or ''):
        item = ' '.join(part.split()).strip()
        if item and item.casefold() not in seen:
            seen[item.casefold()] = item
    return list(seen.values())


def detect_business_types(text: str | None) -> list[str]:
    folded = ' ' + re.sub(r'[^a-z\s]', ' ', (text or '').casefold()) + ' '
    folded = ' '.join(folded.split())
    found: list[str] = []
    for phrase, value in sorted(_BUSINESS_TYPE_WORDS.items(), key=lambda item: -len(item[0])):
        if re.search(rf'(?:^| ){re.escape(phrase)}s?(?: |$)', folded) and value not in found:
            found.append(value)
    return found


def assess_description(text: str | None, vocabulary: set[str] | None = None) -> dict:
    """Separate genuine company prose from imported product/category lists and import notes.

    Pure function used both for display and by the reviewable repair script. Nothing is
    discarded: product wording is returned as ``scope_text`` and notes are kept verbatim.
    """
    raw = (text or '').strip()
    result = {'description': raw or None, 'scope_text': None, 'business_types': [], 'notes': [],
              'flagged': False, 'reasons': []}
    if not raw:
        return result
    remaining = raw
    for clause in list(_CLAUSE.finditer(raw)):
        key, value = clause.group(1).casefold(), clause.group(2).strip()
        if key == 'type':
            types = detect_business_types(value)
            result['business_types'] += [item for item in types if item not in result['business_types']]
            if value and not types:
                result['notes'].append(f'Type: {value}')
        elif value:
            result['notes'].append(f"{'Brands' if key.startswith('brand') else 'Note'}: {value}")
        remaining = remaining.replace(clause.group(0), ' ', 1)
        result['flagged'] = True
        if 'import_metadata_clause' not in result['reasons']:
            result['reasons'].append('import_metadata_clause')
    remaining = ' '.join(remaining.split()).strip(' ;|')
    segments = [part.strip() for part in re.split(r'\s*(?:/|;|\||•|\n)\s*', remaining) if part.strip()]
    words = [len(part.split()) for part in segments] or [0]
    prose = bool(re.search(r'(?:\S+\s+){7,}\S+[.!?؟](?:\s|$)', remaining)) or (
        len(remaining.split()) >= 6 and bool(_PROSE_WORDS.search(remaining)))
    vocab = {item.casefold() for item in (vocabulary or set()) if item}
    reason = None
    if remaining and not prose:
        if len(segments) >= 3 and statistics.median(words) <= 6:
            reason = 'product_or_category_list'
        elif vocab and sum(part.casefold() in vocab for part in segments) * 2 >= len(segments):
            reason = 'matches_supplier_products'
        elif len(remaining.split()) <= 4:
            reason = 'product_label_only'
    if reason:
        result.update(description=None, scope_text=remaining, flagged=True)
        result['reasons'].append(reason)
    else:
        result['description'] = remaining or None
    return result


_EXTENSION_READY: set[str] = set()


def extension_ready(db: Session) -> bool:
    """True once migration zm56c3d4e5f6 exists; a positive answer is cached per database URL."""
    bind = db.get_bind()
    url = str(getattr(bind, 'engine', bind).url)
    if url in _EXTENSION_READY and ':memory:' not in url:
        return True
    key = 'procura.supplier_profile_tables'
    if key not in db.info:
        inspector = inspect(db.connection())
        db.info[key] = inspector.has_table('supplier_profile_details') and inspector.has_table('organization_locations')
        if db.info[key]:
            _EXTENSION_READY.add(url)
    return db.info[key]


def _require_extension(db: Session):
    if not extension_ready(db):
        raise HTTPException(503, 'Supplier profile details migration zm56c3d4e5f6 is pending. Run: python -m alembic upgrade head')


def supplier_or_404(db: Session, org_id: str) -> Organization:
    org = db.get(Organization, org_id)
    if not org or not org.is_supplier:
        raise HTTPException(404, 'Supplier not found')
    return org


def capability_status(cap: Capability) -> str:
    if cap.eligibility_status in {'rejected', 'revoked', 'suspended'}:
        return cap.eligibility_status
    return 'approved' if cap.confirmed and cap.accepts_rfqs else 'pending'


def _chunks(values: list, size: int = 900):
    for start in range(0, len(values), size):
        yield values[start:start + size]


# ---------------------------------------------------------------- catalog projection

def build_catalog(db: Session, org: Organization) -> dict:
    """One bounded set of queries describing the supplier's capability groups and products."""
    caps = db.scalars(select(Capability).where(Capability.org_id == org.id)
                      .order_by(Capability.created_at, Capability.id)).all()
    trades = db.scalars(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id == org.id)
                        .order_by(ImportedSupplierTrade.created_at, ImportedSupplierTrade.id)).all()
    categories: dict[str, Category] = {}
    wanted = {cap.category_id for cap in caps}
    while wanted:
        rows = db.scalars(select(Category).where(Category.id.in_(sorted(wanted)))).all()
        categories.update({row.id: row for row in rows})
        wanted = {row.parent_id for row in rows if row.parent_id and row.parent_id not in categories}
    material_ids = sorted({mid for cap in caps for mid in (cap.material_ids or []) if isinstance(mid, str)})
    materials = {}
    for chunk in _chunks(material_ids):
        for row in db.execute(select(Material.id, Material.name_en, Material.name_ar, Material.unit,
                                     Material.discipline).where(Material.id.in_(chunk))):
            materials[row.id] = row
    contacts = {c.id: c for c in db.scalars(select(Contact).where(
        Contact.org_id == org.id, Contact.deleted_at.is_(None), Contact.merged_into_contact_id.is_(None)))}
    cap_links: dict[str, list[ContactCapability]] = {}
    for chunk in _chunks([cap.id for cap in caps]):
        for link in db.scalars(select(ContactCapability).where(ContactCapability.capability_id.in_(chunk))):
            if link.contact_id in contacts:
                cap_links.setdefault(link.capability_id, []).append(link)
    trade_contacts: dict[str, list[str]] = {}
    for chunk in _chunks([trade.id for trade in trades]):
        for trade_id, contact_id in db.execute(select(ContactSupplierTrade.trade_id, ContactSupplierTrade.contact_id)
                                               .where(ContactSupplierTrade.trade_id.in_(chunk))):
            if contact_id in contacts:
                trade_contacts.setdefault(trade_id, []).append(contact_id)

    groups: dict[str, dict] = {}
    for cap in caps:
        category = categories.get(cap.category_id)
        group = groups.get(cap.category_id)
        if group is None:
            root = categories.get(category.parent_id) if category and category.parent_id else category
            discipline, main = split_discipline(root.name_en if root else None)
            group = groups[cap.category_id] = {
                'category_id': cap.category_id, 'discipline': discipline, 'main_category': main or None,
                'main_category_id': root.id if root else None,
                'main_category_ar': root.name_ar if root else None,
                'subcategory': category.name_en if category and category.parent_id else None,
                'subcategory_ar': category.name_ar if category and category.parent_id else None,
                'category_active': bool(category and category.active),
                'capability_ids': [], 'cities': [], 'material_ids': [], 'brands': [], 'trade_keywords': [],
                'statuses': [], 'contacts': {}, 'trade_ids': [], 'mapping_sources': []}
        group['capability_ids'].append(cap.id)
        group['statuses'].append(capability_status(cap))
        if cap.mapping_source and cap.mapping_source not in group['mapping_sources']:
            group['mapping_sources'].append(cap.mapping_source)
        for city in split_cities(cap.city):
            if city.casefold() not in {c.casefold() for c in group['cities']}:
                group['cities'].append(city)
        for mid in cap.material_ids or []:
            if mid not in group['material_ids']:
                group['material_ids'].append(mid)
        for brand in cap.brands or []:
            if isinstance(brand, str) and brand.strip() and brand.strip().casefold() not in {b.casefold() for b in group['brands']}:
                group['brands'].append(brand.strip())
        for keyword in split_list(cap.trade_keywords):
            if keyword.casefold() not in {k.casefold() for k in group['trade_keywords']}:
                group['trade_keywords'].append(keyword)
        for link in cap_links.get(cap.id, []):
            scope = group['contacts'].setdefault(link.contact_id, {'all': False, 'materials': set(), 'primary': False})
            if link.material_ids is None:
                scope['all'] = True
            else:
                scope['materials'].update(link.material_ids or [])
            scope['primary'] = scope['primary'] or bool(link.is_primary)

    by_path = {}
    for group in groups.values():
        by_path[((group['main_category'] or '').casefold(), (group['subcategory'] or '').casefold())] = group
    pending_trades = []
    for trade in trades:
        parts = [part.strip() for part in (trade.raw_type or '').split('/') if part.strip()]
        discipline, main = split_discipline(parts[0] if parts else None)
        sub = parts[1] if len(parts) > 1 else None
        group = by_path.get(((main or '').casefold(), (sub or '').casefold()))
        trade_brands = split_list(trade.raw_brands, r'[;\n|,]')
        if group:
            group['trade_ids'].append(trade.id)
            for brand in trade_brands:
                if brand.casefold() not in {b.casefold() for b in group['brands']}:
                    group['brands'].append(brand)
        if not group or trade.mapping_status not in APPROVED_TRADE_STATUSES:
            pending_trades.append((trade, discipline, main or None, sub, group, trade_brands))

    def contact_refs(ids):
        return [{'id': cid, 'name': contacts[cid].name or contacts[cid].email or 'Unnamed contact'}
                for cid in dict.fromkeys(ids) if cid in contacts]

    products: list[dict] = []
    for group in groups.values():
        group['status'] = min(group['statuses'], key=lambda s: STATUS_RANK.get(s, 9))
        source_contacts = [cid for tid in group['trade_ids'] for cid in trade_contacts.get(tid, [])]
        base = {'discipline': group['discipline'], 'main_category': group['main_category'],
                'subcategory': group['subcategory'], 'category_id': group['category_id'],
                'brands': group['brands'], 'status': group['status'], 'cities': group['cities']}
        names = set()
        for mid in group['material_ids']:
            material = materials.get(mid)
            if not material:
                continue
            names.add(material.name_en.casefold())
            responsible = [cid for cid, scope in group['contacts'].items() if scope['all'] or mid in scope['materials']]
            products.append({**base, 'id': f"{group['category_id']}:{mid}", 'material_id': mid,
                             'description': material.name_en, 'description_ar': material.name_ar or None,
                             'unit': material.unit, 'source': 'material_master',
                             'contacts': contact_refs(responsible or source_contacts)})
            if not group['discipline'] and material.discipline:
                group.setdefault('_disciplines', Counter())[material.discipline] += 1
        if not group['material_ids']:
            for keyword in group['trade_keywords']:
                names.add(keyword.casefold())
                products.append({**base, 'id': f"{group['category_id']}:kw:{keyword.casefold()}", 'material_id': None,
                                 'description': keyword, 'description_ar': None, 'unit': None, 'source': 'trade_keyword',
                                 'contacts': contact_refs([cid for cid, s in group['contacts'].items() if s['all']] or source_contacts)})
        group['_names'] = names
    for group in groups.values():
        if not group['discipline'] and group.get('_disciplines'):
            group['discipline'] = group['_disciplines'].most_common(1)[0][0]
            for product in products:
                if product['category_id'] == group['category_id']:
                    product['discipline'] = group['discipline']
    for trade, discipline, main, sub, group, trade_brands in pending_trades:
        names = group['_names'] if group else set()
        for index, text in enumerate(split_list(trade.raw_products) or split_list(trade.raw_trade)):
            if text.casefold() in names:
                continue
            products.append({'id': f'trade:{trade.id}:{index}', 'material_id': None, 'trade_id': trade.id,
                             'description': text, 'description_ar': None, 'unit': None,
                             'discipline': discipline or (group['discipline'] if group else None),
                             'main_category': main, 'subcategory': sub,
                             'category_id': group['category_id'] if group else None,
                             'brands': trade_brands or (group['brands'] if group else []),
                             'status': 'pending', 'source': 'imported_source', 'mapping_status': trade.mapping_status,
                             'cities': split_cities(trade.raw_location),
                             'contacts': contact_refs(trade_contacts.get(trade.id, []))})
    for group in groups.values():
        group.pop('_names', None)
        group.pop('_disciplines', None)
        group['contacts'] = [{'id': cid, 'name': contacts[cid].name or contacts[cid].email or 'Unnamed contact',
                              'all_products': scope['all'], 'material_ids': sorted(scope['materials']),
                              'is_primary': scope['primary']} for cid, scope in group['contacts'].items()]
    return {'groups': list(groups.values()), 'products': products, 'trades': trades, 'caps': caps,
            'trade_contacts': trade_contacts}


def catalog_summary(catalog: dict) -> dict:
    products = catalog['products']
    groups = catalog['groups']
    disciplines = {p['discipline'] or 'Unassigned' for p in products} | {g['discipline'] or 'Unassigned' for g in groups}
    mains = {((p['discipline'] or ''), (p['main_category'] or '')) for p in products} | {
        ((g['discipline'] or ''), (g['main_category'] or '')) for g in groups}
    subs = {((p['main_category'] or ''), (p['subcategory'] or '')) for p in products if p['subcategory']} | {
        ((g['main_category'] or ''), (g['subcategory'] or '')) for g in groups if g['subcategory']}
    brands = {b.casefold() for p in products for b in p['brands']} | {b.casefold() for g in groups for b in g['brands']}
    status = Counter(p['status'] for p in products)
    return {'disciplines': len(disciplines), 'main_categories': len(mains), 'subcategories': len(subs),
            'products': len(products), 'brands': len(brands), 'capability_groups': len(groups),
            'approved_products': status.get('approved', 0), 'pending_products': status.get('pending', 0),
            'approved_groups': sum(g['status'] == 'approved' for g in groups),
            'pending_groups': sum(g['status'] == 'pending' for g in groups)}


def capability_tree(catalog: dict) -> list[dict]:
    tree: dict[str, dict] = {}

    def node(discipline, main, sub):
        d = tree.setdefault(discipline or 'Unassigned', {'name': discipline or 'Unassigned', 'mains': {}})
        m = d['mains'].setdefault(main or 'Unassigned', {'name': main or 'Unassigned', 'subs': {}})
        return m['subs'].setdefault(sub or '', {
            'name': sub, 'key': f"{discipline or ''}|{main or ''}|{sub or ''}", 'category_id': None,
            'capability_ids': [], 'cities': [], 'brands': [], 'contacts': [], 'statuses': Counter(),
            'status': None, 'product_count': 0, 'sample_products': []})

    for group in catalog['groups']:
        leaf = node(group['discipline'], group['main_category'], group['subcategory'])
        leaf.update(category_id=group['category_id'], capability_ids=group['capability_ids'], cities=group['cities'],
                    brands=list(group['brands']), contacts=group['contacts'], status=group['status'],
                    trade_keywords=group['trade_keywords'], material_ids=group['material_ids'],
                    category_active=group['category_active'])
    for product in catalog['products']:
        leaf = node(product['discipline'], product['main_category'], product['subcategory'])
        leaf['product_count'] += 1
        leaf['statuses'][product['status']] += 1
        if len(leaf['sample_products']) < 6:
            leaf['sample_products'].append(product['description'])
        for brand in product['brands']:
            if brand.casefold() not in {b.casefold() for b in leaf['brands']}:
                leaf['brands'].append(brand)
        if leaf['status'] is None:
            leaf['status'] = product['status']
    out = []
    for d in sorted(tree.values(), key=lambda x: (x['name'] == 'Unassigned', x['name'])):
        mains = []
        for m in sorted(d['mains'].values(), key=lambda x: x['name']):
            subs = sorted(m['subs'].values(), key=lambda x: x['name'] or '')
            for sub in subs:
                sub['approved_products'] = sub['statuses'].get('approved', 0)
                sub['pending_products'] = sub['statuses'].get('pending', 0)
                sub.pop('statuses')
            mains.append({'name': m['name'], 'subcategories': subs, 'subcategory_count': sum(1 for s in subs if s['name']),
                          'product_count': sum(s['product_count'] for s in subs),
                          'pending': sum(s['status'] == 'pending' for s in subs)})
        out.append({'name': d['name'], 'main_categories': mains, 'main_category_count': len(mains),
                    'subcategory_count': sum(m['subcategory_count'] for m in mains),
                    'product_count': sum(m['product_count'] for m in mains)})
    return out


def _profile_row(db: Session, org_id: str) -> SupplierProfileDetails | None:
    return db.scalar(select(SupplierProfileDetails).where(SupplierProfileDetails.org_id == org_id)) if extension_ready(db) else None


def detail_extensions(db: Session, org: Organization) -> dict:
    """Additive keys for GET /organizations/admin/suppliers/{id}."""
    catalog = build_catalog(db, org)
    vocabulary = {p['description'] for p in catalog['products']} | {
        name for g in catalog['groups'] for name in (g['main_category'], g['subcategory']) if name}
    assessment = assess_description(org.description, vocabulary)
    ready = extension_ready(db)
    profile = _profile_row(db, org.id)
    locations = db.scalars(select(OrganizationLocation).where(OrganizationLocation.org_id == org.id)
                           .order_by(OrganizationLocation.sort_order, OrganizationLocation.created_at)).all() if ready else []
    delivery: dict[str, dict] = {}
    for group in catalog['groups']:
        for city in group['cities'] or ['']:
            entry = delivery.setdefault(city.casefold(), {'city': city or None, 'capability_groups': 0, 'approved_groups': 0})
            entry['capability_groups'] += 1
            entry['approved_groups'] += group['status'] == 'approved'
    legacy_joined = sum(1 for cap in catalog['caps'] if len(split_cities(cap.city)) > 1)
    stored_types = list(profile.business_types or []) if profile else []
    return {
        'profile': {
            'extension_ready': ready,
            'business_types': stored_types,
            'suggested_business_types': [] if stored_types else assessment['business_types'],
            'business_services': list(profile.business_services or []) if profile else [],
            'imported_scope_text': profile.imported_scope_text if profile else None,
            'company_type': org.company_type,
            'description': assessment['description'],
            'description_review': {'flagged': assessment['flagged'], 'reasons': assessment['reasons'],
                                   'scope_text': assessment['scope_text'], 'notes': assessment['notes'],
                                   'raw_description': org.description if assessment['flagged'] else None},
        },
        'locations': [{'id': loc.id, 'kind': loc.kind, 'city': loc.city, 'address': loc.address, 'notes': loc.notes}
                      for loc in locations],
        'coverage': {
            'office_cities': split_cities(org.city),
            'delivery_cities': [entry for entry in delivery.values() if entry['city']],
            'nationwide': any((entry['city'] or '').casefold() in {'all cities', 'nationwide', 'ksa', 'saudi arabia', 'all'}
                              for entry in delivery.values()),
            'unspecified_groups': delivery.get('', {}).get('capability_groups', 0),
            'source_locations': sorted({city for trade in catalog['trades'] for city in split_cities(trade.raw_location)}),
            'legacy_joined_city_rows': legacy_joined,
        },
        'summary': catalog_summary(catalog),
        'source_products': [{'id': trade.id, 'path': trade.raw_type, 'raw_trade': trade.raw_trade,
                             'raw_products': trade.raw_products, 'raw_brands': trade.raw_brands,
                             'location': trade.raw_location, 'mapping_status': trade.mapping_status,
                             'contact_ids': list(dict.fromkeys(catalog['trade_contacts'].get(trade.id, [])))}
                            for trade in catalog['trades']],
    }


# ---------------------------------------------------------------- read endpoints

@router.get('/{org_id}/public-profile', response_model=PublicationPreferences)
def admin_public_profile(org_id: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    supplier_or_404(db, org_id)
    if not consent_available(db):
        raise HTTPException(503, 'Public profile preferences require the pending database migration')
    consent = db.get(PublicProfileConsent, org_id)
    return {'fields': consent.fields if consent else []}


@router.patch('/{org_id}/public-profile', response_model=PublicationPreferences)
def admin_save_public_profile(org_id: str, payload: PublicationPreferences,
                              admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    supplier_or_404(db, org_id)
    if not consent_available(db):
        raise HTTPException(503, 'Public profile preferences require the pending database migration')
    consent = db.get(PublicProfileConsent, org_id)
    if consent is None:
        consent = PublicProfileConsent(org_id=org_id)
        db.add(consent)
    consent.fields = sorted(set(payload.fields))
    audit(db, admin, 'organization.public_visibility_updated', org_id, org_id)
    db.commit()
    return {'fields': consent.fields}

@router.get('/{org_id}/capability-tree')
def supplier_capability_tree(org_id: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    org = supplier_or_404(db, org_id)
    catalog = build_catalog(db, org)
    return {'summary': catalog_summary(catalog), 'disciplines': capability_tree(catalog)}


@router.get('/{org_id}/products')
def supplier_products(org_id: str, q: str = Query('', max_length=200), discipline: str = Query('', max_length=60),
                      category_id: str = Query('', max_length=36), node: str = Query('', max_length=500),
                      status: str = Query('', pattern='^(|approved|pending|suspended|revoked|rejected)$'),
                      limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                      admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    org = supplier_or_404(db, org_id)
    rows = build_catalog(db, org)['products']
    disciplines = sorted({row['discipline'] or 'Unassigned' for row in rows})
    if discipline:
        rows = [row for row in rows if (row['discipline'] or 'Unassigned') == discipline]
    if category_id:
        rows = [row for row in rows if row['category_id'] == category_id]
    if node:
        rows = [row for row in rows if f"{row['discipline'] or ''}|{row['main_category'] or ''}|{row['subcategory'] or ''}" == node]
    if status:
        rows = [row for row in rows if row['status'] == status]
    needle = ' '.join(q.casefold().split())
    if needle:
        rows = [row for row in rows if all(term in ' '.join(filter(None, [
            row['description'], row.get('description_ar'), row['discipline'], row['main_category'], row['subcategory'],
            ' '.join(row['brands']), ' '.join(c['name'] for c in row['contacts'])])).casefold() for term in needle.split())]
    rows.sort(key=lambda r: (r['discipline'] or '~', r['main_category'] or '~', r['subcategory'] or '~', r['description'].casefold()))
    return {'total': len(rows), 'offset': offset, 'limit': limit, 'items': rows[offset:offset + limit],
            'disciplines': disciplines}


# ---------------------------------------------------------------- write endpoints

class ProfileDetailsInput(BaseModel):
    business_types: list[BusinessType] | None = Field(default=None, max_length=8)
    business_services: list[BusinessService] | None = Field(default=None, max_length=9)
    imported_scope_text: str | None = Field(default=None, max_length=10000)


@router.put('/{org_id}/profile-details')
def update_profile_details(org_id: str, payload: ProfileDetailsInput, admin: User = Depends(admin_user),
                           db: Session = Depends(get_db)):
    supplier_or_404(db, org_id)
    _require_extension(db)
    values = payload.model_dump(exclude_unset=True)
    row = _profile_row(db, org_id)
    if row is None:
        row = SupplierProfileDetails(org_id=org_id, business_types=[], business_services=[])
        db.add(row)
    if 'business_types' in values and values['business_types'] is not None:
        row.business_types = list(dict.fromkeys(values['business_types']))
    if 'business_services' in values and values['business_services'] is not None:
        row.business_services = list(dict.fromkeys(values['business_services']))
    if 'imported_scope_text' in values:
        row.imported_scope_text = (values['imported_scope_text'] or '').strip() or None
    audit(db, admin, 'supplier.profile_details_updated', org_id, org_id, fields=sorted(values))
    db.commit()
    return {'saved': True, 'business_types': row.business_types, 'business_services': row.business_services}


class LocationInput(BaseModel):
    kind: LocationKind
    city: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=300)


class LocationsInput(BaseModel):
    locations: list[LocationInput] = Field(max_length=50)
    confirm_clear: bool = False


@router.put('/{org_id}/locations')
def replace_locations(org_id: str, payload: LocationsInput, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    supplier_or_404(db, org_id)
    _require_extension(db)
    cleaned = []
    for item in payload.locations:
        city, address = ' '.join((item.city or '').split()) or None, ' '.join((item.address or '').split()) or None
        if not city and not address:
            raise HTTPException(422, 'Each location needs a city or an address')
        if city and len(split_cities(city)) > 1:
            raise HTTPException(422, f'Enter one city per location: "{city}"')
        cleaned.append((item.kind, city, address, ' '.join((item.notes or '').split()) or None))
    existing = db.scalars(select(OrganizationLocation).where(OrganizationLocation.org_id == org_id)).all()
    if not cleaned and existing and not payload.confirm_clear:
        raise HTTPException(422, 'Confirm removing every company location')
    for row in existing:
        db.delete(row)
    db.flush()
    for index, (kind, city, address, notes) in enumerate(cleaned):
        db.add(OrganizationLocation(org_id=org_id, kind=kind, city=city, address=address, notes=notes, sort_order=index))
    audit(db, admin, 'supplier.locations_replaced', org_id, org_id, before=len(existing), after=len(cleaned))
    db.commit()
    return {'saved': True, 'locations': len(cleaned)}


def _clean_cities(values: list[str]) -> list[str]:
    cities: dict[str, str] = {}
    for value in values:
        for city in split_cities(value):
            cities.setdefault(city.casefold(), city[:100])
    return list(cities.values())


def _copy_links(db: Session, source_ids: list[str], target: Capability):
    seen = set(db.scalars(select(ContactCapability.contact_id).where(ContactCapability.capability_id == target.id)))
    for link in db.scalars(select(ContactCapability).where(ContactCapability.capability_id.in_(source_ids))
                           .order_by(ContactCapability.created_at)):
        if link.contact_id not in seen:
            seen.add(link.contact_id)
            db.add(ContactCapability(contact_id=link.contact_id, capability_id=target.id,
                                     material_ids=link.material_ids, is_primary=link.is_primary))


def _delete_capability(db: Session, cap: Capability):
    db.execute(delete(ContactCapability).where(ContactCapability.capability_id == cap.id))
    db.delete(cap)


def apply_group_cities(db: Session, org_id: str, category_id: str, cities: list[str]) -> dict:
    """Make the category's capability rows exactly one per city, preserving products and contacts."""
    rows = db.scalars(select(Capability).where(Capability.org_id == org_id, Capability.category_id == category_id)
                      .order_by(Capability.created_at, Capability.id).with_for_update()).all()
    if not rows:
        return {'created': 0, 'removed': 0}
    template = min(rows, key=lambda cap: (STATUS_RANK.get(capability_status(cap), 9), cap.created_at))
    row_ids = [row.id for row in rows]
    single = {}
    for row in rows:
        parts = split_cities(row.city)
        if len(parts) <= 1:
            single.setdefault((parts[0] if parts else '').casefold(), row)
    keep = {}
    created = 0
    for city in cities:
        row = single.get(city.casefold())
        if row is None:
            row = Capability(org_id=org_id, category_id=category_id, city=city,
                             qualifications=list(template.qualifications or []),
                             material_ids=sorted({m for r in rows for m in (r.material_ids or [])}),
                             brands=list(dict.fromkeys(b for r in rows for b in (r.brands or []))),
                             trade_keywords=template.trade_keywords, mapping_source=template.mapping_source or 'admin',
                             confirmed=template.confirmed, accepts_rfqs=template.accepts_rfqs,
                             eligibility_status=template.eligibility_status, rejection_reason=template.rejection_reason)
            db.add(row)
            db.flush()
            _copy_links(db, row_ids, row)
            created += 1
        else:
            row.city = city
        keep[row.id] = row
    removed = 0
    for row in rows:
        if row.id not in keep:
            _delete_capability(db, row)
            removed += 1
    return {'created': created, 'removed': removed}


class CoverageInput(BaseModel):
    cities: list[str] = Field(min_length=1, max_length=60)
    category_ids: list[str] | None = Field(default=None, max_length=500)


@router.put('/{org_id}/coverage')
def replace_delivery_coverage(org_id: str, payload: CoverageInput, admin: User = Depends(admin_user),
                              db: Session = Depends(get_db)):
    """Set delivery cities for every (or the listed) capability category; one row per city."""
    supplier_or_404(db, org_id)
    cities = _clean_cities(payload.cities)
    if not cities:
        raise HTTPException(422, 'Enter at least one delivery city')
    category_ids = list(dict.fromkeys(payload.category_ids or db.scalars(
        select(Capability.category_id).where(Capability.org_id == org_id).distinct())))
    totals = {'created': 0, 'removed': 0}
    for category_id in category_ids:
        for key, value in apply_group_cities(db, org_id, category_id, cities).items():
            totals[key] += value
    audit(db, admin, 'supplier.delivery_coverage_replaced', org_id, org_id, cities=cities,
          categories=len(category_ids), **totals)
    db.commit()
    return {'saved': True, 'cities': cities, 'categories': len(category_ids), **totals}


class CapabilityGroupInput(BaseModel):
    category_id: str = Field(min_length=1, max_length=36)
    material_ids: list[str] = Field(default_factory=list, max_length=500)
    brands: list[str] = Field(default_factory=list, max_length=100)
    trade_keywords: str = Field(default='', max_length=5000)
    cities: list[str] | None = Field(default=None, max_length=60)


class CapabilityGroupPatch(BaseModel):
    material_ids: list[str] | None = Field(default=None, max_length=1000)
    add_material_ids: list[str] | None = Field(default=None, max_length=500)
    remove_material_ids: list[str] | None = Field(default=None, max_length=1000)
    brands: list[str] | None = Field(default=None, max_length=100)
    trade_keywords: str | None = Field(default=None, max_length=5000)
    status: Literal['approved', 'pending', 'suspended'] | None = None
    cities: list[str] | None = Field(default=None, max_length=60)


def _validated_materials(db: Session, category: Category, material_ids: list[str]) -> list[str]:
    ids = sorted(set(material_ids))
    if not ids:
        return []
    found = db.scalars(select(Material).where(Material.id.in_(ids), Material.active.is_(True))).all()
    if len(found) != len(ids):
        raise HTTPException(422, 'Every selected product must be an active Material Master item')
    if any(category.id not in {m.category_id, m.main_category_id, m.subcategory_id} for m in found):
        raise HTTPException(422, 'Selected products must belong to the capability category')
    return ids


def _clean_brands(values: list[str]) -> list[str]:
    brands: dict[str, str] = {}
    for value in values:
        for brand in split_list(value, r'[;\n|,]'):
            if len(brand) > 120:
                raise HTTPException(422, 'Brand names must be 120 characters or fewer')
            brands.setdefault(brand.casefold(), brand)
    return list(brands.values())


def _default_cities(db: Session, org: Organization) -> list[str]:
    cities = _clean_cities(list(db.scalars(select(Capability.city).where(Capability.org_id == org.id).distinct())))
    return cities or _clean_cities([org.city or '']) or ['']


@router.post('/{org_id}/capability-groups', status_code=201)
def add_capability_group(org_id: str, payload: CapabilityGroupInput, admin: User = Depends(admin_user),
                         db: Session = Depends(get_db)):
    org = supplier_or_404(db, org_id)
    category = db.scalar(select(Category).where(Category.id == payload.category_id, Category.active.is_(True)))
    if not category:
        raise HTTPException(422, 'Choose an active category')
    if db.scalar(select(Capability.id).where(Capability.org_id == org_id, Capability.category_id == category.id).limit(1)):
        raise HTTPException(409, 'This category is already a supplier capability; edit it instead')
    material_ids = _validated_materials(db, category, payload.material_ids)
    cities = _clean_cities(payload.cities) if payload.cities else _default_cities(db, org)
    created = []
    for city in cities or ['']:
        cap = Capability(org_id=org_id, category_id=category.id, city=city, material_ids=material_ids,
                         brands=_clean_brands(payload.brands), trade_keywords=' | '.join(split_list(payload.trade_keywords)),
                         confirmed=True, accepts_rfqs=True, eligibility_status='enabled', mapping_source='admin')
        db.add(cap)
        created.append(cap)
    db.flush()
    audit(db, admin, 'supplier.capability_group_added', org_id, category.id, cities=cities, materials=len(material_ids))
    db.commit()
    return {'category_id': category.id, 'capability_ids': [cap.id for cap in created], 'cities': cities}


@router.patch('/{org_id}/capability-groups/{category_id}')
def update_capability_group(org_id: str, category_id: str, payload: CapabilityGroupPatch,
                            admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    supplier_or_404(db, org_id)
    rows = db.scalars(select(Capability).where(Capability.org_id == org_id, Capability.category_id == category_id)
                      .with_for_update()).all()
    if not rows:
        raise HTTPException(404, 'Supplier capability not found')
    category = db.get(Category, category_id)
    values = payload.model_dump(exclude_unset=True)
    current = sorted({m for row in rows for m in (row.material_ids or [])})
    materials = None
    if values.get('material_ids') is not None:
        materials = _validated_materials(db, category, values['material_ids'])
    elif values.get('add_material_ids') or values.get('remove_material_ids'):
        added = _validated_materials(db, category, values.get('add_material_ids') or [])
        removed = set(values.get('remove_material_ids') or [])
        materials = sorted((set(current) | set(added)) - removed)
    if materials is not None:
        dropped = set(current) - set(materials)
        for row in rows:
            row.material_ids = materials
        if dropped:
            for link in db.scalars(select(ContactCapability).where(ContactCapability.capability_id.in_([r.id for r in rows]))):
                if link.material_ids is not None:
                    kept = [m for m in link.material_ids if m not in dropped]
                    link.material_ids = kept or None
    if values.get('brands') is not None:
        brands = _clean_brands(values['brands'])
        for row in rows:
            row.brands = brands
    if values.get('trade_keywords') is not None:
        keywords = ' | '.join(split_list(values['trade_keywords']))
        for row in rows:
            row.trade_keywords = keywords
    if values.get('status'):
        for row in rows:
            if values['status'] == 'approved':
                row.confirmed, row.accepts_rfqs, row.eligibility_status, row.rejection_reason = True, True, 'enabled', None
            elif values['status'] == 'pending':
                row.confirmed, row.eligibility_status = False, 'enabled'
            else:
                row.eligibility_status = 'suspended'
    for row in rows:
        row.mapping_source = 'admin'
    result = {}
    if values.get('cities') is not None:
        cities = _clean_cities(values['cities'])
        if not cities:
            raise HTTPException(422, 'A capability needs at least one delivery city')
        db.flush()
        result = apply_group_cities(db, org_id, category_id, cities)
    audit(db, admin, 'supplier.capability_group_updated', org_id, category_id, fields=sorted(values),
          materials_before=len(current), materials_after=len(materials) if materials is not None else len(current), **result)
    db.commit()
    return {'saved': True, 'category_id': category_id, **result}


@router.delete('/{org_id}/capability-groups/{category_id}')
def delete_capability_group(org_id: str, category_id: str, confirm: bool = Query(False),
                            admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    supplier_or_404(db, org_id)
    if not confirm:
        raise HTTPException(422, 'Explicit confirmation is required')
    rows = db.scalars(select(Capability).where(Capability.org_id == org_id, Capability.category_id == category_id)).all()
    if not rows:
        raise HTTPException(404, 'Supplier capability not found')
    audit(db, admin, 'supplier.capability_group_removed', org_id, category_id, capability_ids=[r.id for r in rows],
          cities=[r.city for r in rows])
    for row in rows:
        _delete_capability(db, row)
    db.commit()
    return {'removed': len(rows)}


class TradePatch(BaseModel):
    raw_trade: str | None = Field(default=None, max_length=10000)
    raw_products: str | None = Field(default=None, max_length=20000)
    raw_brands: str | None = Field(default=None, max_length=5000)
    contact_ids: list[str] | None = Field(default=None, max_length=50)


@router.patch('/{org_id}/trades/{trade_id}')
def update_source_products(org_id: str, trade_id: str, payload: TradePatch, admin: User = Depends(admin_user),
                           db: Session = Depends(get_db)):
    """Edit imported source product wording (fuzzy matching evidence) and its responsible employees."""
    supplier_or_404(db, org_id)
    trade = db.get(ImportedSupplierTrade, trade_id)
    if not trade or trade.org_id != org_id:
        raise HTTPException(404, 'Supplier product source not found')
    values = payload.model_dump(exclude_unset=True)
    before = {'raw_trade': trade.raw_trade, 'raw_products': trade.raw_products, 'raw_brands': trade.raw_brands}
    if 'raw_products' in values and values['raw_products'] is not None:
        trade.raw_products = '; '.join(split_list(values['raw_products']))
    if 'raw_trade' in values and values['raw_trade'] is not None:
        trade.raw_trade = '; '.join(split_list(values['raw_trade']))
    if 'raw_brands' in values and values['raw_brands'] is not None:
        trade.raw_brands = '; '.join(_clean_brands([values['raw_brands']]))
    if not (trade.raw_trade or '').strip() and not (trade.raw_products or '').strip():
        raise HTTPException(422, 'A product source needs at least one product description')
    if values.get('contact_ids') is not None:
        ids = list(dict.fromkeys(values['contact_ids']))
        valid = set(db.scalars(select(Contact.id).where(Contact.id.in_(ids), Contact.org_id == org_id,
                                                          Contact.deleted_at.is_(None)))) if ids else set()
        if len(valid) != len(ids):
            raise HTTPException(422, 'Every responsible contact must belong to this supplier')
        db.execute(delete(ContactSupplierTrade).where(ContactSupplierTrade.trade_id == trade.id))
        for cid in ids:
            db.add(ContactSupplierTrade(contact_id=cid, trade_id=trade.id))
    audit(db, admin, 'supplier.source_products_updated', org_id, trade.id, before=before, fields=sorted(values))
    db.commit()
    return {'saved': True, 'id': trade.id, 'raw_trade': trade.raw_trade, 'raw_products': trade.raw_products,
            'raw_brands': trade.raw_brands}


class ContactAssignment(BaseModel):
    category_id: str = Field(min_length=1, max_length=36)
    material_ids: list[str] = Field(default_factory=list, max_length=500)
    is_primary: bool = False


class ContactAssignmentsInput(BaseModel):
    assignments: list[ContactAssignment] = Field(max_length=300)


@router.put('/{org_id}/contacts/{contact_id}/assignments')
def replace_contact_assignments(org_id: str, contact_id: str, payload: ContactAssignmentsInput,
                                admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Set exactly which capability categories (and optionally products) route RFQs to this employee.

    The assignment covers every city row of a category, so routing does not depend on which
    delivery city the RFQ names. Categories that are not yet capabilities are rejected rather
    than silently creating supplier capability.
    """
    supplier_or_404(db, org_id)
    contact = db.get(Contact, contact_id)
    if not contact or contact.org_id != org_id or contact.deleted_at or contact.merged_into_contact_id:
        raise HTTPException(404, 'Supplier contact not found')
    caps = db.scalars(select(Capability).where(Capability.org_id == org_id)).all()
    by_category: dict[str, list[Capability]] = {}
    for cap in caps:
        by_category.setdefault(cap.category_id, []).append(cap)
    wanted: dict[str, ContactAssignment] = {}
    for item in payload.assignments:
        if item.category_id not in by_category:
            raise HTTPException(422, 'Assign employees only to existing supplier capabilities')
        group_materials = {m for cap in by_category[item.category_id] for m in (cap.material_ids or [])}
        if set(item.material_ids) - group_materials:
            raise HTTPException(422, 'Assigned products must be products of that capability')
        wanted[item.category_id] = item
    cap_ids = [cap.id for cap in caps]
    existing = {link.capability_id: link for link in db.scalars(select(ContactCapability).where(
        ContactCapability.contact_id == contact_id, ContactCapability.capability_id.in_(cap_ids)))} if cap_ids else {}
    keep = set()
    for category_id, item in wanted.items():
        for cap in by_category[category_id]:
            keep.add(cap.id)
            link = existing.get(cap.id)
            if link is None:
                link = ContactCapability(contact_id=contact_id, capability_id=cap.id)
                db.add(link)
            link.material_ids = sorted(set(item.material_ids)) or None
            link.is_primary = item.is_primary
            if item.is_primary:
                db.query(ContactCapability).filter(ContactCapability.capability_id == cap.id,
                                                   ContactCapability.contact_id != contact_id).update(
                    {ContactCapability.is_primary: False}, synchronize_session=False)
    removed = 0
    for cap_id, link in existing.items():
        if cap_id not in keep:
            db.delete(link)
            removed += 1
    audit(db, admin, 'supplier.contact_assignments_replaced', org_id, contact_id,
          categories=sorted(wanted), removed_links=removed)
    db.commit()
    return {'saved': True, 'categories': len(wanted), 'removed_links': removed}
