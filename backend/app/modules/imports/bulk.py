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
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import catalog_cache
from app.database import SessionLocal, get_db
from app.models import Capability, Category, Contact, ImportBatch, ImportedRow, ImportedSupplierTrade, Material, Organization, User
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


def _progress(batch_id, total, index, batch_count, imported, skipped, issues, batch_size=BATCH_SIZE, incomplete=0):
    return {"type": "progress", "batch_id": batch_id, "total_rows": total, "batch_size": batch_size,
        "completed_batches": index, "batches_total": batch_count, "imported": imported, "skipped": skipped,
        "incomplete": incomplete, "failed": len(issues), "progress_percent": round(index / batch_count * 100, 2) if batch_count else 100,
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
    name, contact_name, raw_email, raw_phone, country, city, address, main_name, sub_name, desc, unit = (
        row.get(key, "") for key in SUPPLIER_HEADERS)
    name = _clean(name)
    name_key = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    email_values = _supplier_emails(raw_email)
    phone_values = _supplier_phones(raw_phone)
    valid_email = next((email for email in email_values if email["valid"]), None)
    valid_phone = next((phone for phone in phone_values if phone["normalized"] and not phone["fax"]), None)
    if not name and not valid_email and not valid_phone:
        return None, False, [], ["Company Name or a valid email/phone identifier is required"]
    reliable_key = name_key or ("email:" + valid_email["value"].casefold() if valid_email else "phone:" + valid_phone["normalized"])
    org = org_cache.get(reliable_key)
    created = False
    if org is None and name_key:
        org = db.scalar(select(Organization).where(Organization.is_supplier.is_(True),
            func.lower(func.trim(Organization.name_normalized)) == name_key).limit(1))
    if org is None and valid_email:
        org = db.scalar(select(Organization).where(Organization.is_supplier.is_(True),
            func.lower(func.trim(Organization.business_email)) == valid_email["value"].casefold()).limit(1))
        if org is None:
            contact = db.scalar(select(Contact).where(func.lower(func.trim(Contact.email)) == valid_email["value"].casefold()))
            org = db.get(Organization, contact.org_id) if contact else None
    if org is None and valid_phone:
        org = db.scalar(select(Organization).where(Organization.is_supplier.is_(True),
            Organization.business_phone == valid_phone["normalized"]).limit(1))
        if org is None:
            contact = db.scalar(select(Contact).where(Contact.phone_normalized == valid_phone["normalized"]))
            org = db.get(Organization, contact.org_id) if contact else None
    if org is None:
        company_name = name or f"[Imported supplier placeholder: {reliable_key.split(':', 1)[-1]}]"
        org = Organization(name=company_name[:220], name_normalized=(name_key or company_name.casefold())[:220],
            country=country[:100] or None, city=city[:100] or None, address=address[:300] or None,
            business_email=valid_email["value"] if valid_email else None,
            business_phone=valid_phone["normalized"] if valid_phone else None,
            contact_name=contact_name[:150] or None, source="imported", is_supplier=True, is_buyer=False,
            verified_status="unverified", profile_status="pending_onboarding")
        db.add(org); db.flush(); created = True
    else:
        # Fill gaps without erasing any existing valid supplier profile data.
        if not org.business_email and valid_email: org.business_email = valid_email["value"]
        if not org.business_phone and valid_phone: org.business_phone = valid_phone["normalized"]
        if not org.city and city: org.city = city[:100]
        if not org.address and address: org.address = address[:300]
        if not org.contact_name and contact_name: org.contact_name = contact_name[:150]
        if org.verified_status != "verified" and org.source == "imported": org.profile_status = "pending_onboarding"

    contact_warnings = []
    if any(not item["valid"] for item in email_values): contact_warnings.append("Email needs review")
    if any(not item["normalized"] and not item["fax"] for item in phone_values): contact_warnings.append("Phone needs review")
    if name_key: org_cache[name_key] = org
    org_cache[reliable_key] = org

    emails = [item for item in email_values if item["valid"]]
    phones = [item for item in phone_values if item["normalized"] and not item["fax"]]
    faxes = [item for item in phone_values if item["fax"]]
    primary_phone = phones[0] if len(phones) == 1 else None
    contact_specs = []
    if emails:
        contact_specs.extend((email["value"], primary_phone, "", False) for email in emails)
    if phones and not (len(phones) == 1 and emails):
        contact_specs.extend(("", phone, "", False) for phone in phones)
    if faxes:
        contact_specs.extend(("", None, fax["value"], False) for fax in faxes)
    contact_specs.extend(("", None, "", True) for item in email_values if not item["valid"])
    if raw_email and not email_values: contact_specs.append(("", None, "", True))
    if any(not item["normalized"] and not item["fax"] for item in phone_values):
        contact_specs.append(("", None, "", True))
    if not contact_specs and contact_name: contact_specs.append(("", None, "", False))
    contacts_added = 0
    for email_value, phone_value, fax_value, needs_review in contact_specs:
        email_key = email_value.casefold() if email_value else ""
        phone_key = phone_value["normalized"] if phone_value else ""
        try:
            with db.begin_nested():
                duplicate_query = select(Contact.id).where(Contact.org_id == org.id)
                if email_key:
                    duplicate_query = duplicate_query.where(func.lower(func.trim(Contact.email)) == email_key)
                elif phone_key:
                    duplicate_query = duplicate_query.where(Contact.phone_normalized == phone_key)
                elif fax_value:
                    duplicate_query = duplicate_query.where(Contact.fax == fax_value[:80])
                else:
                    duplicate_query = duplicate_query.where(Contact.raw_email == (raw_email or None), Contact.raw_phone == (raw_phone or None),
                        Contact.name == (contact_name or None), Contact.needs_review.is_(needs_review))
                if db.scalar(duplicate_query): continue
                db.add(Contact(org_id=org.id, name=contact_name[:150] or None,
                    email=email_value[:220] or None, phone=phone_value["original"][:80] if phone_value else None,
                    phone_normalized=phone_key or None, fax=fax_value[:80] or None,
                    raw_phone=raw_phone[:300] or None, raw_email=raw_email[:500] or None,
                    needs_review=needs_review, permission_to_notify=False))
                db.flush()
            contacts_added += 1
        except Exception:
            contact_warnings.append("A contact entry needs database review")
    db.flush()
    missing = [label for label, value in (("Company Name", not org.name.startswith("[Imported supplier placeholder:")),
        ("Email", org.business_email), ("Phone", org.business_phone), ("City", org.city),
        ("Address", org.address), ("Contact Person", org.contact_name)) if not value]
    if (missing or contact_warnings) and org.verified_status != "verified" and org.source == "imported":
        org.profile_status = "pending_onboarding"
    elif org.verified_status != "verified" and org.source == "imported":
        org.profile_status = "pending_onboarding"
    return org, created, missing, contact_warnings, contacts_added


def _supplier_emails(raw_value):
    values = []
    for part in re.split(r"[/,;]+", _clean(raw_value)):
        value = part.strip()
        if not value: continue
        values.append({"value": value, "valid": len(value) <= 220 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))})
    return values


