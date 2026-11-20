"""Private, durable supplier price book bulk Excel/CSV import.

Processes validated supplier private rates in durable background jobs:
- Up to 300 records per processing job
- 30 records per committed database batch
- Automatic continuation across jobs (e.g. 1,000 rows = 300 + 300 + 300 + 100)
- Strict Material Master matching (never creates global Material Master records)
- Strict tenant isolation (never reads or modifies another supplier's prices)
"""
from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from itertools import islice
from math import ceil
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.import_storage import get_import_storage
from app.models import (
    Category,
    ImportBatch,
    ImportedRow,
    Material,
    MeasurementUnit,
    PriceBookItem,
    User,
)
from app.modules.materials.service import UNIT_NAMES, normalize_text, normalize_unit
from app.modules.pricebooks.service import category_pair, save_rate, supplier, utc
from app.response_contracts import RateImportOut
from app.schemas import PriceIn, RateImportMapping
from app.security import audit, current_user

router = APIRouter()

JOB_ROWS = 300
BATCH_ROWS = 30
MAX_ROWS = 10_000
MAX_BYTES = 8_000_000
MAX_COLUMNS = 120
MAX_CELL_LENGTH = 4_000

TEMPLATE_HEADERS = [
    'Main Category',
    'Subcategory',
    'Material Description',
    'Material Code (optional)',
    'Unit',
    'Brand (optional)',
    'Model (optional)',
    'Unit Price (SAR)',
    'Minimum Order Quantity (optional)',
    'Availability / Lead Time (optional)',
    'Status (Active / Inactive)',
    'Supplier Notes (optional)',
]

REFERENCE_SHEET_RE = re.compile(
    r'(^|[^a-z])(example|examples|instructions|reference|lookup|legend|_reference)([^a-z]|$)',
    re.IGNORECASE,
)

HEADER_ALIASES: dict[str, str] = {
    'main category': 'main',
    'main_category': 'main',
    'category': 'main',
    'الفئة الرئيسية': 'main',
    'subcategory': 'sub',
    'sub category': 'sub',
    'sub-category': 'sub',
    'sub_category': 'sub',
    'الفئة الفرعية': 'sub',
    'description': 'description',
    'material description': 'description',
    'material name': 'description',
    'item description': 'description',
    'وصف المادة': 'description',
    'الوصف': 'description',
    'material id': 'material_id',
    'material_id': 'material_id',
    'material code': 'code',
    'material code (optional)': 'code',
    'material_code': 'code',
    'code': 'code',
    'item code': 'code',
    'رمز المادة': 'code',
    'unit': 'unit',
    'uom': 'unit',
    'unit of measure': 'unit',
    'الوحدة': 'unit',
    'brand': 'brand',
    'brand (optional)': 'brand',
    'العلامة التجارية': 'brand',
    'model': 'model',
    'model (optional)': 'model',
    'الطراز': 'model',
    'unit price': 'unit_price',
    'unit price (sar)': 'unit_price',
    'unit_price': 'unit_price',
    'price': 'unit_price',
    'price (sar)': 'unit_price',
    'سعر الوحدة': 'unit_price',
    'سعر الوحدة (ر.س)': 'unit_price',
    'minimum order quantity': 'min_order_quantity',
    'minimum order quantity (optional)': 'min_order_quantity',
    'min order quantity': 'min_order_quantity',
    'min_order_quantity': 'min_order_quantity',
    'moq': 'min_order_quantity',
    'الحد الأدنى للطلب': 'min_order_quantity',
    'availability / lead time': 'lead_days',
    'availability / lead time (optional)': 'lead_days',
    'availability/lead time': 'lead_days',
    'availability': 'lead_days',
    'lead time': 'lead_days',
    'lead time (days)': 'lead_days',
    'lead_days': 'lead_days',
    'مدة التوريد': 'lead_days',
    'status': 'row_status',
    'status (active / inactive)': 'row_status',
    'active': 'row_status',
    'الحالة': 'row_status',
    'supplier notes': 'notes',
    'supplier notes (optional)': 'notes',
    'notes': 'notes',
    'remarks': 'notes',
    'ملاحظات المورد': 'notes',
    'price valid until': 'valid_until',
    'valid until': 'valid_until',
    'valid_until': 'valid_until',
    'price basis': 'price_basis',
    'price_basis': 'price_basis',
    'offered specifications': 'offered_specs',
    'offered_specs': 'offered_specs',
}

ISSUE_STATUSES = {'unmatched', 'invalid', 'duplicate', 'conflict', 'failed'}


