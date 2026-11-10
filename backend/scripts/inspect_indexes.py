import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text("""
        SELECT tablename, indexname, indexdef 
        FROM pg_indexes 
        WHERE tablename IN ('supplier_capabilities', 'rfq_invitations')
        ORDER BY tablename, indexname;
    """)).all()
    for row in res:
        print(f"{row[0]} | {row[1]} | {row[2]}")
