"""Direct, retry-safe spreadsheet imports used from the Materials and Suppliers pages."""
from __future__ import annotations

import re
import json
import unicodedata
from io import BytesIO
from math import ceil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import catalog_cache
from app.database import SessionLocal, get_db
from app.models import Capability, Category, Contact, ImportBatch, ImportedRow, Material, Organization, User
from app.modules.materials.service import create_material_record, normalize_text, normalize_unit, resolve_category
from app.security import admin_user, audit

router = APIRouter(prefix="/bulk", tags=["Bulk Materials and Suppliers"])
MATERIAL_HEADERS = ["Discipline", "Main Category", "Subcategory", "Description", "Unit"]
SUPPLIER_HEADERS = ["Company Name", "Contact Person", "Email", "Phone", "Country", "City", "Address", "Main Category", "Subcategory", "Material Description", "Unit"]
DISCIPLINES = {"civil": "Civil", "electrical": "Electrical", "mechanical": "Mechanical"}
BATCH_SIZE = 35


def _clean(value):
    if value is None: return ""
    if isinstance(value, float) and value.is_integer(): value = int(value)
    return unicodedata.normalize("NFKC", str(value)).strip()


def _read(content: bytes, filename: str, headers: list[str]):
    if not filename.casefold().endswith((".xlsx", ".xls")):
        raise HTTPException(415, "Upload an Excel .xlsx or .xls workbook")
    if len(content) > 8_000_000: raise HTTPException(413, "Maximum workbook size is 8 MB")
    try:
        if filename.casefold().endswith(".xls"):
            import xlrd
            book = xlrd.open_workbook(file_contents=content, on_demand=True)
            sheet = book.sheet_by_index(0)
            values = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
            book.release_resources()
        else:
            book = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
            sheet = book.active
            if sheet.max_row > 30_001: raise HTTPException(413, "Workbook exceeds the 30,000-row limit")
            values = list(sheet.iter_rows(values_only=True))
            book.close()
    except HTTPException: raise
    except ImportError as exc: raise HTTPException(503, "Legacy XLS support is not installed") from exc
    except Exception as exc: raise HTTPException(422, "The uploaded Excel workbook could not be read") from exc
    if not values: raise HTTPException(422, "The workbook is empty")
    actual = [_clean(v).casefold() for v in values[0]]
    expected = [h.casefold() for h in headers]
    if actual[:len(expected)] != expected:
        raise HTTPException(422, "The workbook columns do not match the downloadable template")
    if len(values) > 30_001: raise HTTPException(413, "Workbook exceeds the 30,000-row limit")
    return [(number, dict(zip(headers, (_clean(v) for v in row)))) for number, row in enumerate(values[1:], 2) if any(_clean(v) for v in row)]


def _template(headers, rows, name):
    book = Workbook(); sheet = book.active; sheet.title = name
    sheet.append(headers)
    for row in rows: sheet.append(row)
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(42, max(14, max(len(str(cell.value or "")) for cell in column) + 2))
    out = BytesIO(); book.save(out); out.seek(0)
    return StreamingResponse(out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})


@router.get("/materials/template")
def material_template(user: User = Depends(admin_user)):
    return _template(MATERIAL_HEADERS, [["Civil", "External Works", "Landscaping", "Natural Turf", "M2"], ["Electrical", "Tools", "Installation Tools", "PVC Pipe Bender 32mm", "PCS"], ["Mechanical", "HVAC", "Accessories", "Air Filter", "PCS"]], "materials-template")


@router.get("/suppliers/template")
def supplier_template(user: User = Depends(admin_user)):
    return _template(SUPPLIER_HEADERS, [["ABC Trading", "Ahmed", "info@example.com", "+966500000000", "Saudi Arabia", "Riyadh", "Riyadh", "Electrical", "Tools", "PVC Pipe Bender 32mm", "PCS"]], "suppliers-template")


def _record_issue(db, batch, number, raw, reason):
    db.add(ImportedRow(batch_id=batch.id, row_number=number, raw=raw, status="invalid", issue=reason[:300]))


def _chunks(rows):
    return [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]


def _progress(batch_id, total, index, batch_count, imported, skipped, issues, batch_size=BATCH_SIZE):
    return {"type": "progress", "batch_id": batch_id, "total_rows": total, "batch_size": batch_size,
        "completed_batches": index, "batches_total": batch_count, "imported": imported, "skipped": skipped,
        "failed": len(issues), "progress_percent": round(index / batch_count * 100, 2) if batch_count else 100,
        "issues": issues[-50:]}


