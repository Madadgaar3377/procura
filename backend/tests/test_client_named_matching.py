"""Client's five product names through the persisted RFQ matcher (isolated data)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Capability, Category, Invitation, Organization, PriceBookItem, Quotation, QuoteLine, RFQ, RFQItem, RFQMatch
from app.modules.matching.service import _match_batch, match_and_invite
from app.modules.quotations.automation import prepare_invitation_quote


def test_named_items_preserve_partial_suppliers_and_notification_scope():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        for cid, parent, name in (
            ('electrical', None, 'Electrical'), ('lighting', 'electrical', 'Lighting'),
            ('plugs', 'electrical', 'Plugs and Sockets'), ('ups', 'electrical', 'UPS'),
            ('mechanical', None, 'Mechanical'), ('pipes', 'mechanical', 'Pipes')):
            db.add(Category(id=cid, code=cid.upper(), name_en=name, parent_id=parent, active=True))
        db.add(Organization(id='buyer', name='Buyer', name_normalized='buyer', is_buyer=True, is_supplier=False))
        suppliers = (
            ('flood', 'lighting', 'RGB Floodlight', 'Riyadh'),
            ('fuzzy', 'lighting', 'RGB Floodlite', 'Riyadh'),
            ('female', 'plugs', 'Female Plug; 3way Female Plug', 'Riyadh'),
            ('pipe', 'pipes', 'Pipe', 'Riyadh'),
            ('broad', 'electrical', '', 'Riyadh'),
            ('unrelated', 'ups', 'UPS battery', 'Riyadh'),
            ('other_city', 'lighting', 'RGB Floodlight', 'Jeddah'),
            ('undeclared_city', 'lighting', 'RGB Floodlight', ''),
        )
        for sid, category, words, city in suppliers:
            db.add(Organization(id=sid, name=sid, name_normalized=sid, is_supplier=True,
                                is_buyer=False, is_active=True, verified_status='verified', city=city))
            db.add(Capability(org_id=sid, category_id=category, city=city, trade_keywords=words,
                              confirmed=True, accepts_rfqs=True, eligibility_status='enabled'))
        rfq = RFQ(id='named-rfq', reference='NAMED-RFQ', buyer_org_id='buyer', title='Five products',
                  city='Riyadh', status='published', response_deadline=datetime.now(UTC) + timedelta(days=3))
        db.add(rfq)
        descriptions = (
            ('flood-item', 'RGB Flood Light', 'electrical', 'lighting'),
            ('female-item', 'Female Plug', 'electrical', 'plugs'),
            ('threeway-item', '3way Female Plug', 'electrical', 'plugs'),
            ('pipe-item', 'Pipe', 'mechanical', 'pipes'),
            ('plugmi-item', 'Plug Mi', 'electrical', 'plugs'),
        )
        for iid, description, main, sub in descriptions:
            db.add(RFQItem(id=iid, rfq_id=rfq.id, main_category_id=main, subcategory_id=sub,
                           description=description, specifications='Brand: ABB' if iid == 'flood-item' else '',
                           unit='NO.', quantity=Decimal('1')))
        db.add(PriceBookItem(org_id='flood', material_id=None, main_category_id='electrical',
                             subcategory_id='lighting', supplier_description='RGB Flood Light',
                             custom_key='rgb-flood-light', unit='NO.', unit_price=Decimal('125.00'),
                             currency='SAR', active=True, price_basis='supply', availability='available'))
        db.flush()
        started = perf_counter()
        before = _match_batch(db, rfq, evaluate_only=True)
        assert set(before['auto_invite_ids']) == {'flood', 'fuzzy', 'female', 'pipe'}
        assert 'broad' in before['eligible_ids']
        assert 'undeclared_city' in before['eligible_ids']
        assert 'undeclared_city' not in before['auto_invite_ids']
        assert 'unrelated' not in before['auto_invite_ids']
        assert before['item_levels']['plugmi-item'].get('subcategory', 0) >= 1
        result = match_and_invite(db, rfq)
        db.flush()
        elapsed_ms = round((perf_counter() - started) * 1000, 1)
        assert result['new_invitations'] == 4
        invited = {row.supplier_org_id: set(row.matched_item_ids) for row in db.scalars(
            select(Invitation).where(Invitation.rfq_id == rfq.id))}
        assert invited['flood'] == {'flood-item'}
        assert invited['fuzzy'] == {'flood-item'}
        assert {'female-item', 'threeway-item', 'plugmi-item'} <= invited['female']
        assert invited['pipe'] == {'pipe-item'}
        assert 'broad' not in invited and 'unrelated' not in invited and 'undeclared_city' not in invited
        assert db.scalar(select(RFQMatch).where(RFQMatch.rfq_id == rfq.id,
                                                RFQMatch.supplier_org_id == 'other_city')).status == 'location_mismatch'
        flood_invitation = db.scalar(select(Invitation).where(Invitation.rfq_id == rfq.id,
                                                              Invitation.supplier_org_id == 'flood'))
        quotation = prepare_invitation_quote(db, flood_invitation.id)
        assert quotation is not None and quotation.status == 'draft' and quotation.review_required
        line = db.scalar(select(QuoteLine).where(QuoteLine.quotation_id == quotation.id))
        assert line is not None and line.unit_price == Decimal('125.00') and line.source_rate_id
        assert db.scalar(select(Quotation).where(Quotation.supplier_org_id == 'female')) is None
        print({'suppliers': 8, 'evaluated': before['evaluated_suppliers'],
               'item_levels': before['item_levels'], 'excluded': before['ineligible_by_status'],
               'eligible': len(before['eligible_ids']), 'notification_eligible': len(before['auto_invite_ids']),
               'invitations': result['new_invitations'], 'pricebook_drafts': 1,
               'automatic_submissions': 0, 'elapsed_ms': elapsed_ms,
               'email_provider': 'not invoked', 'sms_provider': 'not invoked'})
    engine.dispose()
