"""Read-only production inspection; save source rows locally for isolated replay.

Never modifies the configured database and never prints contact data.
"""
import json
from pathlib import Path
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Category, ImportBatch, ImportedRow

with SessionLocal() as db:
    batches = db.scalars(select(ImportBatch).where(ImportBatch.kind == 'supplier_bulk')).all()
    recovered = []
    for batch in batches:
        rows = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch.id).order_by(ImportedRow.row_number)).all()
        source = [entry for row in rows for entry in (row.raw or {}).get('source_rows', [])]
        print(json.dumps({'batch_id': batch.id, 'source_rows': len(source), 'metadata': {
            key: (batch.preview_metadata or {}).get(key) for key in ['capabilities_saved', 'capabilities_skipped', 'pending_category_mappings']}}))
        recovered.extend(source)
    target = Path('../.test-runtime/recovered-supplier-source.json')
    target.write_text(json.dumps(recovered, ensure_ascii=False), encoding='utf-8')
    print('Recovered source rows:', len(recovered))
    categories = db.scalars(select(Category)).all()
    Path('../.test-runtime/recovered-supplier-taxonomy.json').write_text(json.dumps([
        {key: getattr(category, key) for key in ['id', 'code', 'name_en', 'normalized_name', 'parent_id', 'active']}
        for category in categories], ensure_ascii=False), encoding='utf-8')
    print('Taxonomy categories:', len(categories))
