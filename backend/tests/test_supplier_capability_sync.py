from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Capability, Category, Material, Member, Organization, PriceBookItem, RFQ, RFQItem
from app.modules.capabilities.directory import add_coverage, public_profiles
from app.modules.capabilities.router import review_capability
from app.modules.matching.service import match_and_invite
from app.modules.pricebooks.service import save_rate
from app.schemas import PriceIn


def test_private_rate_creates_pending_capability_then_approval_publishes_and_matches():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        company = Organization(name='Supplier', name_normalized='supplier', city='Riyadh',
                               is_supplier=True, is_buyer=False, verified_status='verified')
        main = Category(code='CIVIL', name_en='Civil', normalized_name='civil', parent_id=None, active=True)
        db.add_all([company, main]); db.flush()
        sub = Category(code='INSULATION', name_en='Architectural Insulation & Air/Vapor Control',
                       normalized_name='architectural insulation', parent_id=main.id, active=True)
        db.add(sub); db.flush()
        material = Material(code='INS-1', category_id=sub.id, main_category_id=main.id,
                            subcategory_id=sub.id, name_en='Supplier insulation', unit='PCS', active=True)
        db.add(material); db.flush()
        user = SimpleNamespace(id='supplier-user', is_admin=False)
        db.add(Member(user_id=user.id, organization_id=company.id, role='owner'))
        db.flush()

        rate = save_rate(db, user, PriceIn(org_id=company.id, material_id=material.id,
                                           unit_price=Decimal('150.00'), lead_days=7))
        db.flush()
        capability = db.scalar(select(Capability).where(Capability.org_id == company.id,
                                                        Capability.category_id == sub.id))
        assert rate.org_id == company.id and rate.unit_price == Decimal('150.00')
        assert capability and not capability.confirmed and capability.eligibility_status == 'enabled'
        assert rate.unit_price == Decimal('150.00')  # price remains private to price-book records
        profile = [{'id': company.id}]
        add_coverage(db, profile, include_categories=True, include_groups=True)
        assert profile[0]['capability_groups'] == []

        buyer = Organization(name='Buyer', name_normalized='buyer', is_buyer=True, is_supplier=False)
        db.add(buyer); db.flush()
        rfq = RFQ(reference='RFQ-TEST', buyer_org_id=buyer.id, title='Different wording', city='Riyadh',
                  response_deadline=datetime.now(UTC) + timedelta(days=1), status='published')
        db.add(rfq); db.flush()
        item = RFQItem(rfq_id=rfq.id, main_category_id=main.id, subcategory_id=sub.id,
                       description='Different buyer specification wording', specifications='', unit='PCS',
                       quantity=Decimal('1'))
        db.add(item); db.flush()
        assert match_and_invite(db, rfq)['eligible_suppliers'] == 0

        admin = SimpleNamespace(id='admin-user', is_admin=True)
        from app.modules.capabilities.router import pending_capabilities
        queue = pending_capabilities(admin, db)
        review_row = next(row for row in queue if row['id'] == capability.id)
        assert review_row['organization_name'] == company.name
        assert review_row['main_category_name'] == 'Civil'
        assert review_row['related_materials'] == ['Supplier insulation']
        assert 'unit_price' not in review_row
        review_capability(capability.id, SimpleNamespace(approve=True, rejection_reason=''), admin, db)
        profile = [{'id': company.id}]
        add_coverage(db, profile, include_categories=True, include_groups=True)
        assert profile[0]['capability_groups'][0]['name_en'] == 'Civil'
        assert profile[0]['capability_groups'][0]['subcategories'][0]['id'] == sub.id
        assert 'unit_price' not in profile[0]
        outcome = match_and_invite(db, rfq)
        assert outcome['eligible_suppliers'] == 1
        assert outcome['new_invitations'] == 1

        review_capability(capability.id, SimpleNamespace(approve=False,
                          rejection_reason='Insufficient evidence'), admin, db)
        assert capability.rejection_reason == 'Insufficient evidence'
        assert db.scalar(select(PriceBookItem.id).where(PriceBookItem.org_id == company.id)) == rate.id
        assert public_profiles(db, [company], include_categories=True)[0]['main_categories'] == []
    engine.dispose()
