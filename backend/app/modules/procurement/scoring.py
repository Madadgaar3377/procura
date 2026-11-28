"""Client weighted quotation ranking: Price 50%, Delivery time 30%, Warranty and terms 20%.

Method (shown to the buyer in the breakdown):

* Only submitted quotations in the RFQ's comparison currency are ranked; other
  currencies are listed as not comparable (no conversion is ever assumed).
* Price: for every RFQ line, the lowest net unit price (after line discount) among
  the ranked quotations scores 100; others score lowest/own x 100. Lines quoted for
  less than the requested quantity score pro rata. Unquoted lines score 0, so a
  partial quotation is never rewarded for what it did not quote.
* Delivery: the quotation's lead time is its longest quoted line lead time. The
  fastest scores 100; others (fastest+1)/(own+1) x 100. No lead time -> 0.
* Warranty and terms: 70 points for warranty months relative to the best offered
  warranty, plus 10 points each for stated payment terms, delivery terms and a
  validity date. Missing information scores 0.
* Weighted score = sum(weight x component) / 100. Final score = weighted score x
  coverage, where coverage is the share of RFQ lines (by requested quantity)
  actually quoted. Quotations are ranked by final score, highest first.
"""
from __future__ import annotations

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select

from app.models import RFQ, Organization, Quotation, QuoteLine, RFQItem
from app.modules.procurement.money import HUNDRED, ZERO, d

DEFAULT_WEIGHTS = {'price': 50, 'delivery': 30, 'warranty': 20}
RANKABLE = {'submitted', 'shortlisted', 'awarded'}
ONE = Decimal('1')


def _q(value: Decimal, places: str = '0.01') -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def normalize_weights(raw: dict | None) -> dict[str, int]:
    if not raw:
        return dict(DEFAULT_WEIGHTS)
    try:
        weights = {key: int(raw.get(key, 0)) for key in DEFAULT_WEIGHTS}
    except (TypeError, ValueError):
        raise HTTPException(422, 'Weights must be whole percentages') from None
    if any(value < 0 or value > 100 for value in weights.values()) or sum(weights.values()) != 100:
        raise HTTPException(422, 'Price, delivery and warranty weights must total 100%')
    return weights


