"""Read-only, contact-free evidence for the reported Distribution Panels RFQ."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.database import engine

RFQ_ID = 'f1eeb71e-0858-41d7-937d-1daffafecc81'
queries = {
    'schema': "SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_name IN ('supplier_capabilities','categories','materials','rfq_items','rfq_matches','rfq_invitations','imported_supplier_trades') ORDER BY table_name,ordinal_position",
    'rfq': 'SELECT id,reference,buyer_org_id,title,city,status,response_deadline FROM rfqs WHERE id=:id',
    'items': 'SELECT * FROM rfq_items WHERE rfq_id=:id',
    'categories': "SELECT id,code,name_en,parent_id,active FROM categories WHERE lower(name_en) LIKE '%distribution%' OR lower(name_en) LIKE '%electrical%' OR lower(name_en) LIKE '%ups%'",
    'suppliers': "SELECT id,name,is_supplier,is_active,verified_status,city FROM organizations WHERE lower(name) LIKE '%abb%' OR lower(name) LIKE '%borri%' OR lower(name) LIKE '%tecon%'",
    'capabilities': "SELECT c.*,cat.name_en,cat.parent_id FROM supplier_capabilities c JOIN categories cat ON cat.id=c.category_id JOIN organizations o ON o.id=c.org_id WHERE lower(o.name) LIKE '%abb%' OR lower(o.name) LIKE '%borri%' OR lower(o.name) LIKE '%tecon%'",
    'source_trades': "SELECT t.id,t.org_id,t.raw_trade,t.raw_brands,t.raw_location,t.mapping_status FROM imported_supplier_trades t JOIN organizations o ON o.id=t.org_id WHERE lower(o.name) LIKE '%abb%' OR lower(o.name) LIKE '%borri%' OR lower(o.name) LIKE '%tecon%'",
    'materials': 'SELECT m.id,m.name_en,m.category_id,m.main_category_id,m.subcategory_id,m.active,m.specs_template FROM materials m WHERE m.id IN (SELECT material_id FROM rfq_items WHERE rfq_id=:id)',
    'matches': 'SELECT m.*,o.name FROM rfq_matches m JOIN organizations o ON o.id=m.supplier_org_id WHERE rfq_id=:id',
    'invitations': 'SELECT i.*,o.name,q.status AS quotation_status FROM rfq_invitations i JOIN organizations o ON o.id=i.supplier_org_id LEFT JOIN quotations q ON q.invitation_id=i.id WHERE i.rfq_id=:id',
    'jobs': "SELECT id,event,status,attempts,last_error,payload FROM outbox_events WHERE payload->>'rfq_id'=:id ORDER BY created_at DESC LIMIT 30",
}
with engine.connect() as connection:
    connection.execute(text('SET TRANSACTION READ ONLY'))
    for label, sql in queries.items():
        print(label, json.dumps([dict(row) for row in connection.execute(text(sql), {'id': RFQ_ID}).mappings()], default=str))