def _material_row(db, row, number, batch, user, category_cache, unit_cache, candidate_by_description, seen):
    discipline = DISCIPLINES.get(row["Discipline"].casefold())
    main_name, sub_name, desc, unit = (row[k] for k in MATERIAL_HEADERS[1:])
    issue = ("Discipline must be Civil, Electrical, or Mechanical" if not discipline else
        "Main Category is required" if not main_name else "Subcategory is required" if not sub_name else
        "Description is required" if not desc else "Unit is required" if not unit else "")
    if issue: return "failed", issue
    if len(main_name) > 180 or len(sub_name) > 180: return "failed", "Category names must contain 1 to 180 characters"
    if len(desc) > 250 or len(normalize_text(desc)) < 3: return "failed", "Material description must contain 3 to 250 characters"
    if len(normalize_unit(unit)) > 30: return "failed", "Unit must contain at most 30 characters"
    try:
        main, _ = resolve_category(db, main_name, None, create=True, cache=category_cache)
        sub, _ = resolve_category(db, sub_name, main, create=True, cache=category_cache)
        identity = (main.id, sub.id, discipline, normalize_unit(unit), normalize_text(desc))
        if identity in seen: return "skipped", ""
        if any((m.main_category_id, m.subcategory_id, m.discipline, m.unit, m.normalized_description) == identity
               for m in candidate_by_description.get(normalize_text(desc), ())):
            seen.add(identity); return "skipped", ""
        seen.add(identity)
        create_material_record(db, actor=user, main_category=main, subcategory=sub, description=desc, unit=unit,
            source_unit=unit, discipline=discipline, import_batch_id=batch.id, import_row_number=number,
            unit_cache=unit_cache, create_unknown_unit=True)
        return "imported", ""
    except (HTTPException, ValueError) as exc:
        category_cache.clear(); unit_cache.clear()
        return "failed", str(getattr(exc, "detail", None) or str(exc) or "Database rejected this row")


def _supplier_row(db, row, number, batch, user, org_cache, cap_cache, category_cache, material_cache, seen):
    name, contact_name, email, phone, country, city, address, main_name, sub_name, desc, unit = (row[k] for k in SUPPLIER_HEADERS)
    email_key = email.casefold().strip()
    reason = ("Company Name is required" if not name else "Valid email is required" if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email_key) else
        "Valid phone is required" if not re.fullmatch(r"\+?[0-9 ()-]{8,20}", phone) else "City is required" if not city else
        "Main Category is required" if not main_name else "Subcategory is required" if not sub_name else
        "Material Description is required" if not desc else "Unit is required" if not unit else "")
    if reason: return "failed", reason
    if len(desc) > 250 or len(normalize_text(desc)) < 3: return "failed", "Material description must contain 3 to 250 characters"
    try:
        path_key = (normalize_text(main_name), normalize_text(sub_name))
        if path_key not in category_cache:
            main = db.scalar(select(Category).where(Category.parent_id.is_(None), Category.active.is_(True), Category.normalized_name == path_key[0]))
            category_cache[path_key] = (main, db.scalar(select(Category).where(Category.active.is_(True), Category.normalized_name == path_key[1], Category.parent_id == (main.id if main else None))))
        main, sub = category_cache[path_key]
        if not main or not sub: return "failed", f"Category path not found: {main_name} / {sub_name}"
        material_key = (sub.id, normalize_text(desc), normalize_unit(unit))
        if material_key not in material_cache:
            material_cache[material_key] = db.scalar(select(Material).where(Material.main_category_id == main.id, Material.subcategory_id == sub.id,
                Material.normalized_description == material_key[1], Material.unit == material_key[2], Material.active.is_(True)))
        material = material_cache[material_key]
        if not material: return "failed", f"Material not found in the Materials database: {desc} ({unit})"
        org = org_cache.get(email_key)
        if not org:
            org = db.scalar(select(Organization).where(func.lower(func.trim(Organization.business_email)) == email_key, Organization.is_supplier.is_(True)))
            if not org:
                contact = db.scalar(select(Contact).where(func.lower(func.trim(Contact.email)) == email_key))
                org = db.get(Organization, contact.org_id) if contact else None
            if not org:
                org = Organization(name=name.strip(), name_normalized=" ".join(name.casefold().split())[:220], country=country or None,
                    city=city or None, address=address or None, business_email=email_key, business_phone=phone,
                    contact_name=contact_name or None, source="imported", is_supplier=True, is_buyer=False, verified_status="unverified")
                db.add(org); db.flush()
            org_cache[email_key] = org
            if contact_name or phone or email_key:
                existing_contact = db.scalar(select(Contact.id).where(Contact.org_id == org.id, func.lower(func.trim(Contact.email)) == email_key))
                if not existing_contact: db.add(Contact(org_id=org.id, name=contact_name or None, email=email_key, phone=phone, permission_to_notify=False))
        ident = (org.id, sub.id, city.casefold().strip())
        cap = cap_cache.get(ident)
        if not cap:
            cap = db.scalar(select(Capability).where(Capability.org_id == org.id, Capability.category_id == sub.id,
                func.lower(func.trim(Capability.city)) == ident[2]))
            if not cap:
                cap = Capability(org_id=org.id, category_id=sub.id, city=city.strip(), confirmed=False, accepts_rfqs=False)
                db.add(cap); db.flush()
            cap_cache[ident] = cap
        duplicate = (ident, material.id)
        if duplicate in seen or material.id in (cap.material_ids or []): return "skipped", ""
        cap.material_ids = [*(cap.material_ids or []), material.id]
        seen.add(duplicate)
        return "imported", ""
    except (HTTPException, ValueError) as exc:
        return "failed", str(getattr(exc, "detail", None) or str(exc) or "Database rejected this row")


