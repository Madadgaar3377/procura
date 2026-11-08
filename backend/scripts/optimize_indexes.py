"""Non-destructive index optimization script for PostgreSQL and SQLite.

Run: python backend/scripts/optimize_indexes.py
"""
import sys
from pathlib import Path
from sqlalchemy import text, inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import engine

INDEXES = [
    ("ix_rfq_items_material_id", "rfq_items", "CREATE INDEX IF NOT EXISTS ix_rfq_items_material_id ON rfq_items (material_id)"),
    ("ix_quotations_supplier_status", "quotations", "CREATE INDEX IF NOT EXISTS ix_quotations_supplier_status ON quotations (supplier_org_id, status)"),
    ("ix_notifications_user_status", "notifications", "CREATE INDEX IF NOT EXISTS ix_notifications_user_status ON notifications (recipient_user_id, status)"),
    ("ix_materials_active_unit", "materials", "CREATE INDEX IF NOT EXISTS ix_materials_active_unit ON materials (active, unit)"),
    ("ix_materials_active_cats", "materials", "CREATE INDEX IF NOT EXISTS ix_materials_active_cats ON materials (active, main_category_id, subcategory_id)"),
]

def main():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    print(f"Connected to database dialect: {engine.dialect.name}")
    created = 0
    with engine.begin() as conn:
        for idx_name, table_name, sql in INDEXES:
            if table_name not in existing_tables:
                print(f"Skipping {idx_name}: table {table_name} does not exist.")
                continue
            existing_indexes = {idx['name'] for idx in inspector.get_indexes(table_name)}
            if idx_name in existing_indexes:
                print(f"Index {idx_name} on {table_name} already exists.")
                continue
            print(f"Creating index {idx_name} on {table_name}...")
            conn.execute(text(sql))
            created += 1

    print(f"Index optimization complete: {created} indexes added or verified.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
