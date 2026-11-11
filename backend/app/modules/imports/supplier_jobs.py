"""Durable direct supplier uploads, using the existing PostgreSQL import queue.

One ImportedRow is one supplier group. Its status, business records and progress
are committed together. No worker lease survives a transaction or process crash.
"""
from copy import deepcopy
from hashlib import sha256
from math import ceil
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database import get_db
from app.models import ImportBatch, ImportedRow, User
from app.security import admin_user
from app.modules.imports import bulk

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


class SupplierJobProgress(BaseModel):
    batch_id: str
    status: str
    stage: str
    message: str | None = None
    total_rows: int
    total_supplier_records_identified: int
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
    category_assignments_created: int = 0
    capabilities_saved: int = 0
    capabilities_skipped: int = 0
    categories_created: int = 0
    materials_created: int = 0
    pending_category_mappings: int = 0
    imported: int = 0
    skipped: int = 0
    incomplete: int = 0
    failed: int = 0
    supplier_failures: int = 0
    failed_rows: int = 0
    issues: list[SourceIssue] = Field(default_factory=list)
    warnings: list[SourceIssue] = Field(default_factory=list)


def _lock(db, key):
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


def _snapshot(batch):
    value = dict(batch.preview_metadata or {})
    total = value.get('total_supplier_records_identified', 0)
    size = (batch.import_options or {}).get('batch_size', 10)
    done = batch.completed_rows or 0
    value.update(batch_id=batch.id, status=batch.status, stage=batch.stage,
                 batch_size=size, batches_total=ceil(total / size),
                 completed_batches=ceil(total / size) if done >= total else done // size,
                 progress_percent=round(done / total * 100, 2) if total else 100,
                 failed=batch.errors, supplier_failures=batch.errors,
                 imported=batch.created, skipped=batch.unchanged_count,
                 incomplete=value.get('suppliers_incomplete', 0),
                 message=batch.failure_reason)
    return value


def _job(db, batch_id):
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.kind == KIND))
    if not batch:
        raise HTTPException(404, 'Supplier import job not found')
    return batch


@router.post('', status_code=202, response_model=SupplierJobProgress)
def enqueue(file: UploadFile = File(...), user: User = Depends(admin_user), db: Session = Depends(get_db)):
    content = file.file.read(8_000_001)
    digest = sha256(content).hexdigest()
    rows = bulk._read(content, file.filename or '', bulk.SUPPLIER_HEADERS)
    groups = _groups(rows)
    # Same-file concurrent uploads return the same durable job.
    _lock(db, int(digest[:15], 16))
    existing = db.scalar(select(ImportBatch).where(ImportBatch.kind == KIND,
        ImportBatch.initiated_by == user.id, ImportBatch.source_sha256 == digest))
    if existing:
        return _snapshot(existing)
    batch = ImportBatch(kind=KIND, filename=(file.filename or 'suppliers.xlsx')[:200],
        status='queued', stage='processing_suppliers', rows=len(groups), initiated_by=user.id,
        source_sha256=digest, source_bytes=len(content),
        import_options={'batch_size': get_settings().supplier_import_batch_size},
        preview_metadata={'total_rows': len(rows), 'total_supplier_records_identified': len(groups), 'suppliers_incomplete': 0,
            'new_suppliers_saved': 0, 'existing_suppliers_reused': 0, 'suppliers_updated': 0,
            'contacts_created': 0, 'contacts_updated': 0, 'category_assignments_created': 0,
            'capabilities_saved': 0, 'categories_created': 0, 'materials_created': 0,
            'pending_category_mappings': 0, 'issues': [], 'warnings': []})
    db.add(batch)
    db.flush()
    for group in groups:
        db.add(ImportedRow(batch_id=batch.id, row_number=group[0][0],
                           raw={'source_rows': group}, status='pending'))
    db.commit()
    return _snapshot(batch)