def _stream_import(kind, rows, filename, user_id):
    chunks = _chunks(rows); batch_count = len(chunks); db = SessionLocal(); issues = []
    imported = skipped = completed_rows = 0; category_cache = {}; unit_cache = {}; seen = set()
    org_cache = {}; cap_cache = {}; supplier_categories = {}; material_cache = {}
    batch = ImportBatch(kind=kind, filename=filename[:200], status="processing", stage="apply", rows=len(rows), initiated_by=user_id)
    try:
        db.add(batch); db.commit(); db.refresh(batch)
        yield json.dumps({**_progress(batch.id, len(rows), 0, batch_count, imported, skipped, issues), "type": "started"}) + "\n"
        for index, chunk in enumerate(chunks, 1):
            before = (imported, skipped, len(issues)); batch_failures = []
            try:
                if kind == "materials":
                    descriptions = {normalize_text(row.get("Description", "")) for _, row in chunk if row.get("Description")}
                    candidates = db.scalars(select(Material).where(Material.normalized_description.in_(descriptions))).all() if descriptions else []
                    by_description = {}
                    for candidate in candidates: by_description.setdefault(candidate.normalized_description or normalize_text(candidate.name_en), []).append(candidate)
                    process = lambda n, r: _material_row(db, r, n, batch, db.get(User, user_id), category_cache, unit_cache, by_description, seen)
                else:
                    needed_paths = {(normalize_text(row["Main Category"]), normalize_text(row["Subcategory"])) for _, row in chunk
                                    if row.get("Main Category") and row.get("Subcategory")}
                    missing_paths = needed_paths - supplier_categories.keys()
                    if missing_paths:
                        names = {name for path in missing_paths for name in path}
                        categories = db.scalars(select(Category).where(Category.active.is_(True), Category.normalized_name.in_(names))).all()
                        mains = {normalize_text(category.normalized_name or category.name_en): category for category in categories if category.parent_id is None}
                        for main_norm, sub_norm in missing_paths:
                            main = mains.get(main_norm)
                            sub = next((category for category in categories if main and category.parent_id == main.id and normalize_text(category.normalized_name or category.name_en) == sub_norm), None)
                            supplier_categories[(main_norm, sub_norm)] = (main, sub)
                    requested_materials = set()
                    for _, row in chunk:
                        path = supplier_categories.get((normalize_text(row.get("Main Category", "")), normalize_text(row.get("Subcategory", ""))))
                        if path and path[1] and row.get("Material Description") and row.get("Unit"):
                            requested_materials.add((path[1].id, normalize_text(row["Material Description"]), normalize_unit(row["Unit"])))
                    missing_materials = requested_materials - material_cache.keys()
                    if missing_materials:
                        sub_ids = {key[0] for key in missing_materials}; descriptions = {key[1] for key in missing_materials}; units = {key[2] for key in missing_materials}
                        candidates = db.scalars(select(Material).where(Material.active.is_(True), Material.subcategory_id.in_(sub_ids),
                            Material.normalized_description.in_(descriptions), Material.unit.in_(units))).all()
                        found = {(material.subcategory_id, material.normalized_description or normalize_text(material.name_en), material.unit): material for material in candidates}
                        for key in missing_materials: material_cache[key] = found.get(key)
                    process = lambda n, r: _supplier_row(db, r, n, batch, db.get(User, user_id), org_cache, cap_cache, supplier_categories, material_cache, seen)
                for number, row in chunk:
                    try:
                        with db.begin_nested():
                            status, reason = process(number, row)
                            if status == "failed":
                                batch_failures.append((number, row, reason)); _record_issue(db, batch, number, row, reason)
                            elif status == "imported": imported += 1
                            else: skipped += 1
                    except Exception as exc:
                        category_cache.clear(); unit_cache.clear(); supplier_categories.clear(); material_cache.clear(); org_cache.clear(); cap_cache.clear()
                        batch_failures.append((number, row, str(exc) or "Database rejected this row")); _record_issue(db, batch, number, row, str(exc) or "Database rejected this row")
                db.flush()
                batch.created = imported; batch.unchanged_count = skipped; batch.errors = len(issues) + len(batch_failures)
                batch.completed_rows = completed_rows + len(chunk)
                db.commit()
            except Exception as exc:
                failed_batch_id = batch.id
                db.rollback(); db.close(); db = SessionLocal()
                category_cache.clear(); unit_cache.clear(); supplier_categories.clear(); material_cache.clear(); org_cache.clear(); cap_cache.clear()
                imported, skipped = before[0], before[1]
                batch_failures = [(number, row, f"Batch {index} database error: {str(exc)[:180]}") for number, row in chunk]
                fallback = SessionLocal()
                try:
                    batch = fallback.get(ImportBatch, failed_batch_id)
                    for number, row, reason in batch_failures: _record_issue(fallback, batch, number, row, reason)
                    batch.errors += len(batch_failures); batch.completed_rows += len(chunk)
                    fallback.commit()
                except Exception:
                    fallback.rollback()
                    yield json.dumps({"type":"error","message":"Could not record a failed batch report in PostgreSQL; previous committed batches remain saved."}) + "\n"
                    return
                finally: fallback.close()
                batch = db.get(ImportBatch, failed_batch_id)
            issues.extend({"row": number, "reason": reason} for number, _, reason in batch_failures)
            completed_rows += len(chunk)
            if kind == "materials" and imported > before[0]: catalog_cache.invalidate()
            yield json.dumps(_progress(batch.id, len(rows), index, batch_count, imported, skipped, issues)) + "\n"
        batch = db.get(ImportBatch, batch.id)
        batch.status = "completed_with_errors" if issues else "completed"; batch.stage = "complete"
        audit(db, db.get(User, user_id), f"{kind[:-1]}_import.completed", entity_id=batch.id, rows=len(rows), created=imported, errors=len(issues))
        db.commit()
        yield json.dumps({"type":"complete", "batch_id":batch.id, "total_rows":len(rows), "batch_size":BATCH_SIZE,
            "completed_batches":batch_count, "batches_total":batch_count, "imported":imported, "skipped":skipped,
            "failed":len(issues), "progress_percent":100, "issues":issues}) + "\n"
    except Exception as exc:
        db.rollback()
        yield json.dumps({"type":"error","message":str(exc) or "The import failed while writing to PostgreSQL."}) + "\n"
    finally: db.close()