def _warranty_months(line: QuoteLine, terms: dict) -> int | None:
    if line.warranty_months is not None:
        return line.warranty_months
    value = terms.get('warranty_months')
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def compare(db, rfq: RFQ, weights: dict | None = None) -> dict:
    weights = normalize_weights(weights if weights is not None else rfq.scoring_weights)
    items = db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id,RFQItem.is_active.is_(True)).order_by(RFQItem.created_at, RFQItem.id)).all()
    quotes = db.scalars(select(Quotation).where(Quotation.rfq_id == rfq.id, Quotation.status.in_(RANKABLE | {'rejected'}))).all()
    ids = [q.id for q in quotes]
    lines_by_quote: dict[str, list[QuoteLine]] = {qid: [] for qid in ids}
    if ids:
        for line in db.scalars(select(QuoteLine).where(QuoteLine.quotation_id.in_(ids))).all():
            lines_by_quote[line.quotation_id].append(line)
    names = dict(db.execute(select(Organization.id, Organization.name).where(
        Organization.id.in_({q.supplier_org_id for q in quotes}))).all()) if quotes else {}
    from app.modules.quotations.router import invoice

    ranked_quotes = [q for q in quotes if q.status in RANKABLE]
    current_quotes = [q for q in ranked_quotes if q.rfq_revision == rfq.revision]
    currency = Counter(q.currency for q in current_quotes).most_common(1)[0][0] if current_quotes else 'SAR'
    comparable = [q for q in ranked_quotes if q.currency == currency and q.rfq_revision == rfq.revision]
    item_by_id = {item.id: item for item in items}
    total_qty_weight = Decimal(len(items)) or ONE

    # Per-item net unit prices among comparable quotations.
    net_price: dict[tuple[str, str], Decimal] = {}
    for quote in comparable:
        for line in lines_by_quote[quote.id]:
            if line.rfq_item_id in item_by_id and d(line.unit_price) > 0:
                net_price[(quote.id, line.rfq_item_id)] = d(line.unit_price) * (ONE - d(line.discount_percent) / HUNDRED)
    best_price = {}
    for (qid, item_id), price in net_price.items():
        best_price[item_id] = min(price, best_price.get(item_id, price))

    lead = {}
    warranty = {}
    for quote in comparable:
        quoted = [line for line in lines_by_quote[quote.id] if (quote.id, line.rfq_item_id) in net_price]
        leads = [line.lead_days for line in quoted if line.lead_days is not None]
        lead[quote.id] = max(leads) if leads else None
        months = [_warranty_months(line, quote.commercial_terms or {}) for line in quoted]
        months = [m for m in months if m is not None]
        warranty[quote.id] = min(months) if months else None
    fastest = min((v for v in lead.values() if v is not None), default=None)
    best_warranty = max((v for v in warranty.values() if v is not None), default=None)

    results = []
    for quote in quotes:
        lines = lines_by_quote[quote.id]
        terms = quote.commercial_terms or {}
        entry = {
            'quotation_id': quote.id,
            'supplier_org_id': quote.supplier_org_id,
            'supplier_name': names.get(quote.supplier_org_id, ''),
            'status': quote.status,
            'currency': quote.currency,
            'submission_source': quote.submission_source,
            'totals': invoice(db, quote, lines),
            'quoted_items': len({line.rfq_item_id for line in lines}),
            'total_items': len(items),
        }
        if quote not in comparable:
            entry.update(comparable=False, rank=None, final_score=None, complete=False,
                         note='Supplier revision required for current RFQ' if quote.rfq_revision != rfq.revision
                         else 'Rejected quotation' if quote.status == 'rejected'
                         else f'Different currency ({quote.currency}); not ranked against {currency}')
            results.append(entry)
            continue
        price_points = ZERO
        coverage_points = ZERO
        short = []
        for item in items:
            price = net_price.get((quote.id, item.id))
            if price is None:
                continue
            line = next(line for line in lines if line.rfq_item_id == item.id)
            requested = d(item.quantity)
            fraction = min(ONE, d(line.quantity) / requested) if requested > 0 else ONE
            if fraction < ONE:
                short.append(item.description)
            coverage_points += fraction
            price_points += (best_price[item.id] / price) * fraction
        coverage = coverage_points / total_qty_weight
        price_score = price_points / total_qty_weight * HUNDRED
        own_lead = lead.get(quote.id)
        delivery_score = (Decimal(fastest + 1) / Decimal(own_lead + 1) * HUNDRED) if own_lead is not None and fastest is not None else ZERO
        own_warranty = warranty.get(quote.id)
        warranty_points = (Decimal(own_warranty) / Decimal(best_warranty) * 70) if own_warranty is not None and best_warranty else ZERO
        stated = {
            'payment_terms': bool(str(terms.get('payment_terms') or '').strip()),
            'delivery_terms': bool(str(terms.get('delivery_terms') or '').strip()),
            'validity': bool(terms.get('validity_until')),
        }
        terms_score = warranty_points + 10 * sum(stated.values())
        weighted = (weights['price'] * price_score + weights['delivery'] * delivery_score + weights['warranty'] * terms_score) / HUNDRED
        final = weighted * coverage
        missing = []
        if own_lead is None:
            missing.append('delivery time')
        if own_warranty is None:
            missing.append('warranty')
        missing += [label.replace('_', ' ') for label, present in stated.items() if not present]
        entry.update(
            comparable=True,
            complete=coverage >= ONE and not short,
            coverage_percent=str(_q(coverage * HUNDRED, '0.1')),
            lead_days=own_lead,
            warranty_months=own_warranty,
            scores={
                'price': str(_q(price_score, '0.1')),
                'delivery': str(_q(delivery_score, '0.1')),
                'warranty_terms': str(_q(terms_score, '0.1')),
            },
            weighted_score=str(_q(weighted, '0.1')),
            final_score=str(_q(final, '0.1')),
            short_quantity_items=short,
            missing_information=missing,
            breakdown=(
                f"Price {_q(price_score, '0.1')} x {weights['price']}% + Delivery {_q(delivery_score, '0.1')} x {weights['delivery']}% + "
                f"Warranty/terms {_q(terms_score, '0.1')} x {weights['warranty']}% = {_q(weighted, '0.1')}; "
                f"x coverage {_q(coverage * HUNDRED, '0.1')}% = {_q(final, '0.1')}"
            ),
        )
        results.append(entry)
    ranked = sorted((r for r in results if r.get('comparable')), key=lambda r: (-Decimal(r['final_score']), Decimal(r['totals']['total']), r['quotation_id']))
    for position, entry in enumerate(ranked, start=1):
        entry['rank'] = position
        entry['recommended'] = position == 1
    others = [r for r in results if not r.get('comparable')]
    return {
        'rfq_id': rfq.id,
        'rfq_reference': rfq.reference,
        'currency': currency,
        'weights': weights,
        'default_weights': DEFAULT_WEIGHTS,
        'method': __doc__.split('Method (shown to the buyer in the breakdown):', 1)[1].strip(),
        'ranking': ranked + others,
        'decision_note': 'The score is a recommendation. The buyer makes the final approval decision.',
    }