@router.get('/{batch_id}', response_model=SupplierJobProgress)
def progress(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    return _snapshot(_job(db, batch_id))


@router.post('/{batch_id}/retry', status_code=202, response_model=SupplierJobProgress)
def retry(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id,
        ImportBatch.kind == KIND).with_for_update())
    if not batch:
        raise HTTPException(404, 'Supplier import job not found')
    if batch.status in {'queued', 'processing'}:
        return _snapshot(batch)
    failed = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch.id,
        ImportedRow.status == 'invalid')).all()
    if batch.status == 'completed' and not failed:
        return _snapshot(batch)
    for item in failed:
        item.status = 'pending'
        item.issue = None
    batch.completed_rows -= len(failed)
    batch.errors = 0
    batch.retry_count = 0
    batch.failure_reason = None
    batch.status = 'queued'
    batch.stage = 'processing_suppliers'
    metadata = dict(batch.preview_metadata or {})
    metadata['issues'] = []
    metadata['failed_rows'] = 0
    batch.preview_metadata = metadata
    db.commit()
    return _snapshot(batch)


def _apply_group(db, batch, item, user):
    counts = {key: 0 for key in ('contacts_created', 'contacts_updated',
        'category_assignments_created', 'capabilities_saved', 'categories_created',
        'materials_created', 'pending_category_mappings', 'capabilities_skipped')}
    created_supplier = False
    updated = False
    warnings = []
    created_contact_ids, updated_contact_ids = set(), set()
    # Caches live only inside this savepoint. Rolled-back entities never leak
    # into subsequent supplier groups.
    org_cache, cap_cache, material_cache = {}, {}, {}
    for number, row in item.raw['source_rows']:
        contact_metrics = {}
        result = bulk._supplier_row(db, row, number, batch, user, org_cache, cap_cache, {}, material_cache, set(), contact_metrics)
        if result[0] is None:
            raise ValueError('Company Name or a valid email/phone identifier is required')
        org, created, missing, contact_warnings, contact_count, mapping = result
        created_supplier |= created
        updated |= contact_metrics.get('company_updated', False)
        created_contact_ids.update(contact_metrics.get('contacts_created_ids', []))
        updated_contact_ids.update(contact_metrics.get('contacts_updated_ids', []))
        warnings.extend({'row': number, 'sheet': row.get('_source_sheet', ''), 'reason': reason}
                        for reason in [*(f'{field} missing' for field in missing), *contact_warnings])
        if mapping:
            if mapping.get('contact_category'):
                mapped = bulk._supplier_contact_category_capability(db, org, mapping)
                if mapped.get('status') != 'saved':
                    bulk.ensure_trade_record(db, org, batch,
                        raw_trade=mapping.get('source_raw_trade') or mapping['contact_category'],
                        raw_type=mapping['contact_category'], raw_brands='', raw_location=mapping.get('city', ''))
            else:
                mapped = bulk._process_supplier_capability(db, org, mapping, batch, user, row, number)
            if mapped and mapped.get('status') == 'saved':
                counts['capabilities_saved'] += int(mapped.get('capability_created', False))
                counts['categories_created'] += mapped.get('categories_created', 0)
                counts['materials_created'] += mapped.get('materials_created', 0)
                if mapped.get('capability_id'):
                    counts['category_assignments_created'] += bulk._link_contact_capability(
                        db, org.id, mapping.get('contact_ids'), mapped['capability_id'])
                if mapped.get('material_warning'):
                    counts['capabilities_skipped'] += 1
                    warnings.append({'row': number, 'reason': mapped['material_warning']})
            else:
                counts['pending_category_mappings'] += 1
                counts['capabilities_skipped'] += 1
                warnings.append({'row': number, 'reason': (mapped or {}).get('reason') or 'Category mapping needs review'})
        db.flush()
    counts['new_suppliers_saved'] = int(created_supplier)
    counts['contacts_created'] = len(created_contact_ids)
    counts['contacts_updated'] = len(updated_contact_ids - created_contact_ids)
    counts['existing_suppliers_reused'] = int(not created_supplier)
    counts['suppliers_updated'] = int(updated and not created_supplier)
    counts['suppliers_incomplete'] = int(bool(warnings))
    return counts, warnings