def _supplier_phones(raw_value):
    raw = _clean(raw_value)
    if not raw: return []
    parts = [part.strip() for part in re.split(r"[,;/|\n]+|(?<=\d)\s+(?=(?:T|TEL|PHONE|MOBILE|P|F|FAX)\s*:)", raw, flags=re.I) if part.strip()]
    values = []
    for part in parts:
        fax = bool(re.match(r"^(?:F|FAX)\s*:\s*", part, re.I))
        stripped = re.sub(r"^(?:T|TEL|PHONE|MOBILE|P|F|FAX)\s*:\s*", "", part, flags=re.I).strip()
        stripped = re.sub(r"^tel\s*:\s*", "", stripped, flags=re.I).strip()
        candidate = stripped
        country = re.match(r"^[\[(]\s*(\+?\d{1,3})\s*[\])]\s*", candidate)
        if country:
            candidate = "+" + country.group(1).lstrip("+") + candidate[country.end():]
        digits = "".join(str(unicodedata.digit(char)) for char in candidate if char.isdigit())
        explicit_plus = candidate.lstrip().startswith("+") or candidate.lstrip().startswith("00")
        if digits.startswith("00") and explicit_plus:
            digits = digits[2:]
        normalized = ("+" if explicit_plus else "") + digits if 8 <= len(digits) <= 15 and (not explicit_plus or not digits.startswith("0")) else ""
        values.append({"original": stripped, "value": stripped, "normalized": normalized, "fax": fax})
    return values


def _category_key(value):
    return " ".join(unicodedata.normalize("NFKC", _clean(value)).casefold().split())


