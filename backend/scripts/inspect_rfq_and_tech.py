import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    print("=== RFQ DETAILS ===")
    rfqs = conn.execute(text("""
        SELECT id, reference, buyer_org_id, title, city, status, created_at, response_deadline
        FROM rfqs
        WHERE id IN ('60f7f99b-ae7e-4647-af98-d40a4fd014f2', 'f2d257c3-52f0-4802-9df5-cc398e228209')
        ORDER BY created_at DESC
    """)).mappings().all()
    for r in rfqs:
        print("RFQ:", dict(r))
        items = conn.execute(text("""
            SELECT id, rfq_id, material_id, main_category_id, subcategory_id, description, unit, quantity
            FROM rfq_items
            WHERE rfq_id = :rfq_id
        """), {"rfq_id": r['id']}).mappings().all()
        for it in items:
            print("  Item:", dict(it))
            # Check category details
            if it['main_category_id']:
                main = conn.execute(text("SELECT id, name_en, parent_id FROM categories WHERE id = :id"), {"id": it['main_category_id']}).mappings().first()
                print("    Main Cat:", dict(main) if main else "Not found")
            if it['subcategory_id']:
                sub = conn.execute(text("SELECT id, name_en, parent_id FROM categories WHERE id = :id"), {"id": it['subcategory_id']}).mappings().first()
                print("    Sub Cat:", dict(sub) if sub else "Not found")

    print("\n=== TECH ORGANIZATION & CAPABILITIES ===")
    tech_org = conn.execute(text("SELECT * FROM organizations WHERE name = 'Tech'")).mappings().first()
    print("Tech Org:", dict(tech_org) if tech_org else "Tech not found")
    
    if tech_org:
        tech_caps = conn.execute(text("""
            SELECT sc.id, sc.category_id, sc.city, sc.confirmed, sc.accepts_rfqs, sc.eligibility_status, sc.qualifications,
                   c.name_en as cat_name, c.parent_id as cat_parent_id
            FROM supplier_capabilities sc
            LEFT JOIN categories c ON c.id = sc.category_id
            WHERE sc.org_id = :org_id
        """), {"org_id": tech_org['id']}).mappings().all()
        print(f"Tech has {len(tech_caps)} capabilities:")
        for tc in tech_caps:
            print("  Cap:", dict(tc))