def process_supplier_jobs(db: Session):
    """Process at most one configured chunk; business data and checkpoint commit together."""
    try:
        _lock(db, WORKER_LOCK)
        batch = db.scalar(select(ImportBatch).where(ImportBatch.kind == KIND,
            ImportBatch.status == 'queued').order_by(ImportBatch.created_at)
            .limit(1).with_for_update(skip_locked=True))
        if not batch:
            db.rollback()
            return []
        batch_id = batch.id
        size = (batch.import_options or {}).get('batch_size', 10)
        items = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch.id,
            ImportedRow.status == 'pending').order_by(ImportedRow.created_at, ImportedRow.id).limit(size)).all()
        metadata = deepcopy(batch.preview_metadata or {})
        user = db.get(User, batch.initiated_by)
        if not user or not user.is_admin or not user.is_active:
            batch.status = 'failed'
            batch.failure_reason = 'The initiating administrator no longer has import permission.'
            db.commit()
            return []
        for item in items:
            try:
                with db.begin_nested():
                    counts, warnings = _apply_group(db, batch, item, user)
                    item.status = 'saved'
                    db.flush()
                for key, count in counts.items():
                    metadata[key] = metadata.get(key, 0) + count
                metadata['warnings'] = (metadata.get('warnings', []) + warnings)[-100:]
            except IntegrityError:
                logger.warning('Supplier import conflict batch_id=%s source_id=%s', batch.id, item.id)
                item.status = 'invalid'
                item.issue = 'Supplier data conflicts with an existing record; review this supplier group.'
                batch.errors += 1
                metadata['failed_rows'] = metadata.get('failed_rows', 0) + len(item.raw['source_rows'])
                metadata['issues'] = (metadata.get('issues', []) + [{'row': item.row_number, 'reason': item.issue}])[-100:]
            except (DBAPIError, PoolTimeoutError):
                # Infrastructure failures retry the whole uncommitted chunk.
                raise
            except Exception as exc:
                logger.warning('Supplier import row failed batch_id=%s source_id=%s type=%s', batch.id, item.id, type(exc).__name__)
                item.status = 'invalid'
                item.issue = 'Invalid supplier data; review company identity and contact fields.' if isinstance(exc, ValueError) else 'Supplier data could not be saved; review this supplier group.'
                batch.errors += 1
                metadata['failed_rows'] = metadata.get('failed_rows', 0) + len(item.raw['source_rows'])
                metadata['issues'] = (metadata.get('issues', []) + [{'row': item.row_number, 'reason': item.issue}])[-100:]
            batch.completed_rows += 1
        batch.preview_metadata = metadata
        batch.created = metadata.get('new_suppliers_saved', 0)
        batch.unchanged_count = metadata.get('existing_suppliers_reused', 0)
        batch.updated_count = metadata.get('suppliers_updated', 0)
        if batch.completed_rows >= metadata['total_supplier_records_identified']:
            batch.status = 'completed_with_errors' if batch.errors else 'completed'
            batch.stage = 'complete'
        batch.failure_reason = None
        batch.retry_count = 0
        db.commit()
        return [batch_id]
    except Exception as exc:
        logger.warning('Supplier import chunk rolled back batch_id=%s type=%s sqlstate=%s',
            locals().get('batch_id', 'unclaimed'), type(exc).__name__, getattr(getattr(exc, 'orig', None), 'sqlstate', None))
        db.rollback()
        if 'batch_id' in locals():
            # Lock before updating retry state; a restarted worker may have
            # finished the chunk while this session reconnected.
            batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update())
            if batch and batch.status == 'queued':
                batch.retry_count += 1
                batch.failure_reason = 'Database temporarily unavailable while saving suppliers. Committed progress is preserved.'
                if batch.retry_count >= 3:
                    batch.status = 'failed'
                db.commit()
        return []
