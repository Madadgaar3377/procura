"""Direct, retry-safe spreadsheet imports used from the Materials and Suppliers pages."""
from __future__ import annotations

import re
import json
import unicodedata
from io import BytesIO
from math import ceil
from zipfile import ZipFile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import catalog_cache
from app.database import SessionLocal, get_db
from app.models import Capability, Category, Contact, ContactCapability, ContactChannel, ImportBatch, ImportedRow, ImportedSupplierTrade, Material, Organization, User
from app.modules.materials.service import create_material_record, normalize_text, normalize_unit, resolve_category
from app.modules.imports.supplier_mapping import apply_supplier_trade_mapping, ensure_import_verification, ensure_trade_record, extract_website
from app.modules.organizations.contact_import import import_contact_row, parse_phones
from app.security import admin_user, audit

router = APIRouter(prefix="/bulk", tags=["Bulk Materials and Suppliers"])
MATERIAL_HEADERS = ["Discipline", "Main Category", "Subcategory", "Description", "Unit"]
SUPPLIER_HEADERS = ["Company Name","Country","City","Address","Website","CR Number","VAT Number","Company Description","Contact Person","Contact Designation","Contact Email","Contact Phone","Category","Material","Is Primary Contact"]
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
            sheets = [(sheet.name, [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]) for sheet in book.sheets()]
            book.release_resources()
        else:
            with ZipFile(BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 64_000_000:
                    raise HTTPException(413, 'Expanded workbook exceeds the 64 MB parsing limit')
            book = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
            sheets=[]
            for sheet in book.worksheets:
                if sheet.max_row > 30_001: raise HTTPException(413, f"Worksheet {sheet.title} exceeds the 30,000-row limit")
                if sheet.max_column > 120: raise HTTPException(413, 'Workbook exceeds the 120-column parsing limit')
                sheets.append((sheet.title,list(sheet.iter_rows(values_only=True))) )
            book.close()
    except HTTPException: raise
    except ImportError as exc: raise HTTPException(503, "Legacy XLS support is not installed") from exc
    except Exception as exc: raise HTTPException(422, "The uploaded Excel workbook could not be read") from exc
    if not any(values for _,values in sheets): raise HTTPException(422, "The workbook is empty")
    if headers is SUPPLIER_HEADERS or headers == SUPPLIER_HEADERS:
        aliases={
            "company name":{"company name","supplier name","suppliers name","supplier company","supplier company name","legal company name","company","supplier"},
            "contact person":{"contact person","contact name","contact person name","representative","sales contact"},
            "contact designation":{"contact designation","designation","job title","contact title","role"},
            "email":{"email","email address","e-mail","contact email"},
            "phone":{"phone","mobile","mobile phone","mobile phone number","telephone","telephone number","phone number","contact number","tel","contact phone"},
            "country":{"country"},"city":{"city","location","supplier location"},"address":{"address","business address"},
            "category":{"category","supplier category","assigned category"},"material":{"material","specific material","assigned material"},
            "is primary contact":{"is primary contact","primary contact","primary"},
            "main category":{"main category","discipline category"},"subcategory":{"subcategory","sub category","sub-category"},
            "material description":{"material description","product description","description","products","materials","product"},
            "unit":{"unit","uom","unit of measure"},"discipline":{"discipline"},
            "trade":{"trade","trade products","trade / products","trade/products","products and services","trade / services","services"},
            "type":{"type","supplier type","supplier / manufacturer","supplier/manufacturer","business type"},
            "website":{"website","web site","url","company website"},"brands":{"brands","brand","brand names"},
            "cr number":{"cr number","commercial registration","commercial registration number","cr no"},
            "vat number":{"vat number","vat registration","vat registration number"},
            "company description":{"company description","about company","profile description"}}
        aliases={canonical:{re.sub(r"[^a-z0-9]+"," ",value.casefold()).strip() for value in values} for canonical,values in aliases.items()}
        parsed=[]
        for sheet_name,values in sheets:
            header_index=None;header_map={}
            for index,source_row in enumerate(values[:15]):
                mapped={}
                for column_index,value in enumerate(source_row):
                    key=re.sub(r"[^a-z0-9]+"," ",_clean(value).casefold()).strip()
                    for canonical,choices in aliases.items():
                        if key in choices: mapped.setdefault(canonical,column_index);break
                if "company name" in mapped and len(mapped)>=2:
                    header_index=index;header_map=mapped;break
            if header_index is None: continue
            previous_name=""
            for physical_row,source_row in enumerate(values[header_index+1:],header_index+2):
                raw_excel={str(values[header_index][column]):("" if value is None else str(value)) for column,value in enumerate(source_row)
                    if column<len(values[header_index]) and _clean(values[header_index][column])}
                canonical_row={canonical:_clean(source_row[column]) if column<len(source_row) else "" for canonical,column in header_map.items()}
                if not any(canonical_row.values()): continue
                name=canonical_row.get("company name","")
                if name: previous_name=name
                elif previous_name and any(canonical_row.get(key) for key in ("trade","type","contact person","phone","email","website","city","brands","main category","subcategory","category")):
                    canonical_row["company name"]=previous_name
                else: continue
                display_keys={"company name":"Company Name","contact person":"Contact Person","contact designation":"Contact Designation",
                    "email":"Contact Email","phone":"Contact Phone","country":"Country","city":"City","address":"Address",
                    "website":"Website","cr number":"CR Number","vat number":"VAT Number","company description":"Company Description",
                    "category":"Category","material":"Material","is primary contact":"Is Primary Contact",
                    "main category":"Main Category","subcategory":"Subcategory","material description":"Material Description",
                    "unit":"Unit","discipline":"Discipline","trade":"Trade","type":"Type","brands":"Brands"}
                row={display_keys[key]:value for key,value in canonical_row.items() if key in display_keys}
                row["_source_sheet"]=sheet_name;row["_source_row"]=physical_row;row["_raw_excel"]=raw_excel
                parsed.append((physical_row,row))
                if len(parsed) > 30_000: raise HTTPException(413, 'Workbook exceeds the 30,000-row limit across worksheets')
        if not parsed: raise HTTPException(422,"No worksheet contains a supplier company-name header")
        return parsed
    values=sheets[0][1]
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
    book=Workbook();sheet=book.active;sheet.title='Supplier Import';sheet.append(SUPPLIER_HEADERS)
    examples=[
        ['Sydney Fine','Saudi Arabia','Jeddah','','','', '', '', 'Heal','Sales','heal@example.com','+966111111111','Lighting','Lighting Fixture','Yes'],
        ['Sydney Fine','Saudi Arabia','Jeddah','','','', '', '', 'Abby Adeeb Arshad','Sales','abby@example.com','+966222222222','Fixer','Fixer','No'],
        ['Sydney Fine','Saudi Arabia','Jeddah','','','', '', '', 'Another Contact','Sales','contact@example.com','+966333333333','Generator','Generator','No'],
        ['ABC Trading','Saudi Arabia','Riyadh','','','', '', '', 'Ali','Sales','ali@example.com','+966444444444','Electrical','Cables','Yes'],
        ['ABC Trading','Saudi Arabia','Riyadh','','','', '', '', 'Ahmed','Sales','ahmed@example.com','+966555555555','Mechanical','Pumps','No'],
    ]
    for row in examples:sheet.append(row)
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for column in sheet.columns:
        letter=column[0].column_letter;sheet.column_dimensions[letter].width=min(38,max(15,max(len(str(cell.value or '')) for cell in column)+2))
    yes_no=DataValidation(type='list',formula1='"Yes,No"',allow_blank=True);sheet.add_data_validation(yes_no);yes_no.add('O2:O5000')
    instructions=book.create_sheet('Instructions')
    instructions.append(['Supplier Excel Import Instructions'])
    for text in [
        'Enter company information and the responsible contact/category on each row.',
        'For multiple contacts, add multiple rows and repeat the same company name.',
        "Enter each person's own email and phone number; blank contact details are allowed.",
        'Select or type the category that the contact handles. Use the category as it appears in the admin Material Master.',
        'Do not create separate supplier companies for different contacts.',
        'Do not enter fake or placeholder contact details.',
        'Missing phone or email can remain blank.',
        'Do not modify the Supplier Import column headers.',
        'Material is optional. The importer links it only when it matches the category and Material Master.',
        'Example: Sydney Fine has Heal (Lighting), Abby Adeeb Arshad (Fixer), and Another Contact (Generator).',
        'Example: ABC Trading has Ali (Electrical / Cables) and Ahmed (Mechanical / Pumps).',
    ]:instructions.append([text])
    instructions.column_dimensions['A'].width=115
    out=BytesIO();book.save(out);out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':'attachment; filename="suppliers-template.xlsx"'})


@router.get('/suppliers/export')
def export_suppliers(user:User=Depends(admin_user),db:Session=Depends(get_db)):
    statement=select(Organization,Contact,Capability,Category).outerjoin(Contact, (Contact.org_id==Organization.id)&Contact.merged_into_contact_id.is_(None)&Contact.deleted_at.is_(None)).outerjoin(
        ContactCapability,ContactCapability.contact_id==Contact.id).outerjoin(Capability,Capability.id==ContactCapability.capability_id).outerjoin(Category,Category.id==Capability.category_id).where(
            Organization.is_supplier.is_(True),Organization.is_active.is_(True)).order_by(Organization.name,Contact.name,Category.name_en)
    rows=db.execute(statement).all()
    assigned=select(ContactCapability.capability_id).where(ContactCapability.capability_id==Capability.id).exists()
    rows.extend(db.execute(select(Organization,Capability,Category).join(Capability,Capability.org_id==Organization.id).join(Category,Category.id==Capability.category_id).where(
        Organization.is_supplier.is_(True),Organization.is_active.is_(True),~assigned)).all())
    rows=[tuple(row) if len(row)==4 else (row[0],None,row[1],row[2]) for row in rows]
    capability_map={cap.id:cap for _,_,cap,_ in rows if cap}
    material_ids=[material_id for cap in capability_map.values() for material_id in (cap.material_ids or [])]
    materials={}
    unique_ids=list(dict.fromkeys(material_ids))
    for offset in range(0,len(unique_ids),500):
        for material in db.scalars(select(Material).where(Material.id.in_(unique_ids[offset:offset+500]))).all():materials[material.id]=material.name_en
    channel_contact_ids={contact.id for _,contact,_,_ in rows if contact}
    channel_values={contact_id:{'email':[],'phone':[]} for contact_id in channel_contact_ids}
    if channel_contact_ids:
        for channel in db.scalars(select(ContactChannel).where(ContactChannel.contact_id.in_(channel_contact_ids),ContactChannel.kind.in_(['email','phone']))).all():
            channel_values[channel.contact_id][channel.kind].append(channel.value)
    book=Workbook();sheet=book.active;sheet.title='Suppliers Export';sheet.append(SUPPLIER_HEADERS)
    category_ids={category.id for _,_,_,category in rows if category}
    categories={category.id:category for category in db.scalars(select(Category).where(Category.id.in_(category_ids))).all()} if category_ids else {}
    parent_ids={category.parent_id for category in categories.values() if category.parent_id}
    parents={category.id:category for category in db.scalars(select(Category).where(Category.id.in_(parent_ids))).all()} if parent_ids else {}
    for org,contact,cap,category in rows:
        values=channel_values.get(contact.id,{}) if contact else {}
        emails=values.get('email') or ([contact.email] if contact and contact.email else [])
        phones=values.get('phone') or ([contact.phone] if contact and contact.phone else [])
        parent=parents.get(category.parent_id) if category and category.parent_id else None
        category_name=f'{parent.name_en} / {category.name_en}' if parent else category.name_en if category else ''
        sheet.append([org.name,org.country or '',org.city or '',org.address or '',org.website or '',org.cr_number or '',org.vat_number or '',org.description or '',
            contact.name or '' if contact else '',contact.role or '' if contact else '','; '.join(dict.fromkeys(emails)),'; '.join(dict.fromkeys(phones)),category_name,
            '; '.join(materials[mid] for mid in cap.material_ids if mid in materials) if cap else '',
            'Yes' if contact and contact.is_primary else 'No' if contact else ''])
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    out=BytesIO();book.save(out);out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':'attachment; filename="suppliers-export.xlsx"'})


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


def _supplier_write_lock(db):
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(717341392)'))


def _supplier_row(db, row, number, batch, user, org_cache, cap_cache, category_cache, material_cache, seen, metrics=None):
    _supplier_write_lock(db)
    name=_clean(row.get("Company Name"));contact_name=_clean(row.get("Contact Person"))
    if len(name) > 220:
        raise ValueError('Company Name must contain at most 220 characters')
    raw_email=row.get("Contact Email") or row.get("Email","");raw_phone=row.get("Contact Phone") or row.get("Phone","")
    country=_clean(row.get("Country"));city=_clean(row.get("City"));address=_clean(row.get("Address"))
    main_name=_clean(row.get("Main Category"));sub_name=_clean(row.get("Subcategory"))
    desc=_clean(row.get("Material") or row.get("Material Description"));unit=_clean(row.get("Unit"))
    contact_designation=_clean(row.get("Contact Designation"))
    contact_category=_clean(row.get("Category"))
    website = _clean(row.get("Website"))
    _, normalized_website = extract_website(website)
    website = normalized_website or website
    source_raw_trade = _clean(row.get("Trade"))
    trade_without_website, extracted_website = extract_website(source_raw_trade)
    raw_trade = source_raw_trade
    trade_keyword = trade_without_website or source_raw_trade
    raw_type = _clean(row.get("Type"))
    discipline = _clean(row.get("Discipline"))
    path = " / ".join(part for part in (discipline, main_name, sub_name) if part)
    # Use the Excel type only when it explicitly describes a discipline hierarchy.
    if raw_type and raw_type.split("/", 1)[0].strip().casefold() in DISCIPLINES:
        path = raw_type
    elif not path and trade_keyword and "/" in trade_keyword and trade_keyword.split("/",1)[0].strip().casefold() in DISCIPLINES:
        path = trade_keyword
    if not discipline and _category_key(main_name) in {"low current elv","low current"}:
        path=f"Electrical / {main_name}"
        if sub_name and sub_name.casefold() not in trade_keyword.casefold():
            trade_keyword=" | ".join(part for part in (trade_keyword,sub_name) if part)
    name = _clean(name)
    name_key = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    email_values = _supplier_emails(raw_email)
    phone_values = _supplier_phones(raw_phone)
    valid_email = next((email for email in email_values if email["valid"]), None)
    valid_phone = next((phone for phone in phone_values if phone["normalized"] and not phone["fax"]), None)
    emails=[item for item in email_values if item['valid']]
    phones=[item for item in phone_values if item['normalized'] and not item['fax']]
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
        if org and not org.is_supplier:
            raise ValueError('Contact email belongs to an unrelated organization; review this supplier')
    if org is None and valid_phone:
        org = db.scalar(select(Organization).where(Organization.is_supplier.is_(True),
            Organization.business_phone == valid_phone["normalized"]).limit(1))
        if org is None:
            contact = db.scalar(select(Contact).where(Contact.phone_normalized == valid_phone["normalized"]))
            org = db.get(Organization, contact.org_id) if contact else None
        if org and not org.is_supplier:
            raise ValueError('Contact phone belongs to an unrelated organization; review this supplier')
    if org and name_key and org.name_normalized and ' '.join(unicodedata.normalize('NFKC', org.name_normalized).casefold().split()) != name_key and not org.name.startswith('[Imported supplier placeholder:'):
        raise ValueError('Company name conflicts with an existing contact identity; review this supplier')
    profile_fields = ('business_email', 'business_phone', 'city', 'address', 'contact_name', 'country',
                      'website', 'cr_number', 'vat_number', 'description', 'profile_status')
    before_profile = tuple(getattr(org, field) for field in profile_fields) if org else None
    if org is None:
        company_name = name or f"[Imported supplier placeholder: {reliable_key.split(':', 1)[-1]}]"
        org = Organization(name=company_name[:220], name_normalized=(name_key or company_name.casefold())[:220],
            country=country[:100] or None, city=city[:100] or None, address=address[:300] or None,
            business_email=valid_email["value"] if valid_email else None,
            business_phone=valid_phone["normalized"] if valid_phone else None,
            contact_name=contact_name[:150] or None, source="imported", is_supplier=True, is_buyer=False,
            verified_status="unverified", profile_status="incomplete")
        db.add(org); db.flush(); created = True
    else:
        # Fill gaps without erasing any existing valid supplier profile data.
        if not org.business_email and valid_email: org.business_email = valid_email["value"]
        if not org.business_phone and valid_phone: org.business_phone = valid_phone["normalized"]
        if not org.city and city: org.city = city[:100]
        if not org.address and address: org.address = address[:300]
        if not org.contact_name and contact_name: org.contact_name = contact_name[:150]
        if not org.country and country: org.country=country[:100]
        if not org.website and (website or extracted_website): org.website=(website or extracted_website)[:300]
        if not org.cr_number and row.get("CR Number"): org.cr_number=_clean(row.get("CR Number"))[:80]
        if not org.vat_number and row.get("VAT Number"): org.vat_number=_clean(row.get("VAT Number"))[:80]
        if not org.description and row.get("Company Description"): org.description=_clean(row.get("Company Description"))[:5000]

    contact_warnings = []
    if any(not item["valid"] for item in email_values): contact_warnings.append("Email needs review")
    if any(not item["normalized"] and not item["fax"] for item in phone_values): contact_warnings.append("Phone needs review")
    if name_key: org_cache[name_key] = org
    org_cache[reliable_key] = org

    source_row = row.get("_source_row", number)
    contact_changes = {}
    contacts_added, contacts_need_review, contact_ids, contacts_updated = import_contact_row(db, org.id, contact_name, raw_email, raw_phone,
        country or "SA", f"{batch.id}:{row.get('_source_sheet', '')}:{source_row}", return_ids=True, metrics=contact_changes)
    for contact_id in contact_ids:
        imported_contact=db.get(Contact,contact_id)
        if imported_contact and contact_designation and not imported_contact.role:
            imported_contact.role=contact_designation[:100]
            contact_changes['updated_ids'].append(contact_id)
        if imported_contact and _clean(row.get("Is Primary Contact")).casefold() in {"yes","y","true","1"}:
            db.query(Contact).filter(Contact.org_id==org.id,Contact.id!=imported_contact.id).update({Contact.is_primary:False},synchronize_session=False)
            imported_contact.is_primary=True
    if contacts_need_review:
        contact_warnings.append(f"Contact data needs review (Excel row {source_row})")
    db.flush()
    # Imported and admin-created supplier records are verified by this authenticated admin operation.
    if org.source == "imported":
        ensure_import_verification(db, org, user)
    if website or extracted_website:
        org.website = org.website or (website or extracted_website)[:300]
    if not org.country and country: org.country=country[:100]
    if not org.cr_number and row.get("CR Number"): org.cr_number=_clean(row.get("CR Number"))[:80]
    if not org.vat_number and row.get("VAT Number"): org.vat_number=_clean(row.get("VAT Number"))[:80]
    if not org.description and row.get("Company Description"): org.description=_clean(row.get("Company Description"))[:5000]
    raw_type = raw_type or " / ".join(part for part in (main_name,sub_name) if part)
    mapping = {"path":path,"raw_trade":trade_keyword,"source_raw_trade":source_raw_trade,"raw_type":raw_type,"raw_brands":_clean(row.get("Brands")),
        "city":city,"description":desc,"unit":unit,"contact_category":contact_category,"contact_material":desc,"contact_ids":contact_ids,
        "contacts_updated":contacts_updated} if raw_trade or raw_type or row.get("Brands") or contact_category else None
    missing_profile = [label for label,value in (("City",org.city),("Address",org.address),("CR Number",org.cr_number),
        ("Contact Person",org.contact_name)) if not value]
    org.profile_status="complete" if not missing_profile and (org.business_email or org.business_phone or emails or phones) else "incomplete"
    missing = [label for label, value in (("Company Name", not org.name.startswith("[Imported supplier placeholder:")),
        ("Email", org.business_email), ("Phone", org.business_phone), ("City", org.city),
        ("Address", org.address), ("Contact Person", org.contact_name)) if not value]
    if metrics is not None:
        metrics['contacts_updated'] = contacts_updated
        metrics['contacts_created_ids'] = contact_changes['created_ids']
        metrics['contacts_updated_ids'] = contact_changes['updated_ids']
        metrics['company_updated'] = bool(before_profile is not None and before_profile != tuple(getattr(org, field) for field in profile_fields))
    return org, created, missing, contact_warnings, contacts_added, mapping


def _process_supplier_capability(db,org,mapping,batch,user,row,number):
    if not mapping:return None
    trade=ensure_trade_record(db,org,batch,raw_trade=mapping["source_raw_trade"],raw_type=mapping["raw_type"],
        raw_brands=mapping["raw_brands"],raw_location=mapping["city"])
    if not mapping["path"]:
        return {"status":"pending","reason":"Category mapping needs review","categories_created":0,
            "materials_created":0,"capability_created":False}
    return apply_supplier_trade_mapping(db,org=org,trade=trade,raw_path=mapping["path"],
        raw_trade=mapping["raw_trade"],city=mapping["city"],description=mapping["description"],
        unit=mapping["unit"],actor=user,batch_id=batch.id,row_number=row.get("_source_row",number),auto_confirm=True)


def _link_contact_capability(db,org_id,contact_ids,capability_id):
    created=0
    for contact_id in dict.fromkeys(contact_ids or []):
        contact=db.scalar(select(Contact).where(Contact.id==contact_id,Contact.org_id==org_id,
            Contact.merged_into_contact_id.is_(None),Contact.deleted_at.is_(None)))
        if not contact:continue
        existing=db.scalar(select(ContactCapability.id).where(ContactCapability.contact_id==contact.id,
            ContactCapability.capability_id==capability_id))
        if not existing:
            db.add(ContactCapability(contact_id=contact.id,capability_id=capability_id,is_primary=contact.is_primary));created+=1
    return created


def _supplier_contact_category_capability(db,org,mapping):
    raw_category=_clean(mapping.get('contact_category'))
    parts=[part.strip() for part in raw_category.split('/') if part.strip()]
    category=None
    if len(parts)>=2:
        main,sub=_find_supplier_category_path(db,parts[-2],parts[-1],{})
        category=sub
    else:
        key=_category_key(raw_category)
        matches=db.scalars(select(Category).where(Category.active.is_(True),or_(
            func.lower(func.trim(Category.name_en))==key,func.lower(func.trim(Category.normalized_name))==key))).all()
        category=matches[0] if len(matches)==1 else None
    if not category:
        return {'status':'pending','reason':f'Category mapping needs review: {raw_category}','capability_created':False,'capability_id':None}
    city=_clean(mapping.get('city'))
    capability=db.scalar(select(Capability).where(Capability.org_id==org.id,Capability.category_id==category.id,
        func.lower(func.trim(Capability.city))==city.casefold()).with_for_update())
    created=capability is None
    if created:
        capability=Capability(org_id=org.id,category_id=category.id,city=city,material_ids=[],trade_keywords='',
            confirmed=True,accepts_rfqs=True,eligibility_status='enabled',mapping_source='admin_import')
        db.add(capability);db.flush()
    material_name=_clean(mapping.get('contact_material'))
    warning=None
    if material_name:
        names = list(dict.fromkeys(part.strip() for part in re.split(r'[;\n]+', material_name) if part.strip()))
        keys = {normalize_text(name) for name in names}
        materials = db.scalars(select(Material).where(Material.active.is_(True),Material.normalized_description.in_(keys),
            or_(Material.category_id==category.id,Material.main_category_id==category.id,Material.subcategory_id==category.id))).all()
        capability.material_ids = list(dict.fromkeys([*(capability.material_ids or []), *(material.id for material in materials)]))
        matched = {material.normalized_description for material in materials}
        missing = [name for name in names if normalize_text(name) not in matched]
        if missing:warning=f'Material was not matched to the selected category: {"; ".join(missing)}'
    return {'status':'saved','reason':None,'capability_id':capability.id,'capability_created':created,
        'material_warning':warning,'categories_created':0,'materials_created':0}


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
    parsed, needs_review = parse_phones(raw, 'SA')
    values=[{"original":item["value"],"value":item["value"],"normalized":item["normalized"],"fax":item["kind"]=="fax"} for item in parsed]
    if needs_review:
        values.append({"original":raw,"value":raw,"normalized":"","fax":bool(re.search(r"(?:^|\s)(?:F|FAX)\s*:",raw,re.I))})
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
    emails = [item["value"].casefold() for item in _supplier_emails(row.get("Contact Email") or row.get("Email")) if item["valid"]]
    if emails: return "email:" + emails[0]
    phones = [item["normalized"] for item in _supplier_phones(row.get("Contact Phone") or row.get("Phone")) if item["normalized"] and not item["fax"]]
    return "phone:" + phones[0] if phones else "unidentified"


def _stream_import(kind, rows, filename, user_id):
    if kind == 'suppliers':
        # Batch suppliers, keeping all of a company's contact/capability rows together.
        from app.modules.imports.supplier_jobs import _groups
        chunks = [[tuple(item) for group in chunk for item in group] for chunk in _chunks(_groups(rows))]
    else:
        chunks = _chunks(rows)
    batch_count = len(chunks); db = SessionLocal()
    issues = []; warnings = []; incomplete_rows = []; imported = skipped = incomplete = completed_rows = 0
    capability_saved = capability_skipped = contacts_created = categories_created = materials_created = contact_assignments_created = contacts_updated = duplicate_rows_skipped = 0; category_cache = {}; unit_cache = {}; seen = set()
    pending_category_mappings = 0
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
                    row_assignments_created=0
                    try:
                        # Commit organization and contacts before attempting this row's capability.
                        with db.begin_nested():
                            result = _supplier_row(db, row, number, batch, db.get(User, user_id), org_cache, cap_cache, {}, material_cache, seen)
                            if result[0] is None:
                                raise ValueError("Company Name or a valid email/phone identifier is required")
                            org, created, missing, contact_warnings, contact_count, mapping = result
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
                    row_contacts_updated=mapping.get('contacts_updated',0) if mapping else 0
                    contacts_updated += row_contacts_updated
                    if created: new_supplier_ids.add(org.id)
                    elif org.id not in new_supplier_ids: reused_supplier_ids.add(org.id)
                    if missing or contact_warnings: incomplete_supplier_ids.add(org.id)
                    if missing:
                        incomplete_rows.append({"row":number, "reason":", ".join(f"{field} missing" for field in missing), "missing_fields":missing})
                        raw_warnings.extend(f"{field} missing" for field in missing)
                    raw_warnings.extend(contact_warnings)
                    cap_status = "skipped"
                    mapping_result=None
                    if mapping:
                        try:
                            with db.begin_nested():
                                if mapping.get('contact_category'):
                                    mapping_result=_supplier_contact_category_capability(db,org,mapping)
                                    if mapping_result.get('status')=='pending':
                                        ensure_trade_record(db,org,batch,raw_trade=mapping.get('source_raw_trade') or mapping['contact_category'],
                                            raw_type=mapping['contact_category'],raw_brands='',raw_location=mapping.get('city',''))
                                else:
                                    mapping_result=_process_supplier_capability(db,org,mapping,batch,db.get(User,user_id),row,number)
                                if mapping_result and mapping_result.get('status')=='saved' and mapping_result.get('capability_id'):
                                    row_assignments_created=_link_contact_capability(db,org.id,mapping.get('contact_ids'),mapping_result['capability_id'])
                                db.flush()
                            db.commit()
                        except Exception as exc:
                            db.rollback()
                            mapping_result={"status":"pending","reason":f"Category mapping needs review ({type(exc).__name__})",
                                "categories_created":0,"materials_created":0,"capability_created":False}
                    contact_assignments_created+=row_assignments_created
                    if mapping_result:
                        if mapping_result.get("status") == "saved":
                            capability_saved += int(mapping_result.get("capability_created", False))
                            categories_created += mapping_result.get("categories_created", 0)
                            materials_created += mapping_result.get("materials_created", 0)
                            cap_status = "saved"
                            if mapping_result.get("material_warning"):
                                capability_skipped += 1
                                raw_warnings.append(mapping_result["material_warning"])
                        else:
                            capability_skipped += 1
                            pending_category_mappings += 1
                            raw_warnings.append(mapping_result.get("reason") or "Category mapping needs review")
                    if not mapping and (row.get("Material Description") or row.get("Main Category") or row.get("Subcategory") or row.get("Category")):
                        capability_skipped += 1
                        raw_warnings.append("Capability source did not contain a reliable category path")
                    if not created and contact_count==0 and row_contacts_updated==0 and not (mapping_result and (mapping_result.get('capability_created') or mapping_result.get('materials_created'))) and not row_assignments_created:
                        duplicate_rows_skipped+=1
                    row_status = "incomplete" if (missing or contact_warnings) else ("created" if created else "existing")
                    issue_text = "; ".join(dict.fromkeys(raw_warnings))
                    if issue_text:
                        warnings.append({"row":number, "reason":issue_text, "capability_saved":cap_status == "saved"})
                    try:
                        db.add(ImportedRow(batch_id=batch.id, row_number=row.get("_source_row",number), raw=row,
                            status="incomplete" if missing else "warning" if issue_text else row_status, issue=issue_text[:300] or None))
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
                "contacts_created":contacts_created,"contacts_updated":contacts_updated,"category_assignments_created":contact_assignments_created,"duplicate_rows_skipped":duplicate_rows_skipped, "capabilities_saved":capability_saved, "capabilities_skipped":capability_skipped,
                "categories_created":categories_created,"materials_created":materials_created,"pending_category_mappings":pending_category_mappings,
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
            "contacts_created":contacts_created,"contacts_updated":contacts_updated,"category_assignments_created":contact_assignments_created,"duplicate_rows_skipped":duplicate_rows_skipped, "capabilities_saved":capability_saved, "capabilities_skipped":capability_skipped,
            "categories_created":categories_created,"materials_created":materials_created,"pending_category_mappings":pending_category_mappings,
            "supplier_failures":len(failed_supplier_keys), "imported":imported, "skipped":skipped,
            "incomplete":len(incomplete_supplier_ids), "failed":len(failed_supplier_keys) if kind == "suppliers" else len(issues),
            "warnings":warnings, "incomplete_rows":incomplete_rows, "issues":issues}) + "\n"
    except Exception as exc:
        db.rollback()
        yield json.dumps({"type":"error","message":"The import failed while writing to the database. Committed progress is preserved."}) + "\n"
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
    book=Workbook(); sheet=book.active; sheet.title="Failed rows"
    sheet.append(["Worksheet", "Excel row", "Reason", "Source data"])
    def safe(value):
        text = str(value or '')
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text
    for item in rows:
        source_rows = item.raw.get('source_rows') or [[item.row_number, item.raw]]
        for number, raw in source_rows:
            sheet.append([safe(raw.get('_source_sheet', '')), number, safe(item.issue),
                          safe(json.dumps(raw, ensure_ascii=False))])
    out=BytesIO();book.save(out);out.seek(0)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{batch.kind}-errors.xlsx"'})


@router.get("/suppliers/{batch_id}/warnings")
def supplier_warning_report(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.kind not in {"suppliers", "supplier_bulk"}: raise HTTPException(404, "Supplier import report not found")
    if batch.kind == 'supplier_bulk':
        book = Workbook(); sheet = book.active; sheet.title = 'Supplier warnings'
        sheet.append(['Excel row', 'Warning'])
        for item in (batch.preview_metadata or {}).get('warnings', []):
            reason = str(item.get('reason') or '')
            sheet.append([item.get('row'), "'" + reason if reason.lstrip().startswith(('=', '+', '-', '@')) else reason])
        out = BytesIO(); book.save(out); out.seek(0)
        return StreamingResponse(out, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="supplier-import-{batch_id}-warnings.xlsx"'})
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
