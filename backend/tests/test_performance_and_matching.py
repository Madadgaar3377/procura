"""Comprehensive performance, connection pooling, Redis fallback, and matching tests."""
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.database import Base
from app.models import (
    RFQ,
    RFQItem,
    RFQMatch,
    Capability,
    Category,
    Invitation,
    Material,
    Member,
    Organization,
    PriceBookItem,
    Quotation,
    User,
)
from app.modules.matching.service import match_and_invite
from app.modules.quotations.automation import prepare_authorized_quote
from app import catalog_cache
from app.redis_client import mark_redis_failure, get_redis, is_redis_available


class PerformanceAndMatchingTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            'sqlite:///:memory:',
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=2,
            pool_timeout=5,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.engine.dispose()

    def test_connection_pool_bounded_and_reused(self):
        """Verify connections are returned and reused by the pool without leaking."""
        pool = self.engine.pool
        self.assertEqual(pool.size(), 5)
        # Check out 3 connections
        s1 = self.Session()
        s2 = self.Session()
        s3 = self.Session()
        s1.execute(text('SELECT 1'))
        s2.execute(text('SELECT 1'))
        s3.execute(text('SELECT 1'))
        self.assertEqual(pool.checkedout(), 3)
        # Close sessions and verify checkin
        s1.close()
        s2.close()
        s3.close()
        self.assertEqual(pool.checkedout(), 0)

    def test_redis_failure_graceful_degradation(self):
        """Verify that when Redis throws an error, the circuit breaker opens and L1/DB still work."""
        mark_redis_failure(RuntimeError("Simulated Redis connection drop"))
        self.assertFalse(is_redis_available())
        self.assertIsNone(get_redis())

        # Test L1 cache still stores and retrieves without crashing
        epoch = catalog_cache.version()
        test_params = {"q": "valve", "limit": 10}
        test_data = [{"id": "m1", "name": "Gate Valve"}]
        catalog_cache.set("test-materials", test_params, test_data, ttl=60, epoch=epoch)
        cached = catalog_cache.get("test-materials", test_params, epoch=epoch)
        self.assertEqual(cached, test_data)

    def test_high_speed_supplier_matching_with_exact_materials(self):
        """Verify supplier matching prioritizes exact material capabilities and city match."""
        with self.Session() as db:
            buyer = Organization(id='buyer-1', name='Buyer Co', name_normalized='buyer co', is_buyer=True, is_supplier=False, verified_status='verified')
            sup_exact = Organization(id='sup-exact', name='Exact Supplier Co', name_normalized='exact supplier co', is_buyer=False, is_supplier=True, verified_status='verified', city='Riyadh')
            sup_cat = Organization(id='sup-cat', name='Category Supplier Co', name_normalized='category supplier co', is_buyer=False, is_supplier=True, verified_status='verified', city='Riyadh')
            sup_wrong_city = Organization(id='sup-city', name='Jeddah Supplier Co', name_normalized='jeddah supplier co', is_buyer=False, is_supplier=True, verified_status='verified', city='Jeddah')
            db.add_all([buyer, sup_exact, sup_cat, sup_wrong_city])

            main_cat = Category(id='cat-civil', code='CIVIL', name_en='Civil', active=True)
            sub_cat = Category(id='cat-pipes', code='PIPES', name_en='Pipes', parent_id='cat-civil', active=True)
            db.add_all([main_cat, sub_cat])

            mat1 = Material(id='mat-p100', code='P100', name_en='Steel Pipe 100mm', category_id='cat-pipes', main_category_id='cat-civil', subcategory_id='cat-pipes', unit='MTR', active=True)
            db.add(mat1)

            # sup_exact has confirmed capability with exact material_id
            cap_exact = Capability(id='cap-1', org_id='sup-exact', category_id='cat-pipes', city='Riyadh', confirmed=True, accepts_rfqs=True, eligibility_status='enabled', material_ids=['mat-p100'])
            # sup_cat has category only
            cap_cat = Capability(id='cap-2', org_id='sup-cat', category_id='cat-pipes', city='Riyadh', confirmed=True, accepts_rfqs=True, eligibility_status='enabled', material_ids=[])
            # sup_wrong_city has Jeddah
            cap_wrong = Capability(id='cap-3', org_id='sup-city', category_id='cat-pipes', city='Jeddah', confirmed=True, accepts_rfqs=True, eligibility_status='enabled', material_ids=['mat-p100'])
            db.add_all([cap_exact, cap_cat, cap_wrong])

            user_sup = User(id='u-sup', username='sup_exact', phone='+966501112233', password_hash='hash123', is_active=True, email='sup@example.com')
            db.add(user_sup)
            db.add(Member(id='mem-1', user_id='u-sup', organization_id='sup-exact', role='owner'))

            rfq = RFQ(
                id='rfq-1',
                reference='RFQ-TEST-PERF-01',
                buyer_org_id='buyer-1',
                title='High Priority Piping',
                city='Riyadh',
                status='published',
                response_deadline=datetime.now(timezone.utc) + timedelta(days=5),
            )
            db.add(rfq)
            db.flush()
            db.add(RFQItem(id='item-1', rfq_id='rfq-1', material_id='mat-p100', main_category_id='cat-civil', subcategory_id='cat-pipes', description='Steel Pipe', unit='MTR', quantity=Decimal('500.000'), specifications='ASTM A53'))
            db.commit()

            # Run matching
            res = match_and_invite(db, rfq)
            db.commit()

            self.assertEqual(res['eligible_suppliers'], 2)  # sup-exact and sup-cat; sup-wrong-city excluded
            self.assertEqual(res['new_invitations'], 2)

            # Check matches: sup-exact should be ranked first with exact_materials in reasons
            matches = db.scalars(select(RFQMatch).where(RFQMatch.rfq_id == 'rfq-1').order_by(RFQMatch.score.desc())).all()
            self.assertEqual(len(matches), 3)  # two eligible suppliers and one persisted location rejection
            self.assertEqual(matches[0].supplier_org_id, 'sup-exact')
            self.assertTrue(any('exact_materials:1/1' in r for r in matches[0].reasons))
            self.assertTrue(any(m.supplier_org_id=='sup-city' and m.status=='location_mismatch' for m in matches))

    def test_automatic_quotation_workflow_idempotency(self):
        """Verify automatic quotations are submitted only when compliant and never duplicated."""
        with self.Session() as db:
            buyer = Organization(id='b-org', name='Buyer', name_normalized='buyer', is_buyer=True, is_supplier=False, verified_status='verified')
            supplier = Organization(
                id='s-org',
                name='Auto Supplier',
                name_normalized='auto supplier',
                is_buyer=False,
                is_supplier=True,
                verified_status='verified',
                city='Riyadh',
                quote_automation={
                    'enabled': True,
                    'price_basis': 'supply',
                    'material_ids': ['mat-cable'],
                    'service_cities': ['Riyadh'],
                    'confirm_terms': True,
                    'max_quantity': 1000,
                    'max_total': 100000,
                    'authorized_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                    'valid_until': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
                    'authorized_by': 'u-owner',
                    'revision': 1,
                },
            )
            db.add_all([buyer, supplier])

            owner = User(id='u-owner', username='owner', phone='+966504445566', password_hash='hash123', is_active=True, phone_verified=True, email='owner@example.com')
            db.add(owner)
            db.add(Member(id='m-1', user_id='u-owner', organization_id='s-org', role='owner'))

            cat = Category(id='cat-elec', code='ELEC', name_en='Electrical', active=True)
            mat = Material(id='mat-cable', code='CBL-1', name_en='Copper Cable 4mm', category_id='cat-elec', main_category_id='cat-elec', unit='MTR', active=True, version=1)
            db.add_all([cat, mat])

            # Active price book rate
            rate = PriceBookItem(
                id='rate-1',
                org_id='s-org',
                material_id='mat-cable',
                material_version=1,
                unit='MTR',
                unit_price=Decimal('25.00'),
                lead_days=3,
                price_basis='supply',
                currency='SAR',
                active=True,
                technical_confirmed=True,
                valid_until=datetime.now(timezone.utc) + timedelta(days=60),
            )
            cap = Capability(id='cap-elec', org_id='s-org', category_id='cat-elec', city='Riyadh', confirmed=True, accepts_rfqs=True, eligibility_status='enabled')
            db.add_all([rate, cap])

            rfq = RFQ(
                id='rfq-auto',
                reference='RFQ-AUTO-01',
                buyer_org_id='b-org',
                title='Cables Needed',
                city='Riyadh',
                status='collecting_quotes',
                response_deadline=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.add(rfq)
            db.flush()
            db.add(RFQItem(id='item-cable', rfq_id='rfq-auto', material_id='mat-cable', main_category_id='cat-elec', description='Copper Cable 4mm', unit='MTR', quantity=Decimal('100.000'), specifications=''))
            inv = Invitation(id='inv-1', rfq_id='rfq-auto', supplier_org_id='s-org', status='invited')
            db.add(inv)
            db.commit()

            # Execute automatic quotation preparation
            quote = prepare_authorized_quote(db, 'inv-1')
            db.commit()

            self.assertIsNotNone(quote)
            self.assertEqual(quote.status, 'submitted')
            self.assertEqual(quote.submission_source, 'automatic')
            self.assertFalse(quote.review_required)

            # Idempotency check: repeat execution must return existing quote without duplicate
            quote_repeat = prepare_authorized_quote(db, 'inv-1')
            self.assertEqual(quote_repeat.id, quote.id)
            total_quotes = db.scalars(select(Quotation).where(Quotation.invitation_id == 'inv-1')).all()
            self.assertEqual(len(total_quotes), 1)


if __name__ == '__main__':
    unittest.main()
