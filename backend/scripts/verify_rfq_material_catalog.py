"""Read-only verification of the imported reinforcement RFQ catalog.
Run from backend: python scripts/verify_rfq_material_catalog.py
Uses configured PostgreSQL; never repairs or changes master data.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func, text, or_
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine, SessionLocal
from app.models import Category, Material
from app.modules.taxonomy.router import materials
from app import catalog_cache

EXPECTED={'Reinforcing Steel':18,'Reinforcement Accessories':8,'Post-Tensioning & Prestressing':2}

def main():
    if engine.dialect.name != 'postgresql':
        raise SystemExit('This verification requires the configured PostgreSQL catalog')
    # Bypass response caches so verification exercises real database queries.
    catalog_cache.get=lambda *args,**kwargs: None
    catalog_cache.set=lambda *args,**kwargs: None
    catalog_cache.version=lambda: 'verification'
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        main=db.scalar(select(Category).where(Category.name_en=='Reinforcement & Prestressing',Category.active.is_(True)))
        assert main is not None, 'Main category missing'
        for name,expected in EXPECTED.items():
            sub=db.scalar(select(Category).where(Category.name_en==name,Category.parent_id==main.id,Category.active.is_(True)))
            assert sub is not None, f'Subcategory missing: {name}'
            found=[]
            for offset in range(0,expected+7,7):
                found.extend(materials(q='',main_category_id=main.id,subcategory_id=sub.id,limit=7,offset=offset,autocomplete=True,db=db))
            duplicates=db.execute(select(Material.name_en,Material.unit,func.count()).where(Material.active.is_(True),Material.subcategory_id==sub.id).group_by(Material.name_en,Material.unit).having(func.count()>1)).all()
            mismatches=db.scalar(select(func.count()).select_from(Material).where(Material.active.is_(True),Material.category_id==sub.id,or_(Material.main_category_id!=main.id,Material.main_category_id.is_(None),Material.subcategory_id!=sub.id,Material.subcategory_id.is_(None))))
            print(f'{name}: {len(found)}/{expected}; duplicate description/unit groups: {len(duplicates)}; category mismatches: {mismatches}')
            assert len(found)==expected and len({m['id'] for m in found})==expected
            assert not duplicates and not mismatches
        print('PASS: all 28 materials accessible through exact-subcategory paginated searches')

if __name__=='__main__':
    try:
        main()
    except SQLAlchemyError as exc:
        raise SystemExit(f'Catalog verification unavailable ({type(exc).__name__}); check database connectivity and TLS configuration.') from None