def _start_stream(kind, file, user):
    rows = _read(file.file.read(8_000_001), file.filename or "", MATERIAL_HEADERS if kind == "materials" else SUPPLIER_HEADERS)
    return StreamingResponse(_stream_import(kind, rows, file.filename or f"{kind}.xlsx", user.id), media_type="application/x-ndjson",
        headers={"Cache-Control":"no-cache, no-transform", "X-Accel-Buffering":"no"})


@router.post("/materials")
def upload_materials(file: UploadFile = File(...), user: User = Depends(admin_user)):
    return _start_stream("materials", file, user)


@router.post("/suppliers")
def upload_suppliers(file: UploadFile = File(...), user: User = Depends(admin_user)):
    return _start_stream("suppliers", file, user)


@router.get("/{batch_id}/errors")
def error_report(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch: raise HTTPException(404, "Import report not found")
    rows = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch_id).order_by(ImportedRow.row_number)).all()
    if not rows: raise HTTPException(404, "This import has no failed rows")
    book=Workbook(); sheet=book.active; sheet.title="Failed rows"; sheet.append(["Excel row", "Reason", *list(rows[0].raw.keys())])
    for item in rows: sheet.append([item.row_number,item.issue,*[item.raw.get(k,"") for k in rows[0].raw.keys()]])
    out=BytesIO();book.save(out);out.seek(0)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{batch.kind}-errors.xlsx"'})
