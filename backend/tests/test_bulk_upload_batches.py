import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Capability, Category, Contact, ImportBatch, Material, MaterialRevision, Organization, User
from app.modules.imports import bulk


def test_direct_upload_commits_100_rows_in_35_row_batches_and_retries_safely(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(bulk, "SessionLocal", sessions)
    try:
        with Session(engine) as db:
            admin = User(username="batch-import-admin", phone="+966500000099", phone_e164="+966500000099", password_hash="x", is_admin=True)
            db.add(admin); db.commit(); admin_id = admin.id

        material_rows = [(i + 2, {"Discipline":"Civil", "Main Category":"Bulk Test Main", "Subcategory":"Bulk Test Sub",
            "Description":f"Bulk test material {i:03d}", "Unit":"PCS"}) for i in range(100)]
        material_events = [json.loads(line) for line in bulk._stream_import("materials", material_rows, "materials.xlsx", admin_id)]
        progress = [event for event in material_events if event["type"] == "progress"]
        assert [event["completed_batches"] for event in progress] == [1, 2, 3]
        assert [event["progress_percent"] for event in progress] == [33.33, 66.67, 100.0]
        assert material_events[-1]["imported"] == 100

        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Material)) == 100
            assert db.scalar(select(func.count()).select_from(MaterialRevision)) == 100

        supplier_rows = []
        for i in range(100):
            supplier_index = i // 2
            supplier_rows.append((i + 2, {"Company Name":f"Bulk test supplier {supplier_index:02d}", "Contact Person":"Test Contact",
                "Email":f"bulk-test-{supplier_index:02d}@example.invalid", "Phone":f"+966500000{supplier_index:03d}", "Country":"Saudi Arabia",
                "City":"Riyadh", "Address":"Test address", "Main Category":"Bulk Test Main", "Subcategory":"Bulk Test Sub",
                "Material Description":f"Bulk test material {i:03d}", "Unit":"PCS"}))
        supplier_events = [json.loads(line) for line in bulk._stream_import("suppliers", supplier_rows, "suppliers.xlsx", admin_id)]
        assert supplier_events[-1]["imported"] == 100
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Organization).where(Organization.is_supplier.is_(True))) == 50
            assert db.scalar(select(func.count()).select_from(Contact)) == 50
            assert db.scalar(select(func.count()).select_from(Capability)) == 50
            assert all(len(cap.material_ids) == 2 for cap in db.scalars(select(Capability)).all())

        retry_events = [json.loads(line) for line in bulk._stream_import("materials", material_rows, "materials-retry.xlsx", admin_id)]
        assert retry_events[-1]["imported"] == 0
        assert retry_events[-1]["skipped"] == 100
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Material)) == 100
    finally:
        engine.dispose()
