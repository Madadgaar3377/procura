"""Read-only, aggregate inspection of the configured Granada Park RFQ."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text
from app.config import get_settings, normalize_database_url
from app.modules.rfqs.material_matching import extract_keywords

RFQ_ID = '57eeb074-91d4-44da-94fc-89deb94e3527'

engine = create_engine(normalize_database_url(get_settings().database_url),
                       connect_args={'connect_timeout': 5}, hide_parameters=True)
with engine.connect() as db:
    if 'one_word_matches' in sys.argv:
        wanted = extract_keywords('RGB Flood Light')
        rows = db.execute(text("SELECT raw_trade FROM imported_supplier_trades WHERE org_id='040c16aa-7722-4642-829c-a0ecb34fc1b8' AND mapping_status='mapped'")).scalars()
        matches = [(part[:100], sorted(wanted & extract_keywords(part))) for raw in rows for part in raw.split(';') if len(wanted & extract_keywords(part)) >= 2]
        print('one_word_matches', matches[:20])
    for label, sql in [
        ('rfq', "SELECT id, project_name, city, status, response_deadline FROM rfqs WHERE id=:id"),
        ('items', "SELECT id, description, material_id, main_category_id, subcategory_id, quantity, unit FROM rfq_items WHERE rfq_id=:id"),
        ('matches', "SELECT status, count(*) AS n FROM rfq_matches WHERE rfq_id=:id GROUP BY status"),
        ('invitations', "SELECT status, count(*) AS n FROM rfq_invitations WHERE rfq_id=:id GROUP BY status"),
        ('jobs', "SELECT status, count(*) AS n FROM outbox_events WHERE event='rfq.published' AND payload->>'rfq_id'=:id GROUP BY status"),
        ('lighting_categories', "SELECT id, name_en, parent_id FROM categories WHERE active AND (name_en ILIKE '%light%' OR name_en ILIKE '%electri%') LIMIT 30"),
        ('requested_category', "SELECT s.id, s.name_en, s.parent_id, m.name_en AS parent_name FROM categories s LEFT JOIN categories m ON m.id=s.parent_id WHERE s.id='a7b26ae2-056f-4876-9a85-b80e5be01c5f'"),
        ('material_hits', "SELECT id, name_en, main_category_id, subcategory_id, source_sn FROM materials WHERE name_en ILIKE '%flood%light%' LIMIT 30"),
        ('category_stats', "SELECT c.name_en, count(*) FROM supplier_capabilities sc JOIN categories c ON c.id=sc.category_id WHERE c.parent_id='65e68686-d8b4-45af-b8b1-ba10b86b0789' GROUP BY c.name_en ORDER BY count(*) DESC LIMIT 20"),
        ('flood_category', "SELECT s.id, s.name_en, p.name_en FROM categories s LEFT JOIN categories p ON p.id=s.parent_id WHERE s.id='acec6422-9722-4ce2-bcce-b2318aafe27a'"),
        ('flood_capabilities', "SELECT c.org_id, o.name, c.city, c.mapping_source FROM supplier_capabilities c JOIN organizations o ON o.id=c.org_id WHERE c.category_id='acec6422-9722-4ce2-bcce-b2318aafe27a' AND c.confirmed AND c.eligibility_status='enabled' LIMIT 20"),
        ('flood_suppliers_by_city', "SELECT c.city, count(DISTINCT c.org_id) FROM supplier_capabilities c JOIN imported_supplier_trades t ON t.org_id=c.org_id JOIN organizations o ON o.id=c.org_id WHERE c.category_id='a7b26ae2-056f-4876-9a85-b80e5be01c5f' AND c.confirmed AND c.accepts_rfqs AND c.eligibility_status='enabled' AND o.is_supplier AND o.is_active AND t.raw_trade ILIKE '%Flood Light%' GROUP BY c.city ORDER BY count(DISTINCT c.org_id) DESC LIMIT 15"),
        ('trade_statuses', "SELECT mapping_status, count(*) FROM imported_supplier_trades WHERE raw_trade ILIKE '%Flood Light%' GROUP BY mapping_status"),
        ('flood_riyadh_suppliers', "SELECT DISTINCT o.name, c.city FROM supplier_capabilities c JOIN imported_supplier_trades t ON t.org_id=c.org_id JOIN organizations o ON o.id=c.org_id WHERE c.category_id='a7b26ae2-056f-4876-9a85-b80e5be01c5f' AND c.confirmed AND c.accepts_rfqs AND c.eligibility_status='enabled' AND o.is_supplier AND o.is_active AND t.raw_trade ILIKE '%Flood Light%' AND (c.city ILIKE '%Riyadh%' OR c.city='') ORDER BY o.name LIMIT 30"),
        ('one_capability', "SELECT cat.name_en, c.mapping_source, c.city, jsonb_array_length(c.material_ids::jsonb) AS materials FROM supplier_capabilities c JOIN categories cat ON cat.id=c.category_id WHERE c.org_id='040c16aa-7722-4642-829c-a0ecb34fc1b8' AND cat.parent_id='65e68686-d8b4-45af-b8b1-ba10b86b0789' ORDER BY cat.name_en"),
        ('one_trades', "SELECT mapping_status, left(raw_trade, 90), left(raw_products, 90) FROM imported_supplier_trades WHERE org_id='040c16aa-7722-4642-829c-a0ecb34fc1b8' LIMIT 20"),
        ('mapped_flood', "SELECT o.name, t.org_id, left(t.raw_trade,160) FROM imported_supplier_trades t JOIN organizations o ON o.id=t.org_id WHERE t.mapping_status='mapped' AND t.raw_trade ILIKE '%Flood Light%' LIMIT 10"),
        ('one_pricebook', "SELECT p.supplier_description, p.offered_description, p.subcategory_id FROM price_book_items p WHERE p.org_id='040c16aa-7722-4642-829c-a0ecb34fc1b8' AND p.active LIMIT 30"),
        ('one_linked_materials', "SELECT m.name_en, cat.name_en FROM supplier_capabilities c JOIN LATERAL jsonb_array_elements_text(c.material_ids::jsonb) v(id) ON true JOIN materials m ON m.id=v.id JOIN categories cat ON cat.id=m.subcategory_id WHERE c.org_id='040c16aa-7722-4642-829c-a0ecb34fc1b8' AND m.subcategory_id='a7b26ae2-056f-4876-9a85-b80e5be01c5f' AND (m.name_en ILIKE '%rgb%' OR m.name_en ILIKE '%flood%') LIMIT 30"),
        ('one_rgb_trades', "SELECT mapping_status, left(raw_trade,250) FROM imported_supplier_trades WHERE org_id='040c16aa-7722-4642-829c-a0ecb34fc1b8' AND mapping_status='mapped' AND raw_trade ILIKE '%rgb%' LIMIT 20"),
        ('one_rgb_products', "SELECT mapping_status, left(raw_products,250) FROM imported_supplier_trades WHERE org_id='040c16aa-7722-4642-829c-a0ecb34fc1b8' AND mapping_status='mapped' AND (raw_products ILIKE '%rgb%' OR raw_products ILIKE '%flood%') LIMIT 20"),
        ('matching_capabilities', "SELECT c.org_id, o.name, cat.name_en, c.city, c.confirmed, c.eligibility_status, left(c.trade_keywords, 130) FROM supplier_capabilities c JOIN organizations o ON o.id=c.org_id JOIN categories cat ON cat.id=c.category_id WHERE c.category_id IN ('a7b26ae2-056f-4876-9a85-b80e5be01c5f','65e68686-d8b4-45af-b8b1-ba10b86b0789') AND o.is_supplier AND o.is_active LIMIT 40"),
        ('capability_sources', "SELECT mapping_source, count(*) FROM supplier_capabilities WHERE category_id='a7b26ae2-056f-4876-9a85-b80e5be01c5f' GROUP BY mapping_source"),
        ('light_trades', "SELECT t.org_id, o.name, left(t.raw_trade,130), t.raw_location FROM imported_supplier_trades t JOIN organizations o ON o.id=t.org_id WHERE t.raw_trade ILIKE '%light%' LIMIT 30"),
        ('product_hits', "SELECT p.org_id, o.name, p.supplier_description, p.main_category_id, p.subcategory_id FROM price_book_items p JOIN organizations o ON o.id=p.org_id WHERE p.active AND p.supplier_description ILIKE '%flood%light%' LIMIT 30"),
        ('source_product_hits', "SELECT t.org_id, o.name, left(t.raw_products, 160) AS products FROM imported_supplier_trades t JOIN organizations o ON o.id=t.org_id WHERE t.raw_products ILIKE '%flood%light%' LIMIT 30"),
    ]:
        if len(sys.argv) > 1 and label not in sys.argv[1:]:
            continue
        print(label, [tuple(row) for row in db.execute(text(sql), {'id': RFQ_ID}).all()])
