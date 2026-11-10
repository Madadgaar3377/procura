"""Opt-in live PostgreSQL test for committed direct bulk upload batches.

Run only against a non-production PostgreSQL database. The test deletes its
UUID-tagged records after verifying persistence.
"""
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import AuditEvent, Capability, Category, Contact, ImportBatch, ImportedRow, Material, MaterialRevision, Organization, User
from app.modules.imports import bulk


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="Configured database is not PostgreSQL")
def test_postgresql_persists_and_reuses_records_across_35_row_batches():
    run_id = uuid4().hex[:12]
    main_name, sub_name = f"Bulk QA {run_id}", f"Materials {run_id}"
    emails = [f"bulk-qa-{run_id}-{i:02d}@example.invalid" for i in range(50)]
    batch_ids = []
    with SessionLocal() as db:
        admin_id = db.scalar(select(User.id).where(User.is_admin.is_(True)).limit(1))
        if not admin_id: pytest.fail("PostgreSQL smoke test requires an existing admin account")
    try:
        materials = [(i + 2, {"Discipline":"Civil", "Main Category":main_name, "Subcategory":sub_name,
            "Description":f"Bulk QA {run_id} material {i:03d}", "Unit":"PCS"}) for i in range(100)]
        material_events = [json.loads(item) for item in bulk._stream_import("materials", materials, "postgres-materials.xlsx", admin_id)]
        batch_ids.append(material_events[0]["batch_id"])
        material_result = material_events[-1]
        assert material_result["imported"] == 100 and material_result["failed"] == 0
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Material).where(Material.import_batch_id == material_result["batch_id"])) == 100

        suppliers = []
        for i in range(100):
            supplier_index = i // 2
            suppliers.append((i + 2, {"Company Name":f"Bulk QA {run_id} supplier {supplier_index:02d}", "Contact Person":"QA Contact",
                "Email":emails[supplier_index], "Phone":f"+1555{int(run_id[:6],16):06d}{supplier_index:03d}", "Country":"Saudi Arabia",
                "City":f"Bulk QA {run_id}", "Address":"Temporary smoke-test address", "Main Category":main_name,
                "Subcategory":sub_name, "Material Description":f"Bulk QA {run_id} material {i:03d}", "Unit":"PCS"}))
        supplier_events = [json.loads(item) for item in bulk._stream_import("suppliers", suppliers, "postgres-suppliers.xlsx", admin_id)]
        batch_ids.append(supplier_events[0]["batch_id"])
        supplier_result = supplier_events[-1]
        assert supplier_result["imported"] == 100 and supplier_result["failed"] == 0
        with SessionLocal() as db:
            org_ids = select(Organization.id).where(Organization.business_email.in_(emails))
            assert db.scalar(select(func.count()).select_from(Organization).where(Organization.id.in_(org_ids))) == 50
            assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id.in_(org_ids))) == 50
            assert db.scalar(select(func.count()).select_from(Capability).where(Capability.org_id.in_(org_ids))) == 50
            assert all(len(cap.material_ids or []) == 2 for cap in db.scalars(select(Capability).where(Capability.org_id.in_(org_ids))).all())
    finally:
        with SessionLocal.begin() as db:
            org_ids = select(Organization.id).where(Organization.business_email.in_(emails))
            db.execute(delete(Capability).where(Capability.org_id.in_(org_ids)))
            db.execute(delete(Contact).where(Contact.org_id.in_(org_ids)))
            db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
            material_ids = select(Material.id).where(Material.import_batch_id.in_(batch_ids)) if batch_ids else select(Material.id).where(False)
            db.execute(delete(MaterialRevision).where(MaterialRevision.material_id.in_(material_ids)))
            db.execute(delete(Material).where(Material.id.in_(material_ids)))
            db.execute(delete(ImportedRow).where(ImportedRow.batch_id.in_(batch_ids)))
            db.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_(batch_ids)))
            db.execute(delete(ImportBatch).where(ImportBatch.id.in_(batch_ids)))
            db.execute(delete(Category).where(Category.name_en == sub_name))
            db.execute(delete(Category).where(Category.name_en == main_name))