def _find_supplier_category_path(db, main_name, sub_name, category_cache):
    key = (_category_key(main_name), _category_key(sub_name))
    if key not in category_cache:
        all_categories = db.scalars(select(Category).where(Category.active.is_(True))).all()
        mains = [item for item in all_categories if item.parent_id is None and
            key[0] in {_category_key(item.name_en), _category_key(item.normalized_name)}]
        main = mains[0] if len(mains) == 1 else None
        subs = [item for item in all_categories if main and item.parent_id == main.id and
            key[1] in {_category_key(item.name_en), _category_key(item.normalized_name)}]
        category_cache[key] = (main, subs[0] if len(subs) == 1 else None)
    return category_cache[key]


def _supplier_capability(db, org, row, batch, number, cap_cache, category_cache, material_cache):
    main_name = _clean(row.get("Main Category")); sub_name = _clean(row.get("Subcategory"))
    desc = _clean(row.get("Material Description")); unit = _clean(row.get("Unit")); city = _clean(row.get("City"))
    warning = ""
    if not desc: return "skipped", "Material description is empty"
    if len(desc) > 250 or len(normalize_text(desc)) < 3:
        return "skipped", "Material description must contain 3 to 250 characters"
    if not main_name or not sub_name:
        return "skipped", "Category path is incomplete"
    main, sub = _find_supplier_category_path(db, main_name, sub_name, category_cache)
    if not main or not sub:
        raw_trade = desc
        path = f"{main_name} / {sub_name}"
        existing = db.scalar(select(ImportedSupplierTrade.id).where(ImportedSupplierTrade.org_id == org.id,
            ImportedSupplierTrade.raw_trade == raw_trade, ImportedSupplierTrade.raw_type == path,
            ImportedSupplierTrade.raw_location == city))
        if not existing:
            db.add(ImportedSupplierTrade(org_id=org.id, import_batch_id=batch.id, raw_trade=raw_trade,
                raw_type=path[:150], raw_location=city, raw_brands=""))
        return "skipped", f"Category path needs mapping: {path}"
    if not unit: return "skipped", "Unit is missing"
    material_key = (main.id, sub.id, normalize_text(desc), normalize_unit(unit))
    if material_key not in material_cache:
        material_cache[material_key] = db.scalar(select(Material).where(Material.main_category_id == main.id,
            Material.subcategory_id == sub.id, Material.normalized_description == material_key[2],
            Material.unit == material_key[3], Material.active.is_(True)))
    material = material_cache[material_key]
    ident = (org.id, sub.id, _category_key(city))
    cap = cap_cache.get(ident)
    if cap is None:
        cap = db.scalar(select(Capability).where(Capability.org_id == org.id, Capability.category_id == sub.id,
            func.lower(func.trim(Capability.city)) == city.casefold().strip()))
    if cap is None:
        cap = Capability(org_id=org.id, category_id=sub.id, city=city, trade_keywords=desc,
            confirmed=False, accepts_rfqs=False)
        db.add(cap); db.flush()
    cap_cache[ident] = cap
    if material and material.id not in (cap.material_ids or []):
        cap.material_ids = [*(cap.material_ids or []), material.id]
        return "saved", ""
    if not material:
        return "saved", f"Material is not in the material master: {desc} ({unit})"
    return "duplicate", ""


def _supplier_identity_key(row):
    name = " ".join(unicodedata.normalize("NFKC", _clean(row.get("Company Name"))).casefold().split())
    if name: return "name:" + name
    emails = [item["value"].casefold() for item in _supplier_emails(row.get("Email")) if item["valid"]]
    if emails: return "email:" + emails[0]
    phones = [item["normalized"] for item in _supplier_phones(row.get("Phone")) if item["normalized"] and not item["fax"]]
    return "phone:" + phones[0] if phones else "unidentified"


