import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Capability, Category, Contact, ImportBatch, ImportedSupplierTrade, Material, MaterialRevision, Organization, User
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
        assert supplier_events[-1]["new_suppliers_saved"] == 50
        assert supplier_events[-1]["capabilities_saved"] == 100
        assert supplier_events[-1]["contacts_created"] == 50
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Organization).where(Organization.is_supplier.is_(True))) == 50
            assert db.scalar(select(func.count()).select_from(Contact)) == 50
            assert db.scalar(select(func.count()).select_from(Capability)) == 50
            assert all(len(cap.material_ids) == 2 for cap in db.scalars(select(Capability)).all())

        supplier_retry = [json.loads(line) for line in bulk._stream_import("suppliers", supplier_rows, "suppliers-retry.xlsx", admin_id)]
        assert supplier_retry[-1]["new_suppliers_saved"] == 0
        assert supplier_retry[-1]["existing_suppliers_reused"] == 50
        assert supplier_retry[-1]["contacts_created"] == 0
        assert supplier_retry[-1]["capabilities_saved"] == 0

        retry_events = [json.loads(line) for line in bulk._stream_import("materials", material_rows, "materials-retry.xlsx", admin_id)]
        assert retry_events[-1]["imported"] == 0
        assert retry_events[-1]["skipped"] == 100
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Material)) == 100
    finally:
        engine.dispose()


def test_supplier_upload_imports_incomplete_contacts_and_reports_missing_fields(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(bulk, "SessionLocal", sessions)
    try:
        with Session(engine) as db:
            admin = User(username="incomplete-import-admin", phone="+966500000098", phone_e164="+966500000098", password_hash="x", is_admin=True)
            main = Category(code="INCOMPLETE", name_en="Incomplete Main", normalized_name="incomplete main", active=True)
            db.add_all([admin, main]); db.flush()
            sub = Category(code="INCOMPLETE-SUB", name_en="Incomplete Sub", normalized_name="incomplete sub", parent_id=main.id, active=True)
            db.add(sub); db.flush()
            material = Material(code="INCOMPLETE-1", main_category_id=main.id, subcategory_id=sub.id,
                category_id=sub.id, name_en="Imported test material", normalized_description="imported test material", unit="PCS")
            db.add(material); db.commit(); admin_id = admin.id; material_id = material.id

        base = {"Country":"Saudi Arabia", "Main Category":"Incomplete Main", "Subcategory":"Incomplete Sub",
            "Material Description":"Imported test material", "Unit":"PCS"}
        rows = [
            (2, {**base, "Company Name":"Both Co", "Email":"both@example.com", "Phone":"+966500000001", "City":"Riyadh", "Address":"Riyadh address", "Contact Person":"A"}),
            (3, {**base, "Company Name":"Phone Co", "Phone":"+966500000002", "City":"Jeddah"}),
            (4, {**base, "Company Name":"Email Co", "Email":"email@example.com", "City":"Dammam"}),
            (5, {**base, "Company Name":"Neither Co", "Material Description":""}),
            (6, {**base, "Phone":"+966500000006", "City":"Tabuk", "Main Category":"Low Current (ELV)", "Subcategory":"LC"}),
            (7, {**base, "Email":"bad-email"}),
        ]
        events = [json.loads(line) for line in bulk._stream_import("suppliers", rows, "incomplete.xlsx", admin_id)]
        result = events[-1]
        assert result["total_rows"] == 6 and result["new_suppliers_saved"] == 5 and result["suppliers_incomplete"] == 4
        assert result["failed"] == 1 and result["supplier_failures"] == 1
        assert result["capabilities_saved"] == 3 and result["capabilities_skipped"] == 2
        assert "Email missing" in result["incomplete_rows"][0]["reason"]
        assert "Phone missing" in result["incomplete_rows"][1]["reason"]
        assert "Company Name" in result["incomplete_rows"][3]["reason"]
        with Session(engine) as db:
            orgs = db.scalars(select(Organization).where(Organization.is_supplier.is_(True))).all()
            assert len(orgs) == 5
            no_contact = next(org for org in orgs if org.name == "Neither Co")
            assert no_contact.business_email is None and no_contact.business_phone is None
            no_name = next(org for org in orgs if org.name.startswith("[Imported supplier placeholder:"))
            assert no_name.verified_status == "unverified" and no_name.profile_status == "pending_onboarding"
            assert db.scalar(select(func.count()).select_from(Capability)) == 3
            assert all(material_id in (cap.material_ids or []) for cap in db.scalars(select(Capability)).all())
            assert db.scalar(select(func.count()).select_from(ImportedSupplierTrade).where(ImportedSupplierTrade.raw_type == "Low Current (ELV) / LC")) == 1
    finally:
        engine.dispose()


def test_supplier_contact_parsers_split_emails_and_keep_fax_separate():
    emails = bulk._supplier_emails("sales@example.com / info@example.com;invalid-address")
    assert [item["value"] for item in emails] == ["sales@example.com", "info@example.com", "invalid-address"]
    assert [item["valid"] for item in emails] == [True, True, False]

    phones = bulk._supplier_phones("T: tel:+966-13-8872513 F: (+966) 13-8872514")
    assert len(phones) == 2
    assert phones[0]["normalized"] == "+966138872513" and not phones[0]["fax"]
    assert phones[1]["normalized"] == "+966138872514" and phones[1]["fax"]


def test_supplier_contact_rows_group_company_and_keep_fax_invalid_contact_and_pending_trade(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(bulk, "SessionLocal", sessions)
    try:
        with Session(engine) as db:
            admin = User(username="supplier-contact-import-admin", phone="+966500000097", phone_e164="+966500000097", password_hash="x", is_admin=True)
            db.add(admin); db.commit(); admin_id = admin.id
        common = {"Company Name":"Signify", "Contact Person":"Export desk", "Country":"Saudi Arabia",
            "City":"Riyadh", "Address":"Business district", "Main Category":"Electrical",
            "Subcategory":"UPS", "Material Description":"", "Unit":""}
        rows = [
            (27, {**common, "Email":"sales@example.com / info@example.com;bad-address", "Phone":"F: +966-13-8872513"}),
            (28, {**common, "Email":"support@example.com", "Phone":"tel:(966) 55 123 4567",
                "Subcategory":"LC/CCTV", "Material Description":"Security systems"}),
        ]
        result = [json.loads(line) for line in bulk._stream_import("suppliers", rows, "supplier-contacts.xlsx", admin_id)][-1]
        assert result["total_supplier_records_identified"] == 1
        assert result["new_suppliers_saved"] == 1 and result["existing_suppliers_reused"] == 0
        assert result["contacts_created"] == 5
        assert result["capabilities_saved"] == 0 and result["capabilities_skipped"] == 2
        assert result["supplier_failures"] == 0 and len(result["warnings"]) == 2
        with Session(engine) as db:
            org = db.scalar(select(Organization).where(Organization.name == "Signify"))
            assert org and org.verified_status == "unverified" and org.profile_status == "pending_onboarding"
            assert org.business_email == "sales@example.com" and org.business_phone == "+966551234567"
            assert db.scalar(select(func.count()).select_from(Organization).where(Organization.name == "Signify")) == 1
            assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id == org.id)) == 5
            assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id == org.id, Contact.fax == "+966-13-8872513")) == 1
            assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id == org.id, Contact.needs_review.is_(True))) == 1
            assert db.scalar(select(func.count()).select_from(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id == org.id)) == 1
    finally:
        engine.dispose()
