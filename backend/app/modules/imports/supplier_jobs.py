"""Durable direct supplier uploads, using the existing PostgreSQL import queue.

One ImportedRow is one supplier group. Its status, business records and progress
are committed together. No worker lease survives a transaction or process crash.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from math import ceil
import logging
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ImportBatch, ImportedRow, Material, Organization, User
from app.modules.imports import bulk
from app.modules.materials.service import normalize_text
from app.security import admin_user

router = APIRouter(prefix='/bulk/suppliers/jobs', tags=['Bulk Materials and Suppliers'])
KIND = 'supplier_bulk'
# Serializes identity lookup/create across this queue's workers, including jobs
# for different files. Transaction advisory locks work with Neon transaction pools.
WORKER_LOCK = 717341392
logger = logging.getLogger(__name__)


class SourceIssue(BaseModel):
    row: int
    reason: str
    sheet: str | None = None
    company_name: str | None = None
    field: str | None = None
    code: str | None = None
    status: str | None = None


class SupplierJobProgress(BaseModel):
    batch_id: str
    filename: str = ''
    file_checksum: str = ''
    status: str
    stage: str
    message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    total_rows: int
    processed_rows: int = 0
    total_supplier_records_identified: int
    new_suppliers_estimated: int = 0
    existing_suppliers_estimated: int = 0
    contacts_detected: int = 0
    disciplines_detected: int = 0
    categories_detected: int = 0
    subcategories_detected: int = 0
    material_values_detected: int = 0
    existing_materials_matched: int = 0
    unresolved_material_mappings: int = 0
    brands_detected: int = 0
    duplicate_source_rows: int = 0
    valid_rows: int = 0
    warning_rows: int = 0
    review_required_rows: int = 0
    invalid_rows: int = 0
    batch_size: int
    batches_total: int
    completed_batches: int
    progress_percent: float
    new_suppliers_saved: int = 0
    existing_suppliers_reused: int = 0
    suppliers_updated: int = 0
    suppliers_incomplete: int = 0
    contacts_created: int = 0
    contacts_updated: int = 0
    responsibilities_created: int = 0
    responsibilities_reused: int = 0
    capabilities_reused: int = 0
    category_assignments_created: int = 0
    capabilities_saved: int = 0
    capabilities_skipped: int = 0
    material_mappings_created: int = 0
    categories_created: int = 0
    materials_created: int = 0
    pending_category_mappings: int = 0
    pending_material_mappings: int = 0
    imported: int = 0
    skipped: int = 0
    incomplete: int = 0
    failed: int = 0
    supplier_failures: int = 0
    failed_rows: int = 0
    issues: list[SourceIssue] = Field(default_factory=list)
    warnings: list[SourceIssue] = Field(default_factory=list)


def _lock(db: Session, key: int):
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def _groups(rows):
    groups = {}
    for number, row in rows:
        key = bulk._supplier_identity_key(row)
        if key == 'unidentified':
            key = f"unidentified:{row.get('_source_sheet', '')}:{number}"
        groups.setdefault(key, []).append([number, row])
    return list(groups.values())


def _chunked(seq, size=500):
    items = list(seq)
    for idx in range(0, len(items), size):
        yield items[idx:idx + size]


def _compute_preview_stats(db: Session, rows: list[tuple[int, dict]], groups: list[list], *, fallback_discipline: str | None = None) -> dict:
    supplier_name_keys: set[str] = set()
    contacts: set[tuple[str, str]] = set()
    disciplines: set[str] = set()
    categories: set[str] = set()
    subcategories: set[tuple[str, str]] = set()
    material_norms: set[str] = set()
    brands: set[str] = set()
    seen_row_sigs: set[tuple] = set()
    duplicate_source_rows = 0
    valid_rows = 0
    warning_rows = 0
    review_required_rows = 0
    invalid_rows = 0

    cat_lookup = bulk.build_category_lookup(db)
    cat_cache = {'_lookup': cat_lookup}
    norm_fallback_disc = bulk.normalize_discipline(fallback_discipline) if fallback_discipline else ''

    for _, row in rows:
        name = bulk.normalize_company_name(row.get('Company Name'))
        country = bulk._clean(row.get('Country'))
        raw_email = row.get('Contact Email') or row.get('Email') or ''
        raw_phone = row.get('Contact Phone') or row.get('Phone') or ''
        contact_person = bulk._clean(row.get('Contact Person'))
        ident = bulk._supplier_identity_key(row)

        if name:
            supplier_name_keys.add(name)
        if contact_person or raw_email or raw_phone:
            contacts.add((ident, (raw_email or raw_phone or contact_person).casefold()))

        raw_disc = bulk._clean(row.get('Discipline'))
        disc = bulk.normalize_discipline(raw_disc) if raw_disc else ''
        if disc:
            disciplines.add(disc)
        elif norm_fallback_disc:
            disciplines.add(norm_fallback_disc)

        cat = bulk._clean(row.get('Category') or row.get('Main Category'))
        sub = bulk._clean(row.get('Subcategory'))
        if cat:
            categories.add(cat.casefold())
        if sub:
            subcategories.add((cat.casefold(), sub.casefold()))

        mats = bulk.parse_semicolon_list(row.get('Materials') or row.get('Material') or row.get('Material Description'))
        for m in mats:
            norm_m = normalize_text(m)
            if norm_m:
                material_norms.add(norm_m)

        brs = bulk.parse_brands(row.get('Brands'))
        for b in brs:
            brands.add(b.casefold())

        sig = (ident, contact_person.casefold(), str(raw_email).casefold(), str(raw_phone).casefold(),
               (disc or raw_disc).casefold(), cat.casefold(), sub.casefold(), tuple(m.casefold() for m in mats))
        if sig in seen_row_sigs:
            duplicate_source_rows += 1
        else:
            seen_row_sigs.add(sig)

        if ident == 'unidentified':
            invalid_rows += 1
            continue

        row_review = False
        row_warn = False
        if raw_disc and not disc:
            row_review = True
        if cat and sub:
            main_c, sub_c = bulk._find_supplier_category_path(db, cat, sub, cat_cache, discipline=disc or norm_fallback_disc)
            if not main_c or not sub_c:
                row_review = True
        elif cat and not sub and (row.get('Discipline') or row.get('Brands')):
            main_c, _ = bulk._find_supplier_category_path(db, cat, '', cat_cache, discipline=disc or norm_fallback_disc)
            if not main_c:
                row_review = True

        if not country and not row_review:
            row_warn = True

        if row_review:
            review_required_rows += 1
        else:
            valid_rows += 1
            if row_warn:
                warning_rows += 1

    existing_org_names: set[str] = set()
    for chunk in _chunked(supplier_name_keys):
        if chunk:
            found = db.scalars(select(Organization.name_normalized).where(
                Organization.is_supplier.is_(True),
                Organization.is_active.is_(True),
                Organization.name_normalized.in_(chunk),
            )).all()
            existing_org_names.update(n for n in found if n)

    existing_suppliers_estimated = len(existing_org_names)
    new_suppliers_estimated = max(0, len(groups) - existing_suppliers_estimated)

    existing_mat_norms: set[str] = set()
    for chunk in _chunked(material_norms):
        if chunk:
            found = db.scalars(select(Material.normalized_description).where(
                Material.active.is_(True),
                Material.normalized_description.in_(chunk),
            )).all()
            existing_mat_norms.update(n for n in found if n)

    existing_materials_matched = len(existing_mat_norms)
    unresolved_material_mappings = max(0, len(material_norms) - existing_materials_matched)

    return {
        'total_rows': len(rows),
        'processed_rows': 0,
        'total_supplier_records_identified': len(groups),
        'new_suppliers_estimated': new_suppliers_estimated,
        'existing_suppliers_estimated': existing_suppliers_estimated,
        'contacts_detected': len(contacts),
        'disciplines_detected': len(disciplines),
        'categories_detected': len(categories),
        'subcategories_detected': len(subcategories),
        'material_values_detected': len(material_norms),
        'existing_materials_matched': existing_materials_matched,
        'unresolved_material_mappings': unresolved_material_mappings,
        'brands_detected': len(brands),
        'duplicate_source_rows': duplicate_source_rows,
        'valid_rows': valid_rows,
        'warning_rows': warning_rows,
        'review_required_rows': review_required_rows,
        'invalid_rows': invalid_rows,
        'suppliers_incomplete': 0,
        'new_suppliers_saved': 0,
        'existing_suppliers_reused': 0,
        'suppliers_updated': 0,
        'contacts_created': 0,
        'contacts_updated': 0,
        'category_assignments_created': 0,
        'capabilities_saved': 0,
        'capabilities_reused': 0,
        'capabilities_skipped': 0,
        'material_mappings_created': 0,
        'categories_created': 0,
        'materials_created': 0,
        'pending_category_mappings': 0,
        'pending_material_mappings': 0,
        'responsibilities_created': 0,
        'responsibilities_reused': 0,
        'issues': [],
        'warnings': [],
    }


def _snapshot(batch: ImportBatch) -> dict:
    value = dict(batch.preview_metadata or {})
    total = value.get('total_supplier_records_identified', 0)
    size = (batch.import_options or {}).get('batch_size', 10)
    done = batch.completed_rows or 0
    if batch.status == 'preview_ready':
        pct = 0.0
    elif total:
        pct = round(min(100.0, done / total * 100), 2)
    else:
        pct = 100.0
    value.update(
        batch_id=batch.id,
        filename=batch.filename or '',
        file_checksum=batch.source_sha256 or '',
        created_at=batch.created_at.isoformat() if batch.created_at else None,
        status=batch.status,
        stage=batch.stage,
        batch_size=size,
        batches_total=ceil(total / size) if total else 0,
        completed_batches=ceil(total / size) if total and done >= total else (done // size if size else 0),
        progress_percent=pct,
        failed=batch.errors,
        supplier_failures=batch.errors,
        imported=batch.created,
        skipped=batch.unchanged_count,
        incomplete=value.get('suppliers_incomplete', 0),
        message=batch.failure_reason,
    )
    return value


def _job(db: Session, batch_id: str) -> ImportBatch:
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.kind == KIND))
    if not batch:
        raise HTTPException(404, 'Supplier import job not found')
    return batch


@router.get('', response_model=list[SupplierJobProgress])
def list_jobs(
    limit: int = Query(default=15, ge=1, le=50),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    batches = db.scalars(
        select(ImportBatch)
        .where(ImportBatch.kind == KIND)
        .order_by(ImportBatch.created_at.desc())
        .limit(limit)
    ).all()
    return [_snapshot(b) for b in batches]


@router.post('', status_code=202, response_model=SupplierJobProgress)
def enqueue(
    file: UploadFile = File(...),
    fallback_discipline: str | None = Form(None),
    default_country: str | None = Form(None),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    return _enqueue(file, user, db, preview=False, fallback_discipline=fallback_discipline, default_country=default_country)


@router.post('/preview', status_code=202, response_model=SupplierJobProgress)
def preview(
    file: UploadFile = File(...),
    fallback_discipline: str | None = Form(None),
    default_country: str | None = Form(None),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    return _enqueue(file, user, db, preview=True, fallback_discipline=fallback_discipline, default_country=default_country)


def _enqueue(
    file: UploadFile,
    user: User,
    db: Session,
    *,
    preview: bool,
    fallback_discipline: str | None = None,
    default_country: str | None = None,
):
    t0 = time.monotonic()
    content = file.file.read(get_settings().supplier_import_max_bytes + 1)
    digest = sha256(content).hexdigest()
    rows = bulk._read(content, file.filename or '', bulk.SUPPLIER_HEADERS)
    groups = _groups(rows)
    # Same-file concurrent uploads return the same durable job.
    _lock(db, int(digest[:15], 16))
    existing = db.scalar(select(ImportBatch).where(
        ImportBatch.kind == KIND,
        ImportBatch.initiated_by == user.id,
        ImportBatch.source_sha256 == digest,
        ImportBatch.status.in_(['queued', 'processing']),
    )) if not preview else None
    if existing:
        return _snapshot(existing)

    stats = _compute_preview_stats(db, rows, groups, fallback_discipline=fallback_discipline)
    now_iso = datetime.now(UTC).isoformat()
    if not preview:
        stats['started_at'] = now_iso

    batch = ImportBatch(
        kind=KIND,
        filename=(file.filename or 'suppliers.xlsx')[:200],
        status='preview_ready' if preview else 'queued',
        stage='preview' if preview else 'processing_suppliers',
        rows=len(groups),
        initiated_by=user.id,
        source_sha256=digest,
        source_bytes=len(content),
        import_options={
            'batch_size': get_settings().supplier_import_batch_size,
            'fallback_discipline': bulk.normalize_discipline(fallback_discipline) if fallback_discipline else '',
            'default_country': bulk._clean(default_country),
        },
        preview_metadata=stats,
    )
    db.add(batch)
    db.flush()
    for group in groups:
        db.add(ImportedRow(
            batch_id=batch.id,
            row_number=group[0][0],
            raw={'source_rows': group},
            status='pending',
        ))
    db.commit()
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    logger.info(
        'Supplier import job created batch_id=%s mode=%s rows=%s groups=%s elapsed_ms=%s',
        batch.id, 'preview' if preview else 'queued', len(rows), len(groups), elapsed_ms,
    )
    return _snapshot(batch)


@router.post('/{batch_id}/start', status_code=202, response_model=SupplierJobProgress)
def start(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.scalar(select(ImportBatch).where(
        ImportBatch.id == batch_id,
        ImportBatch.kind == KIND,
    ).with_for_update())
    if not batch:
        raise HTTPException(404, 'Supplier import job not found')
    if batch.status != 'preview_ready':
        raise HTTPException(409, 'Only a preview-ready job can be started')
    batch.status = 'queued'
    batch.stage = 'processing_suppliers'
    metadata = dict(batch.preview_metadata or {})
    metadata['started_at'] = datetime.now(UTC).isoformat()
    batch.preview_metadata = metadata
    db.commit()
    logger.info('Supplier import job started batch_id=%s groups=%s', batch.id, batch.rows)
    return _snapshot(batch)


@router.post('/{batch_id}/cancel', response_model=SupplierJobProgress)
def cancel(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.scalar(select(ImportBatch).where(
        ImportBatch.id == batch_id,
        ImportBatch.kind == KIND,
    ).with_for_update())
    if not batch:
        raise HTTPException(404, 'Supplier import job not found')
    if batch.status in {'completed', 'completed_with_errors', 'cancelled'}:
        return _snapshot(batch)
    batch.status = 'cancelled'
    batch.stage = 'cancelled'
    batch.failure_reason = 'Cancelled by administrator. Previously committed batches are preserved.'
    metadata = dict(batch.preview_metadata or {})
    metadata['completed_at'] = datetime.now(UTC).isoformat()
    batch.preview_metadata = metadata
    db.commit()
    logger.info('Supplier import job cancelled batch_id=%s completed_groups=%s', batch.id, batch.completed_rows)
    return _snapshot(batch)


@router.get('/{batch_id}', response_model=SupplierJobProgress)
def progress(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    return _snapshot(_job(db, batch_id))


@router.post('/{batch_id}/retry', status_code=202, response_model=SupplierJobProgress)
def retry(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.scalar(select(ImportBatch).where(
        ImportBatch.id == batch_id,
        ImportBatch.kind == KIND,
    ).with_for_update())
    if not batch:
        raise HTTPException(404, 'Supplier import job not found')
    if batch.status in {'queued', 'processing'}:
        return _snapshot(batch)
    failed = db.scalars(select(ImportedRow).where(
        ImportedRow.batch_id == batch.id,
        ImportedRow.status == 'invalid',
    )).all()
    if batch.status == 'completed' and not failed:
        return _snapshot(batch)
    for item in failed:
        item.status = 'pending'
        item.issue = None
        if isinstance(item.raw, dict) and 'row_issues' in item.raw:
            raw_copy = dict(item.raw)
            raw_copy.pop('row_issues', None)
            item.raw = raw_copy
    batch.completed_rows = max(0, (batch.completed_rows or 0) - len(failed))
    batch.errors = 0
    batch.retry_count = 0
    batch.failure_reason = None
    batch.status = 'queued'
    batch.stage = 'processing_suppliers'
    metadata = dict(batch.preview_metadata or {})
    metadata['issues'] = []
    metadata['failed_rows'] = 0
    metadata.pop('completed_at', None)
    batch.preview_metadata = metadata
    db.commit()
    logger.info('Supplier import job retry queued batch_id=%s retried_groups=%s', batch.id, len(failed))
    return _snapshot(batch)


@router.get('/{batch_id}/review-report')
def review_report(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = _job(db, batch_id)
    rows = db.scalars(
        select(ImportedRow)
        .where(ImportedRow.batch_id == batch.id)
        .order_by(ImportedRow.row_number, ImportedRow.id)
    ).all()

    report_rows: list[list] = []
    for item in rows:
        source_rows = (item.raw or {}).get('source_rows') or []
        source_by_num = {num: raw for num, raw in source_rows}
        row_issues = (item.raw or {}).get('row_issues') or []

        if item.status == 'invalid' and not row_issues:
            for num, raw in source_rows:
                report_rows.append(_format_review_row(raw, num, 'FAILED', 'Company Name', 'IMPORT_FAILED', item.issue or 'Row failed to import'))
        else:
            for issue in row_issues:
                num = issue.get('row', item.row_number)
                raw = source_by_num.get(num) or (source_rows[0][1] if source_rows else {})
                report_rows.append(_format_review_row(
                    raw,
                    num,
                    issue.get('status', 'REVIEW_REQUIRED'),
                    issue.get('field', ''),
                    issue.get('code', 'REVIEW_REQUIRED'),
                    issue.get('reason', ''),
                ))

    book = Workbook()
    sheet = book.active
    sheet.title = 'Review & Failed Rows'
    headers = [*bulk.SUPPLIER_TEMPLATE_HEADERS, 'Source Row', 'Import Status', 'Issue Field', 'Issue Code', 'Issue Reason']
    sheet.append(headers)
    for r in report_rows:
        sheet.append(r)

    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='17365D')
        cell.alignment = Alignment(wrap_text=True)
    for col in sheet.columns:
        letter = col[0].column_letter
        sheet.column_dimensions[letter].width = min(44, max(14, max(len(str(c.value or '')) for c in col) + 2))

    out = BytesIO()
    book.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="supplier-review-report-{batch.id}.xlsx"'},
    )


def _format_review_row(raw: dict, row_number: int, status: str, field: str, code: str, reason: str) -> list:
    materials_val = raw.get('Materials') or raw.get('Material') or raw.get('Material Description') or ''
    category_val = raw.get('Category') or raw.get('Main Category') or ''
    values = [
        raw.get('Company Name', ''),
        raw.get('Country', ''),
        raw.get('City', ''),
        raw.get('Address', ''),
        raw.get('Website', ''),
        raw.get('CR Number', ''),
        raw.get('VAT Number', ''),
        raw.get('Company Description', ''),
        raw.get('Contact Person', ''),
        raw.get('Contact Designation', ''),
        raw.get('Contact Email') or raw.get('Email', ''),
        raw.get('Contact Phone') or raw.get('Phone', ''),
        raw.get('Discipline', ''),
        category_val,
        raw.get('Subcategory', ''),
        materials_val,
        raw.get('Brands', ''),
    ]
    return [*(bulk.excel_safe(v) for v in values), row_number, status, bulk.excel_safe(field), bulk.excel_safe(code), bulk.excel_safe(reason)]


def _preload_chunk_material_cache(db: Session, items: list[ImportedRow]) -> dict:
    all_keys: set[str] = set()
    for item in items:
        for _, row in (item.raw or {}).get('source_rows', ()):
            mat_str = row.get('Materials') or row.get('Material') or row.get('Material Description') or ''
            if mat_str:
                for name in bulk.parse_semicolon_list(mat_str):
                    norm = normalize_text(name)
                    if norm:
                        all_keys.add(norm)
    by_norm: dict[str, list[tuple]] = {}
    for chunk in _chunked(all_keys, 500):
        if not chunk:
            continue
        rows = db.execute(
            select(
                Material.id,
                Material.normalized_description,
                Material.category_id,
                Material.main_category_id,
                Material.subcategory_id,
            ).where(Material.active.is_(True), Material.normalized_description.in_(chunk))
        ).all()
        for m_id, norm_desc, cat_id, main_id, sub_id in rows:
            by_norm.setdefault(norm_desc, []).append((m_id, cat_id, main_id, sub_id))
    return {'_by_norm': by_norm}


from contextvars import ContextVar

_CHUNK_CACHES: ContextVar[tuple[dict, dict] | None] = ContextVar('_CHUNK_CACHES', default=None)


def _apply_group(db: Session, batch: ImportBatch, item: ImportedRow, user: User):
    cached = _CHUNK_CACHES.get()
    if cached is not None:
        category_cache, material_cache = cached
    else:
        category_cache, material_cache = {}, {}
    counts = {key: 0 for key in (
        'contacts_created', 'contacts_updated', 'category_assignments_created',
        'capabilities_saved', 'categories_created', 'materials_created',
        'pending_category_mappings', 'capabilities_skipped',
        'responsibilities_created', 'responsibilities_reused',
        'capabilities_reused', 'pending_material_mappings',
        'material_mappings_created', 'processed_rows',
    )}
    created_supplier = False
    updated = False
    warnings = []
    row_issues = []
    created_contact_ids, updated_contact_ids = set(), set()
    options = batch.import_options or {}
    fallback_discipline = options.get('fallback_discipline') or None
    default_country = options.get('default_country') or None

    # org_cache and cap_cache live only inside this savepoint so rolled-back entities never leak.
    org_cache, cap_cache = {}, {}
    source_rows = item.raw.get('source_rows', [])
    for number, row in source_rows:
        counts['processed_rows'] += 1
        contact_metrics = {}
        result = bulk._supplier_row(
            db, row, number, batch, user, org_cache, cap_cache, category_cache, material_cache, set(), contact_metrics,
            fallback_discipline=fallback_discipline, default_country=default_country,
        )
        if result[0] is None:
            raise ValueError('Company Name or a valid email/phone identifier is required')
        org, created, missing, contact_warnings, contact_count, mapping = result
        created_supplier |= created
        updated |= contact_metrics.get('company_updated', False)
        counts['responsibilities_created'] += contact_metrics.get('responsibilities_created', 0)
        counts['responsibilities_reused'] += contact_metrics.get('responsibilities_reused', 0)
        created_contact_ids.update(contact_metrics.get('contacts_created_ids', []))
        updated_contact_ids.update(contact_metrics.get('contacts_updated_ids', []))

        company_name = bulk._clean(row.get('Company Name')) or org.name
        for reason in contact_warnings:
            issue_obj = {
                'row': number,
                'sheet': row.get('_source_sheet', ''),
                'company_name': company_name,
                'field': 'Contact Email' if 'Email' in reason else 'Contact Phone' if 'Phone' in reason else 'Contact',
                'code': 'CONTACT_WARNING',
                'status': 'WARNING',
                'reason': reason,
            }
            warnings.append(issue_obj)
            row_issues.append(issue_obj)

        if mapping:
            if mapping.get('contact_category'):
                mapped = bulk._supplier_contact_category_capability(
                    db, org, mapping, category_cache=category_cache, material_cache=material_cache,
                )
            else:
                mapped = bulk._process_supplier_capability(
                    db, org, mapping, batch, user, row, number,
                    category_cache=category_cache, material_cache=material_cache,
                )
            if mapped and mapped.get('status') == 'saved':
                counts['capabilities_saved'] += int(mapped.get('capability_created', False))
                counts['capabilities_reused'] += int(not mapped.get('capability_created', False))
                counts['categories_created'] += mapped.get('categories_created', 0)
                counts['materials_created'] += mapped.get('materials_created', 0)
                counts['material_mappings_created'] += mapped.get('material_mappings_created', 0)
                if mapped.get('capability_id'):
                    counts['category_assignments_created'] += bulk._link_contact_capability(
                        db, org.id, mapping.get('contact_ids'), mapped['capability_id'], mapped.get('material_ids'),
                    )
                if mapped.get('material_warning'):
                    counts['pending_material_mappings'] += 1
                    issue_obj = {
                        'row': number,
                        'sheet': row.get('_source_sheet', ''),
                        'company_name': company_name,
                        'field': 'Materials',
                        'code': 'UNMATCHED_MATERIAL',
                        'status': 'REVIEW_REQUIRED',
                        'reason': mapped['material_warning'],
                    }
                    warnings.append(issue_obj)
                    row_issues.append(issue_obj)
            else:
                counts['pending_category_mappings'] += 1
                counts['capabilities_skipped'] += 1
                issue_obj = {
                    'row': number,
                    'sheet': row.get('_source_sheet', ''),
                    'company_name': company_name,
                    'field': (mapped or {}).get('field') or 'Category',
                    'code': (mapped or {}).get('code') or 'UNRESOLVED_CATEGORY',
                    'status': 'REVIEW_REQUIRED',
                    'reason': (mapped or {}).get('reason') or 'Category mapping needs review',
                }
                warnings.append(issue_obj)
                row_issues.append(issue_obj)
        db.flush()

    counts['new_suppliers_saved'] = int(created_supplier)
    counts['contacts_created'] = len(created_contact_ids)
    counts['contacts_updated'] = len(updated_contact_ids - created_contact_ids)
    counts['existing_suppliers_reused'] = int(not created_supplier)
    counts['suppliers_updated'] = int(updated and not created_supplier)
    counts['suppliers_incomplete'] = int(bool(warnings))
    if row_issues:
        item.raw = {**item.raw, 'row_issues': row_issues}
    return counts, warnings


def process_supplier_jobs(db: Session):
    """Process at most one configured chunk; business data and checkpoint commit together."""
    t0 = time.monotonic()
    try:
        _lock(db, WORKER_LOCK)
        batch = db.scalar(
            select(ImportBatch)
            .where(ImportBatch.kind == KIND, ImportBatch.status == 'queued')
            .order_by(ImportBatch.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not batch:
            db.rollback()
            return []
        batch_id = batch.id
        size = (batch.import_options or {}).get('batch_size', 10)
        items = db.scalars(
            select(ImportedRow)
            .where(ImportedRow.batch_id == batch.id, ImportedRow.status == 'pending')
            .order_by(ImportedRow.created_at, ImportedRow.id)
            .limit(size)
        ).all()
        metadata = deepcopy(batch.preview_metadata or {})
        if not metadata.get('started_at'):
            metadata['started_at'] = datetime.now(UTC).isoformat()

        user = db.get(User, batch.initiated_by)
        if not user or not user.is_admin or not user.is_active:
            batch.status = 'failed'
            batch.failure_reason = 'The initiating administrator no longer has import permission.'
            metadata['completed_at'] = datetime.now(UTC).isoformat()
            batch.preview_metadata = metadata
            db.commit()
            return []

        # Preload active taxonomy and chunk materials once per batch to avoid N+1 queries.
        category_cache = {'_lookup': bulk.build_category_lookup(db)}
        material_cache = _preload_chunk_material_cache(db, items)
        cache_token = _CHUNK_CACHES.set((category_cache, material_cache))
        try:
            for item in items:
                try:
                    with db.begin_nested():
                        counts, warnings = _apply_group(db, batch, item, user)
                        item.status = 'saved'
                        db.flush()
                    for key, count in counts.items():
                        metadata[key] = metadata.get(key, 0) + count
                    if warnings:
                        metadata['warnings'] = (metadata.get('warnings', []) + warnings)[-100:]
                except IntegrityError:
                    logger.warning('Supplier import conflict batch_id=%s source_id=%s', batch.id, item.id)
                    item.status = 'invalid'
                    item.issue = 'Supplier data conflicts with an existing record; review this supplier group.'
                    batch.errors += 1
                    source_rows = (item.raw or {}).get('source_rows') or []
                    metadata['failed_rows'] = metadata.get('failed_rows', 0) + len(source_rows)
                    metadata['processed_rows'] = metadata.get('processed_rows', 0) + len(source_rows)
                    issue_obj = {
                        'row': item.row_number,
                        'company_name': bulk._clean(source_rows[0][1].get('Company Name')) if source_rows else '',
                        'field': 'Company Name',
                        'code': 'IDENTITY_CONFLICT',
                        'status': 'FAILED',
                        'reason': item.issue,
                    }
                    item.raw = {**item.raw, 'row_issues': [issue_obj]}
                    metadata['issues'] = (metadata.get('issues', []) + [issue_obj])[-100:]
                except (DBAPIError, PoolTimeoutError):
                    # Infrastructure failures retry the whole uncommitted chunk.
                    raise
                except Exception as exc:
                    logger.warning('Supplier import row failed batch_id=%s source_id=%s type=%s', batch.id, item.id, type(exc).__name__)
                    item.status = 'invalid'
                    item.issue = (
                        str(exc)[:280] if isinstance(exc, ValueError)
                        else 'Supplier data could not be saved; review this supplier group.'
                    )
                    batch.errors += 1
                    source_rows = (item.raw or {}).get('source_rows') or []
                    metadata['failed_rows'] = metadata.get('failed_rows', 0) + len(source_rows)
                    metadata['processed_rows'] = metadata.get('processed_rows', 0) + len(source_rows)
                    issue_obj = {
                        'row': item.row_number,
                        'company_name': bulk._clean(source_rows[0][1].get('Company Name')) if source_rows else '',
                        'field': 'Company Name',
                        'code': 'VALIDATION_ERROR' if isinstance(exc, ValueError) else 'DATABASE_ERROR',
                        'status': 'FAILED',
                        'reason': item.issue,
                    }
                    item.raw = {**item.raw, 'row_issues': [issue_obj]}
                    metadata['issues'] = (metadata.get('issues', []) + [issue_obj])[-100:]
                batch.completed_rows += 1
        finally:
            _CHUNK_CACHES.reset(cache_token)

        batch.preview_metadata = metadata
        batch.created = metadata.get('new_suppliers_saved', 0)
        batch.unchanged_count = metadata.get('existing_suppliers_reused', 0)
        batch.updated_count = metadata.get('suppliers_updated', 0)
        if batch.completed_rows >= metadata.get('total_supplier_records_identified', 0):
            has_issues = bool(
                batch.errors
                or metadata.get('capabilities_skipped')
                or metadata.get('pending_category_mappings')
                or metadata.get('pending_material_mappings')
            )
            batch.status = 'completed_with_errors' if has_issues else 'completed'
            batch.stage = 'complete'
            metadata['completed_at'] = datetime.now(UTC).isoformat()
            batch.preview_metadata = metadata
        batch.failure_reason = None
        batch.retry_count = 0
        db.commit()
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            'Supplier import batch completed batch_id=%s processed_groups=%s total_groups=%s status=%s elapsed_ms=%s',
            batch_id, len(items), metadata.get('total_supplier_records_identified', 0), batch.status, elapsed_ms,
        )
        return [batch_id]
    except Exception as exc:
        logger.warning(
            'Supplier import chunk rolled back batch_id=%s type=%s sqlstate=%s',
            locals().get('batch_id', 'unclaimed'),
            type(exc).__name__,
            getattr(getattr(exc, 'orig', None), 'sqlstate', None),
        )
        db.rollback()
        if 'batch_id' in locals():
            batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update())
            if batch and batch.status == 'queued':
                batch.retry_count += 1
                batch.failure_reason = 'Database temporarily unavailable while saving suppliers. Committed progress is preserved.'
                if batch.retry_count >= 3:
                    batch.status = 'failed'
                db.commit()
        return []