def _stream_import(kind, rows, filename, user_id):
    chunks = _chunks(rows); batch_count = len(chunks); db = SessionLocal()
    issues = []; warnings = []; incomplete_rows = []; imported = skipped = incomplete = completed_rows = 0
    capability_saved = capability_skipped = contacts_created = 0; category_cache = {}; unit_cache = {}; seen = set()
    org_cache = {}; cap_cache = {}; material_cache = {}
    new_supplier_ids = set(); reused_supplier_ids = set(); incomplete_supplier_ids = set(); failed_supplier_keys = set()
    supplier_total = len({_supplier_identity_key(row) for _, row in rows if _supplier_identity_key(row) != "unidentified"}) if kind == "suppliers" else 0
    batch = ImportBatch(kind=kind, filename=filename[:200], status="processing", stage="apply", rows=len(rows), initiated_by=user_id)
    try:
        db.add(batch); db.commit(); db.refresh(batch)
        yield json.dumps({"type":"started", "batch_id":batch.id, "total_rows":len(rows), "batch_size":BATCH_SIZE,
            "completed_batches":0, "batches_total":batch_count, "progress_percent":0,
            "total_supplier_records_identified":supplier_total,
            "imported":0, "skipped":0, "incomplete":0, "failed":0, "capabilities_saved":0,
            "capabilities_skipped":0, "warnings":[], "issues":[]}) + "\n"
        for index, chunk in enumerate(chunks, 1):
            batch_failures = []
            if kind == "materials":
                descriptions = {normalize_text(row.get("Description", "")) for _, row in chunk if row.get("Description")}
                candidates = db.scalars(select(Material).where(Material.normalized_description.in_(descriptions))).all() if descriptions else []
                by_description = {}
                for candidate in candidates: by_description.setdefault(candidate.normalized_description or normalize_text(candidate.name_en), []).append(candidate)
                for number, row in chunk:
                    try:
                        with db.begin_nested():
                            status, reason = _material_row(db, row, number, batch, db.get(User, user_id), category_cache, unit_cache, by_description, seen)
                            if status == "failed":
                                batch_failures.append((number, row, reason)); _record_issue(db, batch, number, row, reason)
                            elif status == "imported": imported += 1
                            else: skipped += 1
                    except Exception as exc:
                        batch_failures.append((number, row, str(exc) or "Database rejected this row"))
                        _record_issue(db, batch, number, row, str(exc) or "Database rejected this row")
            else:
                for number, row in chunk:
                    identity_key = _supplier_identity_key(row)
                    raw_warnings = []
                    try:
                        # Commit organization and contacts before attempting this row's capability.
                        with db.begin_nested():
                            result = _supplier_row(db, row, number, batch, db.get(User, user_id), org_cache, cap_cache, {}, material_cache, seen)
                            if result[0] is None:
                                raise ValueError("Company Name or a valid email/phone identifier is required")
                            org, created, missing, contact_warnings, contact_count = result
                            db.flush()
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        failed_supplier_keys.add(identity_key if identity_key != "unidentified" else f"row:{number}")
                        reason = str(exc) if isinstance(exc, ValueError) else f"Supplier record could not be saved: {type(exc).__name__}"
                        batch_failures.append((number, row, reason)); _record_issue(db, batch, number, row, reason)
                        db.commit()
                        continue

                    contacts_created += contact_count
                    if created: new_supplier_ids.add(org.id)
                    elif org.id not in new_supplier_ids: reused_supplier_ids.add(org.id)
                    if missing or contact_warnings: incomplete_supplier_ids.add(org.id)
                    if missing:
                        incomplete_rows.append({"row":number, "reason":", ".join(f"{field} missing" for field in missing), "missing_fields":missing})
                        raw_warnings.extend(f"{field} missing" for field in missing)
                    raw_warnings.extend(contact_warnings)
                    try:
                        with db.begin_nested():
                            cap_status, cap_warning = _supplier_capability(db, org, row, batch, number,
                                cap_cache, category_cache, material_cache)
                            db.flush()
                        db.commit()
                        if cap_status == "saved": capability_saved += 1
                        elif cap_status == "skipped": capability_skipped += 1
                        if cap_warning: raw_warnings.append(cap_warning)
                    except Exception as exc:
                        db.rollback()
                        cap_status = "skipped"; capability_skipped += 1
                        raw_warnings.append(f"Capability processing failed: {type(exc).__name__}")
                    row_status = "incomplete" if (missing or contact_warnings) else ("created" if created else "existing")
                    issue_text = "; ".join(dict.fromkeys(raw_warnings))
                    if issue_text:
                        warnings.append({"row":number, "reason":issue_text, "capability_saved":cap_status == "saved"})
                    try:
                        db.add(ImportedRow(batch_id=batch.id, row_number=number, raw=row,
                            status="warning" if issue_text else row_status, issue=issue_text[:300] or None))
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        warnings.append({"row":number, "reason":f"Import report row could not be saved: {type(exc).__name__}",
                            "capability_saved":cap_status == "saved"})
            issues.extend({"row": number, "reason": reason} for number, _, reason in batch_failures)
            completed_rows += len(chunk)
            batch.created = len(new_supplier_ids) if kind == "suppliers" else imported
            batch.unchanged_count = len(reused_supplier_ids) if kind == "suppliers" else skipped
            batch.errors = len(failed_supplier_keys) if kind == "suppliers" else len(issues)
            batch.completed_rows = completed_rows
            db.commit()
            if kind == "materials" and imported: catalog_cache.invalidate()
            yield json.dumps({"type":"progress", "batch_id":batch.id, "total_rows":len(rows), "batch_size":BATCH_SIZE,
                "completed_batches":index, "batches_total":batch_count, "total_supplier_records_identified":supplier_total,
                "imported":imported, "skipped":skipped, "incomplete":len(incomplete_supplier_ids),
                "failed":len(failed_supplier_keys) if kind == "suppliers" else len(issues),
                "new_suppliers_saved":len(new_supplier_ids), "existing_suppliers_reused":len(reused_supplier_ids),
                "contacts_created":contacts_created, "capabilities_saved":capability_saved, "capabilities_skipped":capability_skipped,
                "progress_percent":round(index / batch_count * 100, 2) if batch_count else 100,
                "issues":issues[-50:], "warnings":warnings[-50:], "incomplete_rows":incomplete_rows[-50:]}) + "\n"
        batch = db.get(ImportBatch, batch.id)
        batch.status = "completed_with_errors" if failed_supplier_keys or (kind == "materials" and issues) else "completed"
        batch.stage = "complete"; batch.errors = len(failed_supplier_keys) if kind == "suppliers" else len(issues)
        audit(db, db.get(User, user_id), f"{kind[:-1]}_import.completed", entity_id=batch.id, rows=len(rows),
            created=len(new_supplier_ids) if kind == "suppliers" else imported, errors=batch.errors)
        db.commit()
        yield json.dumps({"type":"complete", "batch_id":batch.id, "total_rows":len(rows), "batch_size":BATCH_SIZE,
            "completed_batches":batch_count, "batches_total":batch_count,
            "total_supplier_records_identified":supplier_total, "new_suppliers_saved":len(new_supplier_ids),
            "existing_suppliers_reused":len(reused_supplier_ids), "suppliers_incomplete":len(incomplete_supplier_ids),
            "contacts_created":contacts_created, "capabilities_saved":capability_saved, "capabilities_skipped":capability_skipped,
            "supplier_failures":len(failed_supplier_keys), "imported":imported, "skipped":skipped,
            "incomplete":len(incomplete_supplier_ids), "failed":len(failed_supplier_keys) if kind == "suppliers" else len(issues),
            "warnings":warnings, "incomplete_rows":incomplete_rows, "issues":issues}) + "\n"
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
    rows = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch_id, ImportedRow.status == "invalid").order_by(ImportedRow.row_number)).all()
    if not rows: raise HTTPException(404, "This import has no failed rows")
    book=Workbook(); sheet=book.active; sheet.title="Failed rows"; sheet.append(["Excel row", "Reason", *list(rows[0].raw.keys())])
    for item in rows: sheet.append([item.row_number,item.issue,*[item.raw.get(k,"") for k in rows[0].raw.keys()]])
    out=BytesIO();book.save(out);out.seek(0)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{batch.kind}-errors.xlsx"'})


