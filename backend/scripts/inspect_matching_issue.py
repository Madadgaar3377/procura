import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import engine
from sqlalchemy import text

def inspect():
    with engine.connect() as conn:
        print("=== ORGANIZATIONS ===")
        orgs = conn.execute(text("""
            SELECT id, name, name_normalized, is_buyer, is_supplier, verified_status, is_active, city, country
            FROM organizations
            WHERE name ILIKE '%Madadgaar%' OR name ILIKE '%Tech%'
        """)).mappings().all()
        for o in orgs:
            print(dict(o))
        
        print("\n=== CATEGORIES ===")
        cats = conn.execute(text("""
            SELECT id, code, name_en, name_ar, parent_id, active
            FROM categories
            WHERE name_en ILIKE '%Architectural%' OR name_en ILIKE '%Toilet%' OR name_en ILIKE '%Mirror%'
        """)).mappings().all()
        for c in cats:
            print(dict(c))

        print("\n=== RECENT RFQS ===")
        rfqs = conn.execute(text("""
            SELECT id, reference, buyer_org_id, title, city, status, created_at, response_deadline
            FROM rfqs
            ORDER BY created_at DESC
            LIMIT 5
        """)).mappings().all()
        for r in rfqs:
            print(dict(r))

        if rfqs:
            latest_rfq_id = rfqs[0]['id']
            print(f"\n=== RFQ ITEMS FOR LATEST RFQ ({latest_rfq_id}) ===")
            items = conn.execute(text("""
                SELECT id, rfq_id, material_id, main_category_id, subcategory_id, description, unit, quantity
                FROM rfq_items
                WHERE rfq_id = :rfq_id
            """), {"rfq_id": latest_rfq_id}).mappings().all()
            for it in items:
                print(dict(it))

        print("\n=== CAPABILITIES FOR TECH ORGS ===")
        tech_org_ids = [o['id'] for o in orgs if 'tech' in o['name'].lower()]
        for tid in tech_org_ids:
            caps = conn.execute(text("""
                SELECT id, org_id, category_id, city, confirmed, accepts_rfqs, eligibility_status, qualifications, material_ids
                FROM supplier_capabilities
                WHERE org_id = :org_id
            """), {"org_id": tid}).mappings().all()
            print(f"Tech Org {tid} has {len(caps)} capabilities:")
            for cap in caps[:10]:
                print("  ", dict(cap))
            if len(caps) > 10:
                print(f"   ... and {len(caps)-10} more")

        print("\n=== OUTBOX EVENTS RECENT ===")
        events = conn.execute(text("""
            SELECT id, event, status, attempts, last_error, payload, created_at
            FROM outbox_events
            ORDER BY created_at DESC
            LIMIT 10
        """)).mappings().all()
        for ev in events:
            print(dict(ev))

        print("\n=== RFQ MATCHES ===")
        matches = conn.execute(text("""
            SELECT id, rfq_id, supplier_org_id, score, status, reasons, created_at
            FROM rfq_matches
            ORDER BY created_at DESC
            LIMIT 10
        """)).mappings().all()
        for m in matches:
            print(dict(m))

        print("\n=== RFQ INVITATIONS ===")
        invitations = conn.execute(text("""
            SELECT id, rfq_id, supplier_org_id, status, created_at
            FROM rfq_invitations
            ORDER BY created_at DESC
            LIMIT 10
        """)).mappings().all()
        for inv in invitations:
            print(dict(inv))

if __name__ == "__main__":
    inspect()
