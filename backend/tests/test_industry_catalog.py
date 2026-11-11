from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Category, Material
from app.modules.taxonomy.router import industry_catalog, industry_review_candidates


def test_industry_catalog_uses_active_taxonomy_and_batched_material_previews():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        civil = Category(code='ARCH', name_en='Architectural Specialties', name_ar='الأعمال المعمارية', active=True)
        electrical = Category(code='WIRING-MAIN', name_en='Electrical Distribution', active=True)
        mechanical = Category(code='HVAC-MAIN', name_en='HVAC Equipment', active=True)
        qa = Category(code='BULK-QA', name_en='Bulk QA 060', active=True)
        db.add_all([civil, electrical, mechanical, qa])
        db.flush()
        metalwork = Category(code='METALWORK', name_en='Architectural Metalwork',
                             name_ar='الأعمال المعدنية', parent_id=civil.id, active=True)
        wiring = Category(code='WIRING', name_en='Wiring Devices', parent_id=electrical.id, active=True)
        mismatch = Category(code='MISMATCH', name_en='Mislinked subcategory', parent_id=electrical.id, active=True)
        inactive = Category(code='OLD', name_en='Inactive Category', parent_id=civil.id, active=False)
        qa_child = Category(code='QA-CHILD', name_en='QA Sample', parent_id=qa.id, active=True)
        db.add_all([metalwork, wiring, mismatch, inactive, qa_child])
        db.flush()
        db.add_all([
            Material(code='C-1', category_id=metalwork.id, main_category_id=civil.id,
                     subcategory_id=metalwork.id, name_en='Gates and louvers',
                     description='Exterior access assemblies', unit='PCS', discipline='Civil', active=True),
            Material(code='C-2', category_id=metalwork.id, main_category_id=civil.id,
                     subcategory_id=metalwork.id, name_en='Handrails and balustrades',
                     description='Stainless steel guardrails', unit='M', discipline='Civil', active=True),
            Material(code='C-3', category_id=metalwork.id, main_category_id=civil.id,
                     subcategory_id=metalwork.id, name_en='Archived material', active=False),
            Material(code='E-1', category_id=wiring.id, main_category_id=electrical.id,
                     subcategory_id=wiring.id, name_en='Switches and sockets', unit='PCS',
                     discipline='Electrical', active=True),
            Material(code='Q-1', category_id=qa_child.id, main_category_id=qa.id,
                     subcategory_id=qa_child.id, name_en='QA material', active=True),
            Material(code='M-1', category_id=mismatch.id, main_category_id=civil.id,
                     subcategory_id=mismatch.id, name_en='Mislinked material',
                     discipline='Civil', active=True),
        ])
        db.flush()

        page = industry_catalog(q='', discipline=None, page=1, limit=1, db=db)
        assert [item['name_en'] for item in page['disciplines']] == ['Civil', 'Electrical', 'Mechanical']
        assert page['total'] == 1
        assert page['categories'][0]['name_en'] == 'Architectural Metalwork'
        assert page['categories'][0]['material_count'] == 2
        assert len(page['categories'][0]['materials']) == 2
        assert page['categories'][0]['materials'][0]['description'] in {
            'Exterior access assemblies', 'Stainless steel guardrails'
        }
        assert page['categories'][0]['materials'][0]['unit'] in {'PCS', 'M'}

        electrical_page = industry_catalog(q='', discipline='Electrical', page=1, limit=9, db=db)
        assert electrical_page['categories'][0]['name_en'] == 'Wiring Devices'
        assert electrical_page['categories'][0]['material_count'] == 1

        filtered = industry_catalog(q='balustrades', discipline='Civil', page=1, limit=9, db=db)
        assert filtered['total'] == 1
        assert filtered['categories'][0]['id'] == metalwork.id

        review = industry_review_candidates(SimpleNamespace(is_admin=True), db, limit=100, offset=0)
        assert {row['id'] for row in review} == {qa.id, qa_child.id, mechanical.id, mismatch.id}
    engine.dispose()