@router.get("/suppliers/{batch_id}/warnings")
def supplier_warning_report(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.kind != "suppliers": raise HTTPException(404, "Supplier import report not found")
    rows = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch_id,
        or_(ImportedRow.status == "warning", ImportedRow.status == "incomplete")).order_by(ImportedRow.row_number)).all()
    if not rows: raise HTTPException(404, "This import has no supplier warnings")
    book = Workbook(); sheet = book.active; sheet.title = "Supplier warnings"
    source_keys = list(rows[0].raw.keys())
    sheet.append(["Excel row", "Status", "Warning", *source_keys])
    for item in rows: sheet.append([item.row_number, item.status, item.issue or "Profile incomplete", *[item.raw.get(key, "") for key in source_keys]])
    out = BytesIO(); book.save(out); out.seek(0)
    return StreamingResponse(out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="supplier-import-{batch_id}-warnings.xlsx"'})


@router.get("/suppliers/{batch_id}/incomplete")
def incomplete_supplier_report(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.kind != "suppliers": raise HTTPException(404, "Supplier import report not found")
    rows = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch_id, ImportedRow.status == "incomplete").order_by(ImportedRow.row_number)).all()
    if not rows: raise HTTPException(404, "This import has no incomplete suppliers")
    book = Workbook(); sheet = book.active; sheet.title = "Incomplete suppliers"
    source_keys = list(rows[0].raw.keys())
    sheet.append(["Excel row", "Missing fields", *source_keys])
    for item in rows: sheet.append([item.row_number, item.issue or "", *[item.raw.get(key, "") for key in source_keys]])
    out = BytesIO(); book.save(out); out.seek(0)
    return StreamingResponse(out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="supplier-import-{batch_id}-incomplete.xlsx"'})