def _safe_excel_text(value: object) -> str:
    text = '' if value is None else str(value)
    if text and text[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + text
    return text


def _clean_cell(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return 'Active' if value else 'Inactive'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text[:MAX_CELL_LENGTH] if len(text) > MAX_CELL_LENGTH else text


def _normalize_header(value: object) -> str:
    raw = unicodedata.normalize('NFKC', _clean_cell(value)).casefold()
    raw = re.sub(r'\s+', ' ', raw).strip()
    if raw in HEADER_ALIASES:
        return HEADER_ALIASES[raw]
    stripped = re.sub(r'\s*\(optional\)\s*', '', raw).strip()
    if stripped in HEADER_ALIASES:
        return HEADER_ALIASES[stripped]
    return raw


def _parse_decimal(value: object, *, field: str, max_decimals: int = 2) -> Decimal:
    text = _clean_cell(value)
    if not text:
        raise ValueError(f'{field} is required')
    cleaned = re.sub(r'(?i)\b(sar|sr|ر\.س|ريال)\b', '', text).replace(',', '').strip()
    try:
        dec = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f'Invalid {field}: {text}') from exc
    if not dec.is_finite() or dec <= 0:
        raise ValueError(f'{field} must be greater than 0')
    exponent = dec.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -max_decimals:
        raise ValueError(f'{field} cannot have more than {max_decimals} decimal places')
    if dec >= Decimal('1000000000000'):
        raise ValueError(f'{field} exceeds maximum allowed value')
    return dec


def _parse_optional_moq(value: object) -> Decimal | None:
    text = _clean_cell(value)
    if not text:
        return None
    return _parse_decimal(text, field='Minimum Order Quantity', max_decimals=3)


def _parse_lead_days(value: object) -> tuple[int, str]:
    """Return (lead_days, extra_availability_note)."""
    text = _clean_cell(value)
    if not text:
        return 7, ''
    low = text.casefold()
    if low in {'immediate', 'in stock', 'available', 'ready', 'متوفر', 'فوري'}:
        return 1 if low in {'in stock', 'available', 'ready', 'متوفر'} else 0, text
    if re.fullmatch(r'-?\d+(\.\d+)?', text):
        num = float(text)
        if num < 0 or num > 3650 or not num.is_integer():
            raise ValueError('Availability / Lead Time must be a whole number of days between 0 and 3650')
        return int(num), ''
    match = re.search(r'(-?\d+)\s*(?:-\s*(\d+))?\s*(?:day|days|d|يوم|أيام)?', low)
    if match:
        first = int(match.group(1))
        second = int(match.group(2)) if match.group(2) else first
        days = max(first, second)
        if first < 0 or days > 3650:
            raise ValueError('Availability / Lead Time must be between 0 and 3650 days')
        return days, text if not re.fullmatch(r'\d+\s*(?:day|days|d|يوم|أيام)?', low) else ''
    return 7, f'Availability: {text}'


def _parse_row_status(value: object) -> bool:
    text = _clean_cell(value).casefold()
    if not text or text in {'active', 'enabled', 'true', '1', 'yes', 'نشط', 'مفعل'}:
        return True
    if text in {'inactive', 'disabled', 'false', '0', 'no', 'غير نشط', 'موقوف'}:
        return False
    raise ValueError(f'Invalid Status "{value}". Use Active or Inactive.')


def _parse_valid_until(value: object) -> datetime | None:
    text = _clean_cell(value)
    if not text:
        return None
    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y'):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError(f'Invalid Price Valid Until date: {text}')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if utc(dt) <= datetime.now(timezone.utc):
        raise ValueError('Price validity date must be in the future')
    return dt


def _parse_price_basis(value: object) -> str:
    text = _clean_cell(value).casefold().replace(' ', '_').replace('-', '_')
    if not text or text in {'supply', 'supply_only', 'توريد', 'توريد_فقط'}:
        return 'supply'
    if text in {'supply_install', 'supply_and_install', 'supply_&_install', 'supply_and_installation', 'توريد_وتركيب'}:
        return 'supply_install'
    raise ValueError(f'Invalid price basis "{value}". Use supply or supply_install.')


def owned_batch(db: Session, user: User, org_id: str, batch_id: str) -> ImportBatch:
    supplier(db, user, org_id)
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.kind != 'supplier_rates' or (batch.import_options or {}).get('org_id') != org_id:
        raise HTTPException(404, 'Import not found')
    return batch


def _recompute_metadata(db: Session, batch: ImportBatch) -> dict:
    counts = dict(
        db.execute(
            select(ImportedRow.status, func.count(ImportedRow.id))
            .where(ImportedRow.batch_id == batch.id)
            .group_by(ImportedRow.status)
        ).all()
    )
    ready_count = int(counts.get('ready', 0))
    saved_count = int(counts.get('saved', 0))
    unmatched_count = int(counts.get('unmatched', 0))
    invalid_count = int(counts.get('invalid', 0))
    duplicate_count = int(counts.get('duplicate', 0))
    conflict_count = int(counts.get('conflict', 0))
    failed_count = int(counts.get('failed', 0))

    action_counts = {'create': 0, 'update': 0}
    for raw_row in db.scalars(
        select(ImportedRow.raw).where(
            ImportedRow.batch_id == batch.id,
            ImportedRow.status.in_({'ready', 'saved'}),
        )
    ).all():
        act = (raw_row or {}).get('_action', 'create')
        if act in action_counts:
            action_counts[act] += 1

    valid_total = ready_count + saved_count + failed_count
    skipped_count = unmatched_count + invalid_count + duplicate_count + conflict_count
    jobs_total = max(1, ceil(valid_total / JOB_ROWS)) if valid_total else 0
    done_valid = saved_count + failed_count
    if valid_total == 0:
        current_job = 0
        current_batch = 0
        batches_in_job = 0
        progress_percent = 100 if batch.status in {'completed', 'completed_with_errors', 'preview'} else 0
    else:
        current_job = min(jobs_total, (done_valid // JOB_ROWS) + (1 if done_valid < valid_total else 0))
        if done_valid >= valid_total:
            current_job = jobs_total
        job_start = (current_job - 1) * JOB_ROWS
        job_size = min(JOB_ROWS, max(0, valid_total - job_start))
        batches_in_job = max(1, ceil(job_size / BATCH_ROWS)) if job_size else 0
        done_in_job = max(0, min(job_size, done_valid - job_start))
        current_batch = min(batches_in_job, ceil(done_in_job / BATCH_ROWS)) if done_in_job else 0
        progress_percent = min(100, int(round((done_valid / valid_total) * 100))) if batch.stage != 'parse' else 0

    meta = {
        **(batch.preview_metadata or {}),
        'valid_count': valid_total,
        'new_count': action_counts['create'],
        'update_count': action_counts['update'],
        'unmatched_count': unmatched_count,
        'invalid_count': invalid_count,
        'duplicate_count': duplicate_count,
        'conflict_count': conflict_count,
        'skipped_count': skipped_count,
        'failed_apply_count': failed_count,
        'jobs_total': jobs_total,
        'current_job': current_job,
        'batches_in_job': batches_in_job,
        'current_batch': current_batch,
        'progress_percent': 100 if batch.status in {'completed', 'completed_with_errors'} else progress_percent,
    }
    batch.preview_metadata = meta
    batch.errors = skipped_count + failed_count
    return meta


def progress(
    db: Session,
    batch: ImportBatch,
    limit: int = 25,
    offset: int = 0,
    filter_status: str = 'all',
) -> dict:
    meta = dict(batch.preview_metadata or {})
    query = select(ImportedRow).where(ImportedRow.batch_id == batch.id)
    if filter_status == 'valid':
        query = query.where(ImportedRow.status.in_({'ready', 'saved'}))
    elif filter_status == 'issues':
        query = query.where(ImportedRow.status.in_(ISSUE_STATUSES))
    elif filter_status == 'saved':
        query = query.where(ImportedRow.status == 'saved')
    elif filter_status == 'failed':
        query = query.where(ImportedRow.status == 'failed')

    rows = (
        db.scalars(query.order_by(ImportedRow.row_number).limit(limit).offset(offset)).all()
        if limit > 0
        else []
    )
    valid_count = int(meta.get('valid_count', max(0, batch.rows - batch.errors)))
    skipped_count = int(
        meta.get(
            'skipped_count',
            int(meta.get('unmatched_count', 0))
            + int(meta.get('invalid_count', 0))
            + int(meta.get('duplicate_count', 0))
            + int(meta.get('conflict_count', 0)),
        )
    )
    progress_percent = int(meta.get('progress_percent', 0))
    if batch.status in {'completed', 'completed_with_errors'}:
        progress_percent = 100
    elif batch.status == 'preview':
        progress_percent = 100
    elif batch.status == 'uploaded':
        progress_percent = 5
    elif batch.status == 'queued' and progress_percent == 0:
        progress_percent = 5

    items = []
    for r in rows:
        raw = r.raw or {}
        items.append(
            {
                'row': r.row_number,
                'main_category': str(raw.get('main') or ''),
                'subcategory': str(raw.get('sub') or ''),
                'description': str(raw.get('description') or raw.get('code') or ''),
                'material_code': str(raw.get('code') or ''),
                'unit': str(raw.get('_canonical_unit') or raw.get('unit') or ''),
                'brand': str(raw.get('brand') or ''),
                'model': str(raw.get('model') or ''),
                'unit_price': str(raw.get('_normalized_price') or raw.get('unit_price') or ''),
                'min_order_quantity': str(raw.get('min_order_quantity') or ''),
                'lead_days': str(raw.get('_lead_days') if raw.get('_lead_days') is not None else raw.get('lead_days') or ''),
                'row_status': str(raw.get('_row_status_label') or raw.get('row_status') or 'Active'),
                'notes': str(raw.get('notes') or ''),
                'material_id': raw.get('_material_id'),
                'matched_material_code': raw.get('_matched_code'),
                'matched_material_name': raw.get('_matched_name'),
                'existing_price': raw.get('_existing_price'),
                'action': str(raw.get('_action') or 'create'),
                'status': r.status,
                'issue_category': r.status if r.status in ISSUE_STATUSES else None,
                'issue': r.issue,
            }
        )

    return {
        'batch_id': batch.id,
        'filename': batch.filename,
        'status': batch.status,
        'stage': batch.stage,
        'rows': batch.rows,
        'valid': valid_count,
        'new_count': int(meta.get('new_count', 0)),
        'update_count': int(meta.get('update_count', 0)),
        'unmatched_count': int(meta.get('unmatched_count', 0)),
        'invalid_count': int(meta.get('invalid_count', 0)),
        'duplicate_count': int(meta.get('duplicate_count', 0)),
        'conflict_count': int(meta.get('conflict_count', 0)),
        'created': batch.created,
        'updated_count': batch.updated_count,
        'skipped_count': skipped_count,
        'errors': batch.errors,
        'saved': batch.created + batch.updated_count,
        'completed_rows': batch.completed_rows,
        'current_job': int(meta.get('current_job', 0)),
        'jobs_total': int(meta.get('jobs_total', 0)),
        'current_batch': int(meta.get('current_batch', 0)),
        'batches_in_job': int(meta.get('batches_in_job', 0)),
        'progress_percent': progress_percent,
        'failure_reason': batch.failure_reason,
        'items': items,
        'limit': limit,
        'offset': offset,
    }


def _style_header_row(ws, headers: list[str]) -> None:
    header_fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    ws.append(headers)
    for idx, title in enumerate(headers, 1):
        cell = ws.cell(row=1, column=idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        ws.column_dimensions[get_column_letter(idx)].width = max(18, min(36, len(title) + 6))
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = 'A2'


def build_template_workbook(db: Session) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = 'Price Book Import'
    _style_header_row(ws, TEMPLATE_HEADERS)

    # Number formatting for data rows
    for row_idx in range(2, 501):
        ws.cell(row=row_idx, column=8).number_format = '#,##0.00'
        ws.cell(row=row_idx, column=9).number_format = '#,##0.###'
        ws.cell(row=row_idx, column=10).number_format = '0'

    # Fetch live active categories and measurement units for dropdowns & realistic examples
    main_cats = db.scalars(
        select(Category)
        .where(Category.active.is_(True), Category.parent_id.is_(None))
        .order_by(Category.name_en)
        .limit(200)
    ).all()
    units_db = db.scalars(
        select(MeasurementUnit)
        .where(MeasurementUnit.active.is_(True))
        .order_by(MeasurementUnit.sort_order, MeasurementUnit.code)
        .limit(200)
    ).all()
    unit_codes = [u.code for u in units_db] if units_db else sorted(UNIT_NAMES.keys())

    # Hidden reference sheet for Excel dropdowns
    ref = wb.create_sheet(title='_Reference')
    ref.sheet_state = 'hidden'
    ref['A1'] = 'MainCategories'
    ref['B1'] = 'Units'
    ref['C1'] = 'Status'
    for i, cat in enumerate(main_cats, 2):
        ref.cell(row=i, column=1, value=cat.name_en)
    for i, code in enumerate(unit_codes, 2):
        ref.cell(row=i, column=2, value=code)
    ref['C2'] = 'Active'
    ref['C3'] = 'Inactive'

    if main_cats:
        dv_cat = DataValidation(
            type='list',
            formula1=f"='_Reference'!$A$2:$A${len(main_cats) + 1}",
            allow_blank=True,
        )
        ws.add_data_validation(dv_cat)
        dv_cat.add('A2:A5000')

    if unit_codes:
        dv_unit = DataValidation(
            type='list',
            formula1=f"='_Reference'!$B$2:$B${len(unit_codes) + 1}",
            allow_blank=False,
        )
        ws.add_data_validation(dv_unit)
        dv_unit.add('E2:E5000')

    dv_status = DataValidation(type='list', formula1="='_Reference'!$C$2:$C$3", allow_blank=True)
    ws.add_data_validation(dv_status)
    dv_status.add('K2:K5000')

    # Sheet 2: Instructions & Examples (no fake production material IDs)
    inst = wb.create_sheet(title='Instructions & Examples')
    inst.column_dimensions['A'].width = 28
    inst.column_dimensions['B'].width = 62
    inst.column_dimensions['C'].width = 58
    title_font = Font(bold=True, size=13, color='1E3A5F')
    sub_font = Font(bold=True, size=11, color='1E3A5F')

    inst.append(['Procumaterial — Supplier Private Price Book Bulk Import Guide', '', 'دليل الاستيراد الجماعي لدفتر أسعار المورد الخاص'])
    inst['A1'].font = title_font
    inst['C1'].font = title_font
    inst.append([])
    inst.append(['Rule / Column', 'English Instructions', 'التعليمات بالعربية'])
    for col in range(1, 4):
        inst.cell(row=3, column=col).font = sub_font

    guide_rows = [
        ('Important Rule', 'Uploaded rows must match existing Procumaterial Material Master items. No new global Material Master records are created.', 'يجب أن تطابق الصفوف مواد دليل المواد الرئيسي المعتمد. لن يتم إنشاء أي مواد عامة جديدة.'),
        ('Privacy & Quotations', 'Your prices stay strictly private to your company. Buyers only see prices when you submit a quotation.', 'تبقى أسعارك خاصة بشركتك فقط، ولا يراها المشترون إلا عند تقديم عرض سعر رسمي.'),
        ('Main Category', 'Main material category name (English or Arabic) matching Material Master.', 'اسم الفئة الرئيسية للمادة (بالعربية أو الإنجليزية).'),
        ('Subcategory', 'Subcategory name matching Material Master.', 'اسم الفئة الفرعية للمادة.'),
        ('Material Description', 'Exact material description from Procumaterial Material Master.', 'الوصف الدقيق للمادة كما هو في دليل المواد الرئيسي.'),
        ('Material Code (optional)', 'Exact Procumaterial Material Code if known (recommended for fastest unique matching). Do not enter fake IDs.', 'رمز المادة في دليل المواد إن توفر (موصى به للمطابقة السريعة). لا تدخل رموزاً وهمية.'),
        ('Unit', 'Approved measurement unit matching the Material Master item (e.g. BAG, TON, M3, PCS).', 'وحدة القياس المعتمدة والمطابقة لدليل المواد.'),
        ('Brand (optional)', 'Offered brand or manufacturer name.', 'العلامة التجارية أو الشركة المصنعة (اختياري).'),
        ('Model (optional)', 'Offered model or grade reference.', 'الطراز أو الموديل (اختياري).'),
        ('Unit Price (SAR)', 'Positive price in Saudi Riyals (SAR), up to 2 decimal places.', 'سعر الوحدة بالريال السعودي (قيمة موجبة حتى خانتين عشريتين).'),
        ('Minimum Order Quantity (optional)', 'Optional minimum order quantity (positive number).', 'الحد الأدنى للكمية للطلب (اختياري).'),
        ('Availability / Lead Time (optional)', 'Delivery lead time in days (e.g. 3, 7, 14) or availability note.', 'مدة التوريد بالأيام (مثلاً 3 أو 7 أو 14) أو حالة التوفر.'),
        ('Status (Active / Inactive)', 'Active or Inactive. Defaults to Active when blank.', 'نشط (Active) أو غير نشط (Inactive). الافتراضي نشط.'),
        ('Supplier Notes (optional)', 'Internal notes or commercial remarks for your team.', 'ملاحظات داخلية أو تجارية لفريقك (اختياري).'),
    ]
    for row in guide_rows:
        inst.append(list(row))

    inst.append([])
    inst.append(['Valid Example Rows (Illustrative — fill your own rows on Sheet 1 "Price Book Import")'])
    inst.cell(row=inst.max_row, column=1).font = sub_font
    inst.append(TEMPLATE_HEADERS)
    for col in range(1, len(TEMPLATE_HEADERS) + 1):
        inst.cell(row=inst.max_row, column=col).font = sub_font

    # Build illustrative example rows using real active categories if available, without fake production IDs
    sample_main = main_cats[0].name_en if main_cats else 'Civil Materials'
    sample_sub_obj = (
        db.scalar(
            select(Category)
            .where(Category.active.is_(True), Category.parent_id == main_cats[0].id)
            .order_by(Category.name_en)
            .limit(1)
        )
        if main_cats
        else None
    )
    sample_sub = sample_sub_obj.name_en if sample_sub_obj else 'Cement & Concrete'
    ex_unit_1 = 'BAG' if 'BAG' in unit_codes else (unit_codes[0] if unit_codes else 'PCS')
    ex_unit_2 = 'TON' if 'TON' in unit_codes else (unit_codes[0] if unit_codes else 'PCS')

    inst.append([
        sample_main,
        sample_sub,
        'Ordinary Portland Cement 50kg (example — use exact Material Master description)',
        '',
        ex_unit_1,
        'Saudi Cement',
        'OPC Type I',
        18.50,
        100,
        3,
        'Active',
        'Riyadh warehouse stock',
    ])
    inst.append([
        sample_main,
        sample_sub,
        'Deformed Steel Rebar 16mm (example — use exact Material Master description)',
        '',
        ex_unit_2,
        'SABIC Hadeed',
        'ASTM A615 Grade 60',
        2650.00,
        5,
        7,
        'Active',
        'Delivered within central region',
    ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


@router.get('/{org_id}/import-template')
def download_template(
    org_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    supplier(db, user, org_id)
    stream = build_template_workbook(db)
    return StreamingResponse(
        stream,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': 'attachment; filename="procumaterial-price-book-template.xlsx"',
            'Cache-Control': 'no-store',
        },
    )


@router.get('/{org_id}/export')
def export_existing_price_book(
    org_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    supplier(db, user, org_id)
    rows = db.execute(
        select(PriceBookItem, Material)
        .outerjoin(Material, Material.id == PriceBookItem.material_id)
        .where(PriceBookItem.org_id == org_id)
        .order_by(PriceBookItem.created_at.desc(), PriceBookItem.id)
    ).all()
    cat_ids = {
        cid
        for p, m in rows
        for cid in [
            p.main_category_id,
            p.subcategory_id,
            *([m.main_category_id, m.subcategory_id, m.category_id] if m else []),
        ]
        if cid
    }
    cats = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_(cat_ids))).all()} if cat_ids else {}
    parents = {c.parent_id for c in cats.values() if c.parent_id and c.parent_id not in cats}
    if parents:
        cats.update({c.id: c for c in db.scalars(select(Category).where(Category.id.in_(parents))).all()})

    wb = Workbook()
    ws = wb.active
    ws.title = 'Price Book Import'
    _style_header_row(ws, TEMPLATE_HEADERS)

    for item, mat in rows:
        main_id, sub_id = category_pair(db, mat) if mat else (item.main_category_id, item.subcategory_id)
        main_name = cats[main_id].name_en if main_id in cats else ''
        sub_name = cats[sub_id].name_en if sub_id in cats else ''
        ws.append([
            _safe_excel_text(main_name),
            _safe_excel_text(sub_name),
            _safe_excel_text(mat.name_en if mat else item.supplier_description),
            _safe_excel_text(mat.code if mat else ''),
            _safe_excel_text(item.unit),
            _safe_excel_text(item.brand or ''),
            _safe_excel_text(item.model or ''),
            float(item.unit_price),
            float(item.min_order_quantity) if item.min_order_quantity is not None else '',
            int(item.lead_days),
            'Active' if item.active else 'Inactive',
            _safe_excel_text(item.remarks or ''),
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': 'attachment; filename="supplier-price-book.xlsx"',
            'Cache-Control': 'no-store',
        },
    )


@router.get('/{org_id}/imports/recent', response_model=list[RateImportOut])
def recent_imports(
    org_id: str,
    limit: int = Query(5, ge=1, le=20),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    supplier(db, user, org_id)
    batches = db.scalars(
        select(ImportBatch)
        .where(ImportBatch.kind == 'supplier_rates')
        .order_by(ImportBatch.created_at.desc())
        .limit(50)
    ).all()
    owned = [b for b in batches if (b.import_options or {}).get('org_id') == org_id][:limit]
    return [progress(db, b, limit=25 if idx == 0 else 0, offset=0) for idx, b in enumerate(owned)]


@router.post('/{org_id}/bulk-import', response_model=RateImportOut)
def upload(
    org_id: str,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = True,
    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    supplier(db, user, org_id)
    if not dry_run:
        raise HTTPException(409, 'Upload a preview and confirm its batch before saving rates')
    filename = (file.filename or 'rates').strip()
    low = filename.lower()
    suffix = '.csv' if low.endswith('.csv') else '.xlsx' if low.endswith('.xlsx') else None
    if not suffix:
        raise HTTPException(415, 'CSV or XLSX required')

    if idempotency_key:
        recent = db.scalars(
            select(ImportBatch)
            .where(ImportBatch.kind == 'supplier_rates', ImportBatch.initiated_by == user.id)
            .order_by(ImportBatch.created_at.desc())
            .limit(20)
        ).all()
        for existing_batch in recent:
            opts = existing_batch.import_options or {}
            if opts.get('org_id') == org_id and opts.get('idempotency_key') == idempotency_key:
                return progress(db, existing_batch)

    batch = ImportBatch(
        kind='supplier_rates',
        filename=filename[:200],
        status='uploaded',
        stage='parse',
        initiated_by=user.id,
        import_options={'org_id': org_id, **({'idempotency_key': idempotency_key} if idempotency_key else {})},
        preview_metadata={'progress_percent': 5},
    )
    db.add(batch)
    db.flush()
    key = f'{org_id}/{batch.id}{suffix}'
    try:
        size, digest = get_import_storage().write(key, file.file, MAX_BYTES)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(413, 'Import file exceeds the 8 MB limit') from exc
    except Exception as exc:
        from app.storage import StorageUnavailable

        if isinstance(exc, StorageUnavailable):
            db.rollback()
            raise HTTPException(503, 'File storage is temporarily unavailable') from exc
        raise

    batch.source_storage_key = key
    batch.source_bytes = size
    batch.source_sha256 = digest.hex()
    audit(db, user, 'price_book.import_uploaded', org_id, batch.id, filename=batch.filename, bytes=size)
    db.commit()
    return progress(db, batch)


@router.get('/{org_id}/imports/{batch_id}', response_model=RateImportOut)
def status(
    org_id: str,
    batch_id: str,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    filter_status: str = Query('all', pattern='^(all|valid|issues|saved|failed)$'),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return progress(db, owned_batch(db, user, org_id, batch_id), limit, offset, filter_status)


def _validate_and_build_payload(
    org_id: str,
    raw: dict,
    material: Material,
    categories: dict[str, Category],
    valid_units: set[str],
    *,
    strict_category_check: bool = True,
) -> tuple[PriceIn, str, Decimal, int, str]:
    """Validate row fields against an already-loaded active Material Master record."""
    if not material or not material.active:
        raise ValueError('Material Master item is inactive or not found')

    main_id = material.main_category_id
    sub_id = material.subcategory_id
    if not main_id:
        leaf = categories.get(material.category_id)
        if leaf:
            main_id = leaf.parent_id or leaf.id
            sub_id = leaf.id if leaf.parent_id else None

    main_cat = categories.get(main_id) if main_id else None
    sub_cat = categories.get(sub_id) if sub_id else main_cat
    if not main_cat or not main_cat.active or main_cat.parent_id or not sub_cat or not sub_cat.active:
        raise ValueError('Material category hierarchy is inactive or invalid')

    if strict_category_check:
        if raw.get('main'):
            main_norm = normalize_text(str(raw['main']))
            if main_norm not in {normalize_text(main_cat.name_en), normalize_text(main_cat.name_ar or '')}:
                raise ValueError(f'Main Category does not match Material Master ({main_cat.name_en})')
        if raw.get('sub') and sub_cat:
            sub_norm = normalize_text(str(raw['sub']))
            if sub_norm not in {normalize_text(sub_cat.name_en), normalize_text(sub_cat.name_ar or '')}:
                raise ValueError(f'Subcategory does not match Material Master ({sub_cat.name_en})')

    mat_unit = normalize_unit(material.unit or '')
    raw_unit = _clean_cell(raw.get('unit'))
    canonical = normalize_unit(raw_unit) if raw_unit else mat_unit
    if not canonical or canonical not in valid_units:
        raise ValueError(f'Invalid measurement unit "{raw_unit}"')
    if canonical != mat_unit:
        raise ValueError(f'Unit mismatch: Material Master uses {mat_unit}, uploaded row has {canonical}')

    unit_price = _parse_decimal(raw.get('unit_price'), field='Unit Price (SAR)', max_decimals=2)
    moq = _parse_optional_moq(raw.get('min_order_quantity'))
    lead_days, extra_avail = _parse_lead_days(raw.get('lead_days'))
    active = _parse_row_status(raw.get('row_status'))
    valid_until = _parse_valid_until(raw.get('valid_until'))
    price_basis = _parse_price_basis(raw.get('price_basis'))

    notes = _clean_cell(raw.get('notes'))
    if extra_avail:
        notes = f'{extra_avail}; {notes}' if notes else extra_avail

    payload = PriceIn(
        org_id=org_id,
        material_id=material.id,
        main_category_id=main_id,
        subcategory_id=sub_id,
        supplier_description=material.name_en,
        unit=canonical,
        unit_price=unit_price,
        min_order_quantity=moq,
        brand=_clean_cell(raw.get('brand'))[:150],
        model=_clean_cell(raw.get('model'))[:150],
        offered_specs=_clean_cell(raw.get('offered_specs')),
        remarks=notes[:4000],
        lead_days=lead_days,
        valid_until=valid_until,
        price_basis=price_basis,
        active=active,
    )
    return payload, canonical, unit_price, lead_days, ('Active' if active else 'Inactive')


def _preview_values(
    payload: PriceIn,
    material: Material,
    existing: PriceBookItem | None,
    canonical_unit: str,
    unit_price: Decimal,
    lead_days: int,
    status_label: str,
) -> dict:
    return {
        '_material_id': material.id,
        '_matched_code': material.code,
        '_matched_name': material.name_en,
        '_canonical_unit': canonical_unit,
        '_normalized_price': f'{unit_price:.2f}',
        '_lead_days': lead_days,
        '_row_status_label': status_label,
        '_action': 'update' if existing else 'create',
        '_rate_id': existing.id if existing else None,
        '_version': existing.version if existing else None,
        '_existing_price': f'{existing.unit_price:.2f}' if existing else None,
        '_payload': payload.model_dump(mode='json'),
    }


def _active_units(db: Session) -> set[str]:
    units = set(UNIT_NAMES.keys())
    for u in db.scalars(select(MeasurementUnit)).all():
        if u.active:
            units.add(u.code)
        else:
            units.discard(u.code)
    return units


@router.put('/{org_id}/imports/{batch_id}/rows/{row_number}', response_model=RateImportOut)
def map_row(
    org_id: str,
    batch_id: str,
    row_number: int,
    payload: RateImportMapping,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    batch = owned_batch(db, user, org_id, batch_id)
    if batch.status != 'preview':
        raise HTTPException(409, 'Only a preview can be mapped')
    row = db.scalar(
        select(ImportedRow)
        .where(ImportedRow.batch_id == batch.id, ImportedRow.row_number == row_number)
        .with_for_update()
    )
    if not row:
        raise HTTPException(404, 'Import row not found')
    material = db.get(Material, payload.material_id)
    if not material or not material.active:
        raise HTTPException(422, 'Select an active Material Master item')

    categories = {c.id: c for c in db.scalars(select(Category)).all()}
    valid_units = _active_units(db)
    try:
        rate, canonical, unit_price, lead_days, status_label = _validate_and_build_payload(
            org_id, row.raw or {}, material, categories, valid_units, strict_category_check=False
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    # Check duplicate against other ready rows in this batch
    other_ready = db.scalars(
        select(ImportedRow).where(
            ImportedRow.batch_id == batch.id,
            ImportedRow.status == 'ready',
            ImportedRow.row_number != row_number,
        )
    ).all()
    for other in other_ready:
        oraw = other.raw or {}
        if oraw.get('_material_id') == material.id and (oraw.get('_payload') or {}).get('price_basis') == rate.price_basis:
            raise HTTPException(409, 'Another valid row in this file already maps to this material')

    existing = db.scalar(
        select(PriceBookItem)
        .where(
            PriceBookItem.org_id == org_id,
            PriceBookItem.material_id == material.id,
            PriceBookItem.price_basis == rate.price_basis,
        )
        .order_by(PriceBookItem.active.desc(), PriceBookItem.updated_at.desc())
        .limit(1)
    )
    row.raw = {
        **(row.raw or {}),
        **_preview_values(rate, material, existing, canonical, unit_price, lead_days, status_label),
    }
    row.status = 'ready'
    row.issue = None
    _recompute_metadata(db, batch)
    db.commit()
    return progress(db, batch)


@router.post('/{org_id}/imports/{batch_id}/confirm', response_model=RateImportOut)
def confirm(
    org_id: str,
    batch_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    supplier(db, user, org_id, lock=True)
    batch = owned_batch(db, user, org_id, batch_id)
    db.refresh(batch, with_for_update=True)
    if batch.status in {'queued', 'applying', 'completed', 'completed_with_errors'}:
        return progress(db, batch)
    if batch.status != 'preview' or not batch.rows:
        raise HTTPException(409, 'Upload and preview a file before confirming import')

    ready_count = int(
        db.scalar(
            select(func.count(ImportedRow.id)).where(
                ImportedRow.batch_id == batch.id,
                ImportedRow.status == 'ready',
            )
        )
        or 0
    )
    if ready_count == 0:
        raise HTTPException(409, 'No valid rows available to import. Fix issues or map unmatched materials first.')

    _recompute_metadata(db, batch)
    batch.status = 'queued'
    batch.stage = 'apply'
    batch.failure_reason = None
    audit(db, user, 'price_book.import_confirmed', org_id, batch.id, ready_rows=ready_count, skipped_rows=batch.errors)
    db.commit()
    return progress(db, batch)


@router.post('/{org_id}/imports/{batch_id}/retry', response_model=RateImportOut)
def retry_import(
    org_id: str,
    batch_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    supplier(db, user, org_id, lock=True)
    batch = owned_batch(db, user, org_id, batch_id)
    db.refresh(batch, with_for_update=True)
    if batch.status in {'completed', 'completed_with_errors'}:
        return progress(db, batch)
    if batch.status not in {'failed', 'applying', 'uploaded', 'queued'}:
        raise HTTPException(409, 'This import cannot be retried in its current state')

    has_rows = db.scalar(select(ImportedRow.id).where(ImportedRow.batch_id == batch.id).limit(1)) is not None
    if batch.stage == 'parse' or not has_rows:
        db.execute(delete(ImportedRow).where(ImportedRow.batch_id == batch.id))
        batch.rows = 0
        batch.errors = 0
        batch.created = 0
        batch.updated_count = 0
        batch.completed_rows = 0
        batch.status = 'uploaded'
        batch.stage = 'parse'
        batch.failure_reason = None
        batch.retry_count = (batch.retry_count or 0) + 1
    else:
        # Preserve already-saved rows and continue safely from remaining ready rows
        batch.status = 'queued'
        batch.stage = 'apply'
        batch.failure_reason = None
        batch.retry_count = (batch.retry_count or 0) + 1
        _recompute_metadata(db, batch)

    audit(db, user, 'price_book.import_retried', org_id, batch.id, retry_count=batch.retry_count, stage=batch.stage)
    db.commit()
    return progress(db, batch)


@router.get('/{org_id}/imports/{batch_id}/errors.xlsx')
def download_failed_rows(
    org_id: str,
    batch_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    batch = owned_batch(db, user, org_id, batch_id)
    issue_rows = db.scalars(
        select(ImportedRow)
        .where(ImportedRow.batch_id == batch.id, ImportedRow.status.in_(ISSUE_STATUSES))
        .order_by(ImportedRow.row_number)
    ).all()

    wb = Workbook()
    ws = wb.active
    ws.title = 'Failed Rows'
    headers = ['Row Number', *TEMPLATE_HEADERS, 'Error Category', 'Issue / Correction Needed']
    _style_header_row(ws, headers)

    for r in issue_rows:
        raw = r.raw or {}
        ws.append([
            r.row_number,
            _safe_excel_text(raw.get('main') or ''),
            _safe_excel_text(raw.get('sub') or ''),
            _safe_excel_text(raw.get('description') or ''),
            _safe_excel_text(raw.get('code') or ''),
            _safe_excel_text(raw.get('unit') or ''),
            _safe_excel_text(raw.get('brand') or ''),
            _safe_excel_text(raw.get('model') or ''),
            _safe_excel_text(raw.get('unit_price') or ''),
            _safe_excel_text(raw.get('min_order_quantity') or ''),
            _safe_excel_text(raw.get('lead_days') or ''),
            _safe_excel_text(raw.get('row_status') or ''),
            _safe_excel_text(raw.get('notes') or ''),
            _safe_excel_text(r.status),
            _safe_excel_text(r.issue or ''),
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f'attachment; filename="price-book-failed-rows-{batch.id[:8]}.xlsx"',
            'Cache-Control': 'no-store',
        },
    )


def _select_data_sheet(book):
    for name in book.sheetnames:
        sheet = book[name]
        if getattr(sheet, 'sheet_state', 'visible') != 'visible':
            continue
        if REFERENCE_SHEET_RE.search(name):
            continue
        return sheet
    return book.active


def parse_rows(batch: ImportBatch) -> list[dict]:
    data = get_import_storage().read(batch.source_storage_key)
    if batch.source_storage_key.endswith('.csv'):
        try:
            decoded = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded = data.decode('cp1256', errors='replace')
        iterator = csv.reader(StringIO(decoded))
        headers = next(iterator, [])
        if not headers or len(headers) > MAX_COLUMNS:
            raise ValueError('Import exceeds the safe column limit')
        norm_headers = [_normalize_header(h) for h in headers]
        result = []
        for raw_row in islice(iterator, MAX_ROWS + 1):
            if len(raw_row) > MAX_COLUMNS:
                raise ValueError('Import row exceeds the safe column limit')
            if not any(_clean_cell(v) for v in raw_row):
                continue
            item = {}
            for idx, key in enumerate(norm_headers):
                if key:
                    item[key] = _clean_cell(raw_row[idx]) if idx < len(raw_row) else ''
            result.append(item)
    else:
        try:
            with ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 500 or sum(item.file_size for item in infos) > 64_000_000:
                    raise ValueError('Workbook exceeds safe expanded size')
        except BadZipFile as exc:
            raise ValueError('File is not a valid .xlsx workbook') from exc

        book = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
        try:
            sheet = _select_data_sheet(book)
            if sheet is None:
                raise ValueError('Workbook contains no readable worksheet')
            if hasattr(sheet, 'reset_dimensions'):
                sheet.reset_dimensions()
            if sheet.max_column and sheet.max_column > MAX_COLUMNS:
                raise ValueError('Import exceeds the safe column limit')
            if sheet.max_row and sheet.max_row > MAX_ROWS + 5:
                raise ValueError(f'Import exceeds the maximum limit of {MAX_ROWS} rows')

            iterator = sheet.iter_rows(max_col=MAX_COLUMNS, max_row=MAX_ROWS + 6, values_only=True)
            headers: list[str] = []
            # Detect header row within first 5 rows
            for _ in range(5):
                candidate = next(iterator, None)
                if candidate is None:
                    break
                normalized = [_normalize_header(v) for v in candidate]
                if any(k in {'description', 'unit_price', 'code', 'unit', 'main'} for k in normalized):
                    headers = normalized
                    break
            if not headers:
                raise ValueError('Could not find valid header columns (e.g. Material Description, Unit, Unit Price (SAR))')

            result = []
            for raw_row in islice(iterator, MAX_ROWS + 1):
                if not any(v is not None and str(v).strip() != '' for v in raw_row):
                    continue
                item = {}
                for idx, key in enumerate(headers):
                    if key:
                        item[key] = _clean_cell(raw_row[idx]) if idx < len(raw_row) else ''
                result.append(item)
        finally:
            book.close()

    if not result or len(result) > MAX_ROWS:
        raise ValueError(f'Import must contain between 1 and {MAX_ROWS} data rows')
    return result


def _match_material(
    raw: dict,
    by_id: dict[str, Material],
    by_code: dict[str, list[Material]],
    by_desc: dict[str, list[Material]],
    categories: dict[str, Category],
) -> tuple[Material | None, str | None, str | None]:
    """Return (material, error_status, error_message). Never creates global materials."""
    explicit_id = _clean_cell(raw.get('material_id'))
    if explicit_id:
        mat = by_id.get(explicit_id)
        if not mat:
            return None, 'unmatched', f'Material ID "{explicit_id}" not found in active Material Master'
        return mat, None, None

    code = _clean_cell(raw.get('code')).upper()
    if code:
        matches = by_code.get(code, [])
        if len(matches) == 1:
            return matches[0], None, None
        if len(matches) > 1:
            return None, 'conflict', f'Ambiguous Material Code "{code}" matches multiple Material Master items'
        return None, 'unmatched', f'Material Code "{code}" not found in Procumaterial Material Master'

    desc = normalize_text(_clean_cell(raw.get('description')))
    if not desc:
        return None, 'invalid', 'Material Description or Material Code is required'

    candidates = by_desc.get(desc, [])
    if not candidates:
        return None, 'unmatched', 'Material not found in Procumaterial Material Master; no global material will be created'

    # Narrow by main category, subcategory, and unit when provided
    main_norm = normalize_text(_clean_cell(raw.get('main')))
    sub_norm = normalize_text(_clean_cell(raw.get('sub')))
    unit_norm = normalize_unit(_clean_cell(raw.get('unit')))

    def cat_matches(mat: Material) -> bool:
        m_id = mat.main_category_id
        s_id = mat.subcategory_id
        if not m_id:
            leaf = categories.get(mat.category_id)
            if leaf:
                m_id = leaf.parent_id or leaf.id
                s_id = leaf.id if leaf.parent_id else None
        m_cat = categories.get(m_id) if m_id else None
        s_cat = categories.get(s_id) if s_id else m_cat
        if main_norm and (not m_cat or main_norm not in {normalize_text(m_cat.name_en), normalize_text(m_cat.name_ar or '')}):
            return False
        if sub_norm and (not s_cat or sub_norm not in {normalize_text(s_cat.name_en), normalize_text(s_cat.name_ar or '')}):
            return False
        return True

    cat_filtered = [m for m in candidates if cat_matches(m)]
    if not cat_filtered:
        return None, 'unmatched', 'Material description found, but Main Category / Subcategory does not match Material Master'

    if unit_norm:
        unit_filtered = [m for m in cat_filtered if normalize_unit(m.unit or '') == unit_norm]
        if len(unit_filtered) == 1:
            return unit_filtered[0], None, None
        if len(unit_filtered) > 1:
            return None, 'conflict', 'Ambiguous material match; specify Material Code or map manually'
        # Description & category matched, but unit did not
        expected_units = ', '.join(sorted({normalize_unit(m.unit or '') for m in cat_filtered}))
        return None, 'invalid', f'Unit mismatch: Material Master uses {expected_units}, row has {unit_norm}'

    if len(cat_filtered) == 1:
        return cat_filtered[0], None, None
    return None, 'conflict', 'Ambiguous material match; specify Material Code or map manually'


def _parse_and_preview_batch(db: Session, batch: ImportBatch, user: User, org_id: str) -> None:
    try:
        rows = parse_rows(batch)
    except Exception as exc:
        batch.status = 'failed'
        batch.failure_reason = str(exc)[:480] or 'Could not read CSV/XLSX file. Check format and row limits.'
        return

    # Clear any prior rows on retry of parse stage
    db.execute(delete(ImportedRow).where(ImportedRow.batch_id == batch.id))

    explicit_ids = {_clean_cell(r.get('material_id')) for r in rows if _clean_cell(r.get('material_id'))}
    codes = {_clean_cell(r.get('code')).upper() for r in rows if _clean_cell(r.get('code'))}
    descs = {normalize_text(_clean_cell(r.get('description'))) for r in rows if _clean_cell(r.get('description'))}

    filters = []
    if explicit_ids:
        filters.append(Material.id.in_(explicit_ids))
    if codes:
        filters.append(func.upper(Material.code).in_(codes))
        filters.append(func.upper(Material.source_material_code).in_(codes))
        filters.append(func.upper(Material.source_sn).in_(codes))
    if descs:
        filters.append(Material.normalized_description.in_(descs))
        filters.append(func.lower(Material.name_en).in_(descs))
        filters.append(func.lower(Material.name_ar).in_(descs))
        filters.append(func.lower(Material.arabic_description).in_(descs))

    materials: list[Material] = []
    if filters:
        materials = db.scalars(
            select(Material).where(Material.active.is_(True), or_(*filters)).limit(50_001)
        ).all()
        if len(materials) > 50_000:
            batch.status = 'failed'
            batch.failure_reason = 'Too many ambiguous material matches. Include Material Codes.'
            return

    categories = {c.id: c for c in db.scalars(select(Category)).all()}
    valid_units = _active_units(db)

    mat_ids = [m.id for m in materials]
    existing_rates: dict[tuple[str, str], PriceBookItem] = {}
    if mat_ids:
        # Prefer active rate over inactive when both exist for (material_id, price_basis)
        for rate in db.scalars(
            select(PriceBookItem)
            .where(PriceBookItem.org_id == org_id, PriceBookItem.material_id.in_(mat_ids))
            .order_by(PriceBookItem.active.desc(), PriceBookItem.updated_at.desc())
        ).all():
            key = (rate.material_id, rate.price_basis)
            if key not in existing_rates:
                existing_rates[key] = rate

    by_id: dict[str, Material] = {}
    by_code: dict[str, list[Material]] = {}
    by_desc: dict[str, list[Material]] = {}
    for mat in materials:
        by_id[mat.id] = mat
        for cval in (mat.code, mat.source_material_code, mat.source_sn):
            if cval and cval.strip():
                key = cval.strip().upper()
                if mat not in by_code.setdefault(key, []):
                    by_code[key].append(mat)
        for dval in (mat.normalized_description, mat.name_en, mat.name_ar, mat.arabic_description):
            if dval and dval.strip():
                key = normalize_text(dval)
                if key and mat not in by_desc.setdefault(key, []):
                    by_desc[key].append(mat)

    seen_keys: set[tuple[str, str]] = set()
    staged_rows: list[ImportedRow] = []

    for row_number, raw in enumerate(rows, 2):
        mat, err_status, err_msg = _match_material(raw, by_id, by_code, by_desc, categories)
        if err_status or mat is None:
            staged_rows.append(
                ImportedRow(
                    batch_id=batch.id,
                    row_number=row_number,
                    raw=raw,
                    status=err_status or 'unmatched',
                    issue=err_msg,
                )
            )
            continue

        try:
            payload, canonical, unit_price, lead_days, status_label = _validate_and_build_payload(
                org_id, raw, mat, categories, valid_units, strict_category_check=True
            )
        except ValueError as exc:
            staged_rows.append(
                ImportedRow(
                    batch_id=batch.id,
                    row_number=row_number,
                    raw={**raw, '_material_id': mat.id, '_matched_code': mat.code, '_matched_name': mat.name_en},
                    status='invalid',
                    issue=str(exc)[:290],
                )
            )
            continue

        dedupe_key = (payload.material_id, payload.price_basis)
        if dedupe_key in seen_keys:
            staged_rows.append(
                ImportedRow(
                    batch_id=batch.id,
                    row_number=row_number,
                    raw={**raw, '_material_id': mat.id, '_matched_code': mat.code, '_matched_name': mat.name_en},
                    status='duplicate',
                    issue='Duplicate material and price basis in uploaded file',
                )
            )
            continue

        seen_keys.add(dedupe_key)
        existing = existing_rates.get(dedupe_key)
        enriched = {
            **raw,
            **_preview_values(payload, mat, existing, canonical, unit_price, lead_days, status_label),
        }
        staged_rows.append(
            ImportedRow(
                batch_id=batch.id,
                row_number=row_number,
                raw=enriched,
                status='ready',
                issue=None,
            )
        )

    # Bulk insert ImportedRow records in chunks of JOB_ROWS
    for start in range(0, len(staged_rows), JOB_ROWS):
        db.add_all(staged_rows[start : start + JOB_ROWS])
        db.flush()

    batch.rows = len(rows)
    batch.status = 'preview'
    batch.stage = 'preview'
    meta = _recompute_metadata(db, batch)
    audit(
        db,
        user,
        'price_book.import_previewed',
        org_id,
        batch.id,
        rows=batch.rows,
        valid=meta['valid_count'],
        errors=batch.errors,
    )


def _apply_job_batch(db: Session, batch: ImportBatch, user: User, org_id: str) -> None:
    """Process one durable job of up to JOB_ROWS (300) rows in committed batches of BATCH_ROWS (30)."""
    batch.status = 'applying'
    batch.stage = 'apply'
    db.flush()

    job_rows = db.scalars(
        select(ImportedRow)
        .where(ImportedRow.batch_id == batch.id, ImportedRow.status == 'ready')
        .order_by(ImportedRow.row_number)
        .limit(JOB_ROWS)
    ).all()

    for start in range(0, len(job_rows), BATCH_ROWS):
        chunk = job_rows[start : start + BATCH_ROWS]
        # Preload existing PriceBookItem records for this 30-row chunk in one query
        chunk_mat_ids = [r.raw.get('_material_id') for r in chunk if (r.raw or {}).get('_material_id')]
        existing_by_key: dict[tuple[str, str], PriceBookItem] = {}
        if chunk_mat_ids:
            for item in db.scalars(
                select(PriceBookItem)
                .where(PriceBookItem.org_id == org_id, PriceBookItem.material_id.in_(chunk_mat_ids))
                .order_by(PriceBookItem.active.desc(), PriceBookItem.updated_at.desc())
            ).all():
                key = (item.material_id, item.price_basis)
                if key not in existing_by_key:
                    existing_by_key[key] = item

        for row in chunk:
            try:
                with db.begin_nested():
                    raw_payload = dict((row.raw or {}).get('_payload') or {})
                    # Enforce tenant isolation unconditionally
                    if raw_payload.get('org_id') != org_id:
                        raise ValueError('Tenant isolation check failed for row')
                    payload = PriceIn.model_validate(raw_payload)
                    existing = existing_by_key.get((payload.material_id, payload.price_basis))
                    saved_item = save_rate(
                        db,
                        user,
                        payload,
                        existing,
                        expected_version=existing.version if existing else None,
                    )
                    existing_by_key[(payload.material_id, payload.price_basis)] = saved_item
                row.status = 'saved'
                row.issue = None
                if existing:
                    batch.updated_count += 1
                else:
                    batch.created += 1
            except (ValueError, HTTPException) as exc:
                row.status = 'failed'
                row.issue = (str(exc.detail) if isinstance(exc, HTTPException) else str(exc))[:290]
                batch.errors += 1
            batch.completed_rows += 1

        # Persist progress & commit after every 30-row database batch!
        _recompute_metadata(db, batch)
        db.commit()

    # After finishing up to 300 rows (1 job), check if more ready rows remain
    remaining = db.scalar(
        select(ImportedRow.id)
        .where(ImportedRow.batch_id == batch.id, ImportedRow.status == 'ready')
        .limit(1)
    )
    if remaining is not None:
        batch.status = 'applying'
        _recompute_metadata(db, batch)
        db.commit()
    else:
        meta = _recompute_metadata(db, batch)
        has_issues = batch.errors > 0 or meta.get('skipped_count', 0) > 0
        batch.status = 'completed_with_errors' if has_issues else 'completed'
        batch.stage = 'completed'
        meta['progress_percent'] = 100
        batch.preview_metadata = meta
        audit(
            db,
            user,
            'price_book.import_completed',
            org_id,
            batch.id,
            created=batch.created,
            updated=batch.updated_count,
            skipped=meta.get('skipped_count', 0),
            errors=batch.errors,
        )
        db.commit()


def process_rate_import(db: Session, event) -> None:
    batch = db.scalar(
        select(ImportBatch)
        .where(ImportBatch.id == event.payload.get('batch_id'), ImportBatch.kind == 'supplier_rates')
        .with_for_update()
    )
    if not batch:
        return
    user = db.get(User, batch.initiated_by)
    org_id = (batch.import_options or {}).get('org_id')
    if not user or not user.is_active:
        batch.status = 'failed'
        batch.failure_reason = 'Import owner is inactive'
        return
    try:
        supplier(db, user, org_id, lock=True)
    except HTTPException:
        batch.status = 'failed'
        batch.failure_reason = 'Import authorization is no longer valid'
        return

    if event.event == 'pricebook.import.parse':
        if batch.status != 'uploaded':
            return
        _parse_and_preview_batch(db, batch, user, org_id)
        return

    if batch.status not in {'queued', 'applying'}:
        return
    _apply_job_batch(db, batch, user, org_id)
