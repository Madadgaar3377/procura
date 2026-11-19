"""Read-only duplicate category report; never repairs the configured database."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import SessionLocal,engine
from app.modules.taxonomy.duplicates import preview_duplicates

if __name__=='__main__':
    try:
        with SessionLocal() as db:
            if engine.dialect.name=='postgresql':db.execute(text('SET TRANSACTION READ ONLY'))
            report=preview_duplicates(user=None,db=db)
            print(json.dumps({'duplicate_groups':len(report),'groups':report},indent=2,ensure_ascii=True))
    except SQLAlchemyError as exc:
        raise SystemExit(f'Read-only duplicate report unavailable ({type(exc).__name__}); check database connectivity/TLS.') from None
