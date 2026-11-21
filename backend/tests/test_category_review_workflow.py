"""Comprehensive tests for Category Review Needed classification, bulk safe auto-fix,
duplicate merging, empty category retention, explicit decisions, and buyer/supplier workflows.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    Capability,
    Category,
    CategoryReviewDecision,
    ImportBatch,
    ImportedRow,
    Material,
    MaterialRevision,
    Organization,
    RFQ,
    RFQItem,
    User,
)
from app.modules.matching.service import eligible_supplier_ids
from app.modules.taxonomy.review_service import (
    analyze_categories,
    apply_single_category_decision,
    build_analysis_payload,
    run_bulk_approve_valid,
    run_bulk_auto_fix,
)
from app.modules.taxonomy.router import categories as list_categories_endpoint


def _make_session_factory():
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    return engine, factory


def _seed_admin(db: Session) -> User:
    admin = User(
        username='review_admin',
        phone='+966500001111',
        phone_e164='+966500001111',
        password_hash='x',
        is_admin=True,
        is_active=True,
        approval_status='approved',
    )
    db.add(admin)
    db.flush()
    return admin


def test_imported_categories_classification_auto_fix_and_idempotency():
    engine, SessionFactory = _make_session_factory()
    try:
        with SessionFactory() as db:
            admin = _seed_admin(db)

            # 1. Main categories matching actual imported hierarchy examples
            elec = Category(code='IMP-ELEC-01', name_en='Electrical', normalized_name='electrical', active=True)
            arch_ins = Category(
                code='IMP-ARCH-01',
                name_en='Architectural Insulation & Air/Vapor Control',
                normalized_name='architectural insulation & air/vapor control',
                active=True,
            )
            concrete = Category(
                code='IMP-CONC-01',
                name_en='Concrete Constituents & Supplied Concrete',
                normalized_name='concrete constituents & supplied concrete',
                active=True,
            )
            shared = Category(
                code='IMP-SHAR-01',
                name_en='Shared Chemicals, Anchors & Consumables',
                normalized_name='shared chemicals, anchors & consumables',
                active=True,
            )
            qa = Category(code='IMP-QA-01', name_en='Bulk QA Import Test', normalized_name='bulk qa import test', active=True)
            db.add_all([elec, arch_ins, concrete, shared, qa])
            db.flush()

            # 2. Duplicate subcategories: Electrical -> Accessories (0 materials) vs (3 materials)
            acc_empty = Category(
                code='IMP-ACC-EMPTY',
                name_en='Accessories',
                normalized_name='accessories',
                parent_id=elec.id,
                active=True,
            )
            acc_populated = Category(
                code='IMP-ACC-POP',
                name_en='Accessories',
                normalized_name='accessories',
                parent_id=elec.id,
                active=True,
            )
            acoustic = Category(
                code='IMP-ACOUSTIC',
                name_en='Acoustic Products',
                normalized_name='acoustic products',
                parent_id=arch_ins.id,
                active=True,
            )
            vapor = Category(
                code='IMP-VAPOR',
                name_en='Air & Vapor Control Layers',
                normalized_name='air & vapor control layers',
                parent_id=arch_ins.id,
                active=True,
            )
            admixtures = Category(
                code='IMP-ADMIX',
                name_en='Admixtures & Fibres',
                normalized_name='admixtures & fibres',
                parent_id=concrete.id,
                active=True,
            )
            empty_sub = Category(
                code='IMP-CONC-EMPTY',
                name_en='Supplied Specialty Concrete',
                normalized_name='supplied specialty concrete',
                parent_id=concrete.id,
                active=True,
            )
            adhesives = Category(
                code='IMP-ADH',
                name_en='Adhesives & Resins',
                normalized_name='adhesives & resins',
                parent_id=shared.id,
                active=True,
            )
            anchors = Category(
                code='IMP-ANC',
                name_en='Anchors & Fasteners',
                normalized_name='anchors & fasteners',
                parent_id=shared.id,
                active=True,
            )
            db.add_all([acc_empty, acc_populated, acoustic, vapor, admixtures, empty_sub, adhesives, anchors])
            db.flush()

            # Import batch with source discipline metadata
            batch = ImportBatch(
                kind='materials',
                filename='electrical_catalog.xlsx',
                status='completed',
                rows=1,
                initiated_by=admin.id,
                preview_metadata={'discipline': 'Electrical'},
            )
            db.add(batch)
            db.flush()
            db.add(
                ImportedRow(
                    batch_id=batch.id,
                    row_number=2,
                    raw={'_discipline': 'Electrical', '_name': 'Brass Cable Gland M20'},
                    status='created',
                )
            )

            # 3. Materials with missing/unsupported disciplines
            m_gland = Material(
                code='MAT-ACC-1',
                category_id=acc_populated.id,
                main_category_id=elec.id,
                subcategory_id=acc_populated.id,
                name_en='Brass Cable Gland M20 IP68',
                unit='PCS',
                discipline=None,
                import_batch_id=batch.id,
                import_row_number=2,
                active=True,
            )
            m_conduit = Material(
                code='MAT-ACC-2',
                category_id=acc_populated.id,
                main_category_id=elec.id,
                subcategory_id=acc_populated.id,
                name_en='PVC Conduit Inspection Elbow 25mm',
                unit='PCS',
                discipline=None,
                active=True,
            )
            m_generic_acc = Material(
                code='MAT-ACC-3',
                category_id=acc_populated.id,
                main_category_id=elec.id,
                subcategory_id=acc_populated.id,
                name_en='Generic Universal Mounting Bracket',
                unit='PCS',
                discipline=None,
                active=True,
            )
            m_acoustic_civil = Material(
                code='MAT-ACO-1',
                category_id=acoustic.id,
                main_category_id=arch_ins.id,
                subcategory_id=acoustic.id,
                name_en='Mineral Wool Acoustic Ceiling Board 600x600',
                unit='M2',
                discipline=None,
                active=True,
            )
            m_vapor = Material(
                code='MAT-VAP-1',
                category_id=vapor.id,
                main_category_id=arch_ins.id,
                subcategory_id=vapor.id,
                name_en='Polyethylene Vapor Barrier Membrane 250 Micron',
                unit='M2',
                discipline=None,
                active=True,
            )
            m_admix = Material(
                code='MAT-ADM-1',
                category_id=admixtures.id,
                main_category_id=concrete.id,
                subcategory_id=admixtures.id,
                name_en='Superplasticizer Concrete Admixture Type F',
                unit='L',
                discipline=None,
                active=True,
            )
            # Shared category: one clearly Civil, one genuinely ambiguous generic sealant
            m_anchor_civil = Material(
                code='MAT-ANC-1',
                category_id=anchors.id,
                main_category_id=shared.id,
                subcategory_id=anchors.id,
                name_en='Heavy Duty Concrete Wedge Anchor M12x120',
                unit='PCS',
                discipline=None,
                active=True,
            )
            m_sealant_generic = Material(
                code='MAT-ADH-1',
                category_id=adhesives.id,
                main_category_id=shared.id,
                subcategory_id=adhesives.id,
                name_en='General Purpose Silicone Sealant 300ml',
                unit='PCS',
                discipline=None,
                active=True,
            )
            db.add_all([
                m_gland, m_conduit, m_generic_acc, m_acoustic_civil,
                m_vapor, m_admix, m_anchor_civil, m_sealant_generic,
            ])
            db.flush()

            # Supplier capability & RFQ reference on acc_empty to verify preservation on merge
            supplier = Organization(name='Gulf Electrical Supply', name_normalized='gulf electrical supply', is_supplier=True, is_active=True, verified_status='verified')
            buyer = Organization(name='Riyadh Builders', name_normalized='riyadh builders', is_buyer=True, is_active=True, verified_status='verified')
            db.add_all([supplier, buyer])
            db.flush()
            cap = Capability(org_id=supplier.id, category_id=acc_empty.id, city='Riyadh', confirmed=True, accepts_rfqs=True, eligibility_status='enabled')
            rfq = RFQ(
                reference='RFQ-TEST-001',
                buyer_org_id=buyer.id,
                title='Electrical Accessories Package',
                city='Riyadh',
                response_deadline=datetime.now(UTC) + timedelta(days=5),
                status='published',
            )
            db.add_all([cap, rfq])
            db.flush()
            rfq_item = RFQItem(
                rfq_id=rfq.id,
                material_id=m_gland.id,
                main_category_id=elec.id,
                subcategory_id=acc_empty.id,
                description=m_gland.name_en,
                specifications='',
                unit='PCS',
                quantity=Decimal('10'),
            )
            db.add(rfq_item)
            db.commit()

            original_ids = {
                'gland': m_gland.id,
                'conduit': m_conduit.id,
                'generic_acc': m_generic_acc.id,
                'acoustic': m_acoustic_civil.id,
                'vapor': m_vapor.id,
                'admix': m_admix.id,
                'anchor': m_anchor_civil.id,
                'sealant': m_sealant_generic.id,
            }
            acc_pop_id = acc_populated.id
            acc_empty_id = acc_empty.id
            empty_sub_id = empty_sub.id
            adhesives_id = adhesives.id
            qa_id = qa.id
            cap_id = cap.id
            rfq_item_id = rfq_item.id

        # Step 1: Analyze all before fixing
        with SessionFactory() as db:
            payload = build_analysis_payload(db)
            by_cat = {item['id']: item for item in payload['items']}
            assert by_cat[acc_empty_id]['issue_type'] == 'duplicate_category'
            assert by_cat[acc_pop_id]['issue_type'] == 'duplicate_category'
            assert by_cat[acc_pop_id]['duplicate_canonical_id'] == acc_pop_id
            assert by_cat[empty_sub_id]['issue_type'] == 'empty_category'
            assert by_cat[adhesives_id]['issue_type'] == 'ambiguous_classification'
            assert by_cat[qa_id]['issue_type'] == 'invalid_import_data'
            assert payload['total_safe_auto_fix'] > 0
            assert payload['total_conflicts'] > 0

        # Step 2: Dry-run confirmation preview makes zero changes
        preview = run_bulk_auto_fix(actor=admin, dry_run=True, session_factory=SessionFactory)
        assert preview['dry_run'] is True
        assert preview['categories_processed'] > 0
        with SessionFactory() as db:
            assert db.get(Category, acc_empty_id).active is True
            assert db.get(Material, original_ids['admix']).discipline is None

        # Step 3: Run Auto-Fix Safe Records (Pass 1 merges duplicates + fixes safe disciplines;
        # Pass 2 fixes safe electrical materials on now-deduplicated Accessories)
        res1 = run_bulk_auto_fix(actor=admin, dry_run=False, session_factory=SessionFactory)
        assert res1['duplicates_merged'] == 1
        assert res1['materials_updated'] >= 4

        res2 = run_bulk_auto_fix(actor=admin, dry_run=False, session_factory=SessionFactory)
        assert res2['materials_updated'] == 2  # m_gland + m_conduit now safely assigned Electrical

        # Pass 3 must be completely idempotent!
        res3 = run_bulk_auto_fix(actor=admin, dry_run=False, session_factory=SessionFactory)
        assert res3['categories_processed'] == 0
        assert res3['materials_updated'] == 0
        assert res3['duplicates_merged'] == 0

        # Verify database integrity & preserved IDs
        with SessionFactory() as db:
            assert db.get(Category, acc_empty_id).active is False
            assert db.get(Category, acc_pop_id).active is True
            # Capability and RFQItem moved to canonical category!
            assert db.get(Capability, cap_id).category_id == acc_pop_id
            assert db.get(RFQItem, rfq_item_id).subcategory_id == acc_pop_id

            # Material IDs preserved and safe disciplines assigned
            assert db.get(Material, original_ids['gland']).discipline == 'Electrical'
            assert db.get(Material, original_ids['conduit']).discipline == 'Electrical'
            # Generic mounting bracket in Electrical -> Accessories NOT blindly forced!
            assert db.get(Material, original_ids['generic_acc']).discipline is None
            # Architectural insulation & Concrete assigned Civil
            assert db.get(Material, original_ids['acoustic']).discipline == 'Civil'
            assert db.get(Material, original_ids['vapor']).discipline == 'Civil'
            assert db.get(Material, original_ids['admix']).discipline == 'Civil'
            # Shared category: concrete wedge anchor -> Civil, generic silicone sealant -> left pending!
            assert db.get(Material, original_ids['anchor']).discipline == 'Civil'
            assert db.get(Material, original_ids['sealant']).discipline is None
            # Revisions recorded
            revs = db.scalars(select(MaterialRevision).where(MaterialRevision.material_id == original_ids['admix'])).all()
            assert len(revs) >= 1

        # Step 4: Approve All Valid retains empty category without deleting or fabricating materials
        approve_res = run_bulk_approve_valid(actor=admin, dry_run=False, include_empty=True, session_factory=SessionFactory)
        assert approve_res['retained_empty'] >= 1
        with SessionFactory() as db:
            # Empty category still active, still 0 materials, and no longer in pending review queue!
            assert db.get(Category, empty_sub_id).active is True
            pending_ids = {a.category.id for a in analyze_categories(db, include_resolved=False)}
            assert empty_sub_id not in pending_ids
            # Ambiguous & QA items STILL in pending queue!
            assert adhesives_id in pending_ids
            assert acc_pop_id in pending_ids
            assert qa_id in pending_ids

            # Buyer RFQ category selection (with_active_materials=True) excludes retained empty category!
            buyer_cats = list_categories_endpoint(limit=None, offset=0, with_active_materials=True, db=db)
            buyer_cat_ids = {c['id'] for c in buyer_cats}
            assert empty_sub_id not in buyer_cat_ids
            assert acc_pop_id in buyer_cat_ids

            # Explicit decision validation: cannot blindly approve category with missing discipline
            with pytest.raises(HTTPException) as exc_info:
                apply_single_category_decision(db, category_id=adhesives_id, decision='approve', actor=admin)
            assert exc_info.value.status_code == 422

            # Explicit per-material reclassify resolves ambiguous shared category
            dec = apply_single_category_decision(
                db,
                category_id=adhesives_id,
                decision='reclassify',
                actor=admin,
                material_disciplines={original_ids['sealant']: 'Civil'},
                notes='Verified sealant is used for architectural facade joints.',
            )
            assert dec['materials_updated'] == 1
            assert db.get(Material, original_ids['sealant']).discipline == 'Civil'
            assert adhesives_id not in {a.category.id for a in analyze_categories(db, include_resolved=False)}

            # Audit history persisted
            history = db.scalars(
                select(CategoryReviewDecision).where(CategoryReviewDecision.category_id == adhesives_id)
            ).all()
            assert len(history) == 1
            assert history[0].decision == 'reclassify'
    finally:
        engine.dispose()


def test_batched_processing_30_per_batch_with_continuation_and_matching_guard():
    engine, SessionFactory = _make_session_factory()
    try:
        with SessionFactory() as db:
            admin = _seed_admin(db)
            concrete_root = Category(
                code='IMP-CONC-ROOT',
                name_en='Concrete Constituents & Supplied Concrete',
                normalized_name='concrete constituents & supplied concrete',
                active=True,
            )
            db.add(concrete_root)
            db.flush()

            # Create 45 subcategories each with 1 Concrete material missing discipline
            for i in range(45):
                sub = Category(
                    code=f'IMP-BATCH-SUB-{i:03d}',
                    name_en=f'Concrete Admixtures Group {i:03d}',
                    normalized_name=f'concrete admixtures group {i:03d}',
                    parent_id=concrete_root.id,
                    active=True,
                )
                db.add(sub)
                db.flush()
                db.add(
                    Material(
                        code=f'MAT-BATCH-{i:03d}',
                        category_id=sub.id,
                        main_category_id=concrete_root.id,
                        subcategory_id=sub.id,
                        name_en=f'Concrete Superplasticizer Grade {i:03d}',
                        unit='L',
                        discipline=None,
                        active=True,
                    )
                )

            # Also verify supplier matching excludes materials with unsupported discipline
            elec_root = Category(code='ELEC-MATCH', name_en='Electrical', normalized_name='electrical', active=True)
            db.add(elec_root)
            db.flush()
            sub_match = Category(code='SUB-MATCH', name_en='Wiring Devices', normalized_name='wiring devices', parent_id=elec_root.id, active=True)
            db.add(sub_match)
            db.flush()
            invalid_mat = Material(
                code='MAT-INVALID-DISC',
                category_id=sub_match.id,
                main_category_id=elec_root.id,
                subcategory_id=sub_match.id,
                name_en='Unclassified Mystery Device',
                unit='PCS',
                discipline='UnsupportedTrade',
                active=True,
            )
            supplier = Organization(name='Match Supplier', name_normalized='match supplier', is_supplier=True, is_active=True, verified_status='verified')
            buyer = Organization(name='Match Buyer', name_normalized='match buyer', is_buyer=True, is_active=True, verified_status='verified')
            db.add_all([invalid_mat, supplier, buyer])
            db.flush()
            db.add(
                Capability(
                    org_id=supplier.id,
                    category_id=elec_root.id,  # broad main only, relies on exact material_ids
                    city='Riyadh',
                    material_ids=[invalid_mat.id],
                    confirmed=True,
                    accepts_rfqs=True,
                    eligibility_status='enabled',
                )
            )
            rfq = RFQ(
                reference='RFQ-GUARD-001',
                buyer_org_id=buyer.id,
                title='Invalid Material Guard Test',
                city='Riyadh',
                response_deadline=datetime.now(UTC) + timedelta(days=5),
                status='published',
            )
            db.add(rfq)
            db.flush()
            db.add(
                RFQItem(
                    rfq_id=rfq.id,
                    material_id=invalid_mat.id,
                    main_category_id=elec_root.id,
                    subcategory_id=sub_match.id,
                    description=invalid_mat.name_en,
                    specifications='',
                    unit='PCS',
                    quantity=Decimal('5'),
                )
            )
            db.commit()
            rfq_id = rfq.id

        # Supplier matching must NOT match on the unsupported-discipline material!
        with SessionFactory() as db:
            rfq_obj = db.get(RFQ, rfq_id)
            supplier_ids = db.scalars(select(Organization.id).where(Organization.is_supplier.is_(True))).all()
            matches = eligible_supplier_ids(db, rfq_obj, list(supplier_ids))
            assert len(matches) == 0

        # Run bulk auto-fix with job_limit=35, batch_size=30 -> 2 batches + has_more=True
        job1 = run_bulk_auto_fix(actor=admin, dry_run=False, job_limit=35, batch_size=30, session_factory=SessionFactory)
        assert job1['batches_executed'] == 2
        assert job1['categories_processed'] == 35
        assert job1['has_more'] is True
        assert job1['next_cursor'] is not None

        # Continuation job finishes the remaining safe categories
        job2 = run_bulk_auto_fix(
            actor=admin,
            dry_run=False,
            cursor_after_id=job1['next_cursor'],
            job_limit=35,
            batch_size=30,
            session_factory=SessionFactory,
        )
        assert job2['categories_processed'] >= 10
        assert job2['has_more'] is False
    finally:
        engine.dispose()


def test_approve_valid_and_auto_fix_router_no_detached_instance_error(monkeypatch):
    """Regression test for production HTTP 500 DetachedInstanceError in review_router.py."""
    from app.models import AuditEvent
    from app.modules.taxonomy import review_router

    engine, SessionFactory = _make_session_factory()
    monkeypatch.setattr(review_router, 'SessionLocal', SessionFactory)
    try:
        with SessionFactory() as db:
            admin = _seed_admin(db)
            main_cat = Category(
                code='IMP-ROUTER-MAIN',
                name_en='Concrete Constituents & Supplied Concrete',
                normalized_name='concrete constituents & supplied concrete',
                active=True,
            )
            db.add(main_cat)
            db.flush()
            empty_cat = Category(
                code='IMP-ROUTER-EMPTY',
                name_en='Specialty Grouts',
                normalized_name='specialty grouts',
                parent_id=main_cat.id,
                active=True,
            )
            qa_cat = Category(
                code='IMP-ROUTER-QA',
                name_en='Bulk QA Test Category',
                normalized_name='bulk qa test category',
                active=True,
            )
            db.add_all([empty_cat, qa_cat])
            db.commit()
            admin_id = admin.id

        # Simulate FastAPI dependency: load ORM User from request Session, then call router handler
        with SessionFactory() as req_db:
            orm_admin = req_db.get(User, admin_id)
            assert orm_admin is not None
            preview = review_router.approve_all_valid(
                payload=review_router.BulkApproveValidIn(dry_run=True, include_empty=True, batch_size=30),
                user=orm_admin,
                db=req_db,
            )
            assert preview['dry_run'] is True
            assert preview['total_eligible'] == 2  # main_cat + empty_cat
            assert preview['retained_empty'] == 2
            assert preview['excluded_count'] == 1  # qa_cat
            assert preview['excluded_by_issue_type'] == {'invalid_import_data': 1}
            assert len(preview['preview_items']) == 2
            assert len(preview['excluded_preview_items']) == 1

        # Apply via router with a fresh ORM User (must not raise DetachedInstanceError)
        with SessionFactory() as req_db:
            orm_admin = req_db.get(User, admin_id)
            assert orm_admin is not None
            applied = review_router.approve_all_valid(
                payload=review_router.BulkApproveValidIn(dry_run=False, include_empty=True, batch_size=30),
                user=orm_admin,
                db=req_db,
            )
            assert applied['dry_run'] is False
            assert applied['retained_empty'] == 2
            assert applied['remaining_valid'] == 0
            assert applied['batches_executed'] == 1

        # Auto-fix router handler also must not raise DetachedInstanceError
        with SessionFactory() as req_db:
            orm_admin = req_db.get(User, admin_id)
            assert orm_admin is not None
            fix_preview = review_router.auto_fix_safe_records(
                payload=review_router.BulkCategoryFixIn(dry_run=True, batch_size=30),
                user=orm_admin,
                db=req_db,
            )
            assert fix_preview['dry_run'] is True

        # Verify audit log written once
        with SessionFactory() as verify_db:
            audits = verify_db.scalars(
                select(AuditEvent).where(AuditEvent.action == 'category.bulk_retained_empty')
            ).all()
            assert len(audits) == 1
            assert audits[0].metadata_json['count'] == 2
    finally:
        engine.dispose()


def test_bulk_approve_valid_batches_of_30_and_strict_idempotency():
    """Verify batches of 30, live remaining counts, and zero duplicate decisions or audits on retry."""
    from app.models import AuditEvent

    engine, SessionFactory = _make_session_factory()
    try:
        with SessionFactory() as db:
            admin = _seed_admin(db)
            parent = Category(
                code='IMP-IDEM-ROOT',
                name_en='Architectural Finishes',
                normalized_name='architectural finishes',
                active=True,
            )
            db.add(parent)
            db.flush()
            # 34 empty subcategories + 1 empty root = 35 valid empty categories -> 2 batches (30 + 5)
            created_ids = [parent.id]
            for i in range(34):
                sub = Category(
                    code=f'IMP-IDEM-SUB-{i:03d}',
                    name_en=f'Valid Empty Subcategory {i:03d}',
                    normalized_name=f'valid empty subcategory {i:03d}',
                    parent_id=parent.id,
                    active=True,
                )
                db.add(sub)
                db.flush()
                created_ids.append(sub.id)
            db.commit()

        # First execution: 2 batches (30 + 5)
        res1 = run_bulk_approve_valid(
            actor=admin,
            dry_run=False,
            include_empty=True,
            batch_size=30,
            session_factory=SessionFactory,
        )
        assert res1['batches_executed'] == 2
        assert res1['total_eligible'] == 35
        assert res1['retained_empty'] == 35
        assert res1['skipped_already_approved'] == 0
        assert res1['remaining_valid'] == 0
        assert res1['has_more'] is False

        # Second execution (retry): must be completely idempotent
        res2 = run_bulk_approve_valid(
            actor=admin,
            dry_run=False,
            include_empty=True,
            batch_size=30,
            session_factory=SessionFactory,
        )
        assert res2['retained_empty'] == 0
        assert res2['batches_executed'] == 0
        assert res2['remaining_valid'] == 0

        # Verify exact decision & audit counts (no duplicates)
        with SessionFactory() as verify_db:
            decisions = verify_db.scalars(select(CategoryReviewDecision)).all()
            audits = verify_db.scalars(
                select(AuditEvent).where(AuditEvent.action == 'category.bulk_retained_empty')
            ).all()
            assert len(decisions) == 35
            assert len(audits) == 2
            assert sum(a.metadata_json['count'] for a in audits) == 35
    finally:
        engine.dispose()


def test_distinct_hierarchy_and_review_issue_classification():
    """Verify missing_parent, archived_parent, hierarchy_depth_exceeded, duplicate, empty,
    missing_discipline, unsupported_discipline, and suspicious label are all distinguished."""
    engine, SessionFactory = _make_session_factory()
    try:
        with SessionFactory() as db:
            admin = _seed_admin(db)

            # 1. Hierarchy depth > 2 (Discipline -> Main Category -> Subcategory)
            civil_root = Category(code='ROOT-CIV', name_en='Civil', normalized_name='civil', active=True)
            elec_root = Category(code='ROOT-ELC', name_en='Electrical', normalized_name='electrical', active=True)
            archived_parent = Category(code='ARCH-PAR', name_en='Old Archived Parent', normalized_name='old archived parent', active=False)
            valid_main = Category(code='VALID-MAIN', name_en='Structural Works', normalized_name='structural works', active=True)
            qa_cat = Category(code='QA-CAT', name_en='Bulk QA Sample', normalized_name='bulk qa sample', active=True)
            db.add_all([civil_root, elec_root, archived_parent, valid_main, qa_cat])
            db.flush()

            arch_ins = Category(
                code='LVL2-ARCH',
                name_en='Architectural Insulation & Air/Vapor Control',
                normalized_name='architectural insulation & air/vapor control',
                parent_id=civil_root.id,
                active=True,
            )
            elec_lvl2 = Category(
                code='LVL2-ELC',
                name_en='Electrical',
                normalized_name='electrical',
                parent_id=elec_root.id,
                active=True,
            )
            db.add_all([arch_ins, elec_lvl2])
            db.flush()

            acoustic_lvl3 = Category(
                code='LVL3-ACOUSTIC',
                name_en='Acoustic Products',
                normalized_name='acoustic products',
                parent_id=arch_ins.id,
                active=True,
            )
            acc_lvl3 = Category(
                code='LVL3-ACC',
                name_en='Accessories',
                normalized_name='accessories',
                parent_id=elec_lvl2.id,
                active=True,
            )
            child_of_archived = Category(
                code='SUB-ARCH-PAR',
                name_en='Orphan Under Archived Parent',
                normalized_name='orphan under archived parent',
                parent_id=archived_parent.id,
                active=True,
            )
            # Missing parent (dangling FK simulated by disabling FK checks or using non-existent id if allowed)
            # SQLite in-memory has FK off by default unless PRAGMA foreign_keys=ON
            child_missing_parent = Category(
                code='SUB-MISS-PAR',
                name_en='Orphan Missing Parent',
                normalized_name='orphan missing parent',
                parent_id='non-existent-parent-uuid',
                active=True,
            )
            # Valid empty category
            valid_empty = Category(
                code='VALID-EMPTY',
                name_en='Precast Concrete Elements',
                normalized_name='precast concrete elements',
                parent_id=valid_main.id,
                active=True,
            )
            # Duplicate categories under valid_main
            dup1 = Category(code='DUP-1', name_en='Waterproofing Membranes', normalized_name='waterproofing membranes', parent_id=valid_main.id, active=True)
            dup2 = Category(code='DUP-2', name_en='Waterproofing Membranes', normalized_name='waterproofing membranes', parent_id=valid_main.id, active=True)
            # Missing discipline & unsupported discipline categories
            miss_disc_cat = Category(code='MISS-DISC', name_en='Reinforcing Steel', normalized_name='reinforcing steel', parent_id=valid_main.id, active=True)
            unsup_disc_cat = Category(code='UNSUP-DISC', name_en='Specialty Coatings', normalized_name='specialty coatings', parent_id=valid_main.id, active=True)
            db.add_all([
                acoustic_lvl3, acc_lvl3, child_of_archived, child_missing_parent,
                valid_empty, dup1, dup2, miss_disc_cat, unsup_disc_cat,
            ])
            db.flush()

            db.add_all([
                Material(
                    code='MAT-MISS-1',
                    category_id=miss_disc_cat.id,
                    main_category_id=valid_main.id,
                    subcategory_id=miss_disc_cat.id,
                    name_en='Deformed Rebar 16mm',
                    unit='TON',
                    discipline=None,
                    active=True,
                ),
                Material(
                    code='MAT-UNSUP-1',
                    category_id=unsup_disc_cat.id,
                    main_category_id=valid_main.id,
                    subcategory_id=unsup_disc_cat.id,
                    name_en='Specialty Coating Product',
                    unit='L',
                    discipline='MarineSpecial',
                    active=True,
                ),
            ])
            db.commit()

            payload = build_analysis_payload(db, limit=100)
            by_id = {item['id']: item for item in payload['items']}

            assert by_id[child_missing_parent.id]['issue_type'] == 'missing_parent'
            assert by_id[child_missing_parent.id]['issue_label'] == 'Missing parent'

            assert by_id[child_of_archived.id]['issue_type'] == 'archived_parent'
            assert by_id[child_of_archived.id]['issue_label'] == 'Archived parent'

            assert by_id[acoustic_lvl3.id]['issue_type'] == 'hierarchy_depth_exceeded'
            assert by_id[acoustic_lvl3.id]['issue_label'] == 'Exceeds two-level hierarchy'
            assert by_id[acoustic_lvl3.id]['hierarchy_path'] == [
                'Civil',
                'Architectural Insulation & Air/Vapor Control',
                'Acoustic Products',
            ]
            assert by_id[acc_lvl3.id]['issue_type'] == 'hierarchy_depth_exceeded'
            assert by_id[acc_lvl3.id]['hierarchy_path'] == ['Electrical', 'Electrical', 'Accessories']

            assert by_id[qa_cat.id]['issue_type'] == 'invalid_import_data'
            assert by_id[dup1.id]['issue_type'] == 'duplicate_category'
            assert by_id[valid_empty.id]['issue_type'] == 'empty_category'
            assert by_id[miss_disc_cat.id]['issue_type'] == 'missing_discipline'
            assert by_id[unsup_disc_cat.id]['issue_type'] == 'unsupported_discipline'

            # Guard: cannot approve or retain_empty categories with hierarchy_depth_exceeded / archived_parent / missing_parent
            for blocked_id in (acoustic_lvl3.id, child_of_archived.id, child_missing_parent.id):
                with pytest.raises(HTTPException) as exc_info:
                    apply_single_category_decision(db, category_id=blocked_id, decision='retain_empty', actor=admin)
                assert exc_info.value.status_code == 422
    finally:
        engine.dispose()
