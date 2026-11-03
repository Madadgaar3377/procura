"""Admin-only staged imports for category hierarchy spreadsheets."""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections.abc import Sequence
from io import BytesIO, StringIO
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from openpyxl import load_workbook
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app import catalog_cache
from app.database import get_db
from app.models import Category, ImportBatch, ImportedRow, User
from app.modules.imports.materials import _category_code, _normal
from app.schemas import CategoryImportPreview, CategoryImportResult
from app.security import admin_user, audit

router = APIRouter(prefix='/categories/import', tags=['Category Hierarchy Imports'])
MAX_BYTES = 8_000_000
MAX_ROWS = 30_000
MAX_COLUMNS = 120
MAX_CELL_LENGTH = 500
ALIASES = {
    'discipline': {'discipline', 'trade', 'material discipline'},
    'main_category': {'main category', 'category', 'category name', 'level 1', 'division', 'parent category'},
    'main_category_ar': {'main category ar', 'main category arabic', 'arabic main category', 'الفئة الرئيسية', 'التصنيف الرئيسي'},
    'subcategory': {'sub category', 'subcategory', 'sub-category', 'level 2', 'group'},
    'subcategory_ar': {'subcategory ar', 'subcategory arabic', 'arabic subcategory', 'الفئة الفرعية', 'التصنيف الفرعي'},
}
REFERENCE = re.compile(r'(^|[^a-z])(reference|suggested|lookup|legend|expenditure|read me)([^a-z]|$)', re.IGNORECASE)
DISCIPLINES = {'civil': 'Civil', 'electrical': 'Electrical', 'mechanical': 'Mechanical'}


def _missing_import_batch_columns(exc: ProgrammingError) -> bool:
    """Recognize an API/database migration mismatch without exposing SQL details."""
    original = getattr(exc, 'orig', None)
    if getattr(original, 'sqlstate', None) == '42703':
        message = str(original).casefold()
    else:
        message = str(exc).casefold()
    return 'import_batches' in message and any(
        column in message for column in ('updated_count', 'unchanged_count', 'categories_created', 'updated_at')
    )


def _raise_migration_required(exc: ProgrammingError) -> None:
    if _missing_import_batch_columns(exc):
        raise HTTPException(
            503,
            'The database schema is behind this API version. From the backend directory, run '
            '`alembic upgrade head`, then restart the API and upload the workbook again.',
        ) from exc
    raise exc


def _header(value: object) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s/-]', '', text)).strip()


def _public_sheets(sheets: list[dict]) -> list[dict]:
    return [{key: value for key, value in sheet.items() if not key.startswith('_')} for sheet in sheets]


def _header_match(headers: list[str], mapping: dict[str, str]) -> bool:
    if mapping.get('main_category'):
        return True
    return any(header in ALIASES['main_category'] for header in headers)


def _read_xlsx(content: bytes, mapping: dict[str, str]) -> tuple[list[dict], list[dict], dict | None]:
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 3_000 or sum(item.file_size for item in entries) > 128_000_000 or any(item.file_size > 64_000_000 for item in entries):
                raise HTTPException(413, 'Workbook expands beyond the safe parsing limit')
    except BadZipFile as exc:
        raise HTTPException(422, 'The uploaded XLSX file is not a valid workbook') from exc
    try:
        book = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise HTTPException(422, 'The uploaded XLSX workbook could not be read') from exc
    try:
        sheets = []
        for sheet in book.worksheets:
            if sheet.max_column > MAX_COLUMNS:
                raise HTTPException(413, f'Worksheet {sheet.title} exceeds the {MAX_COLUMNS}-column limit')
            column_count = max(1, sheet.max_column or 1)
            header_row = None
            headers: list[str] = []
            for row_index, values in enumerate(sheet.iter_rows(min_row=1, max_row=15, max_col=column_count, values_only=True), 1):
                candidate = [_header(value) for value in values]
                if _header_match(candidate, mapping):
                    header_row, headers = row_index, candidate
                    break
            reference = bool(REFERENCE.search(sheet.title))
            eligible = header_row is not None and not reference
            sheets.append({'name': sheet.title, 'eligible': eligible, 'header_row': header_row,
                           'row_count': max(0, (sheet.max_row or 0) - (header_row or 0)),
                           'headers': [value for value in headers if value],
                           'reason': 'Reference worksheets are excluded.' if reference else None,
                           '_sheet': sheet, '_headers': headers})
        eligible_sheets = [item for item in sheets if item['eligible']]
        public = _public_sheets(sheets)
        if len(eligible_sheets) > 1:
            return [], public, None
        if not eligible_sheets:
            raise HTTPException(422, 'No category worksheet with a main category column was found')
        selected = eligible_sheets[0]
        if (selected['_sheet'].max_row or 0) - selected['header_row'] > MAX_ROWS:
            raise HTTPException(413, f'Worksheet exceeds the {MAX_ROWS}-row import limit')
        rows = []
        for row_number, values in enumerate(selected['_sheet'].iter_rows(min_row=selected['header_row'] + 1, max_col=max(1, selected['_sheet'].max_column or 1), values_only=True), selected['header_row'] + 1):
            row: dict[str, object] = {header: str(value).strip()[:MAX_CELL_LENGTH] if value is not None else ''
                                      for header, value in zip(selected['_headers'], values) if header}
            if any(row.values()):
                row['_source_row'] = row_number
                rows.append(row)
        return rows, public, selected
    finally:
        book.close()


def _read_csv(content: bytes, mapping: dict[str, str]) -> tuple[list[dict], list[dict], dict]:
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise HTTPException(422, 'CSV must use UTF-8 encoding') from exc
    reader = csv.DictReader(StringIO(text))
    headers = [_header(value) for value in (reader.fieldnames or [])]
    if not _header_match(headers, mapping):
        raise HTTPException(422, 'CSV must include a main category column')
    rows = []
    for row_number, row in enumerate(reader, 2):
        if row_number > MAX_ROWS + 1:
            raise HTTPException(413, f'CSV exceeds the {MAX_ROWS}-row import limit')
        item: dict[str, object] = {_header(key): str(value or '').strip()[:MAX_CELL_LENGTH] for key, value in row.items() if key}
        if any(item.values()):
            item['_source_row'] = row_number
            rows.append(item)
    sheet = {'name': 'CSV', 'eligible': True, 'header_row': 1, 'row_count': len(rows), 'headers': headers}
    return rows, [sheet], sheet


def _resolve_values(row: dict, mapping: dict[str, str], key: str) -> str:
    mapped = mapping.get(key)
    if mapped:
        return str(row.get(_header(mapped), '')).strip()
    aliases = ALIASES[key]
    return next((str(value).strip() for header, value in row.items() if header in aliases and value), '')


def _existing_path_map(categories: Sequence[Category]) -> dict[tuple[str, ...], Category]:
    by_id = {item.id: item for item in categories}
    paths: dict[tuple[str, ...], Category] = {}
    for category in categories:
        parts = [category.name_en]
        parent_id = category.parent_id
        seen = {category.id}
        while parent_id and parent_id in by_id and parent_id not in seen:
            parent = by_id[parent_id]
            seen.add(parent.id)
            parts.append(parent.name_en)
            parent_id = parent.parent_id
        paths[tuple(_normal(part) for part in reversed(parts))] = category
    return paths


def _category_resolver(db: Session):
    categories = db.scalars(select(Category)).all()
    by_parent_label: dict[tuple[str | None, str], list[Category]] = {}
    by_code = {item.code: item for item in categories}
    for item in categories:
        if item.active:
            by_parent_label.setdefault((item.parent_id, _normal(item.name_en)), []).append(item)
    pending: list[list[Category]] = [[], [], []]

    def resolve(label: str, parent: Category | None, discipline: str) -> tuple[Category, bool]:
        normalized = _normal(label)
        parent_id = parent.id if parent else None
        key = (parent_id, normalized)
        matches = by_parent_label.get(key, [])
        if len(matches) > 1:
            raise HTTPException(409, f'Ambiguous existing category name under parent: {label}')
        if matches:
            return matches[0], False
        code = _category_code(parent_id, label, discipline)
        existing = by_code.get(code)
        if existing:
            if existing.parent_id != parent_id or _normal(existing.name_en) != normalized:
                raise HTTPException(409, f'Generated category identity conflicts with an existing record: {label}')
            return existing, False
        depth = 0 if parent is None else 1 if parent.parent_id is None else 2
        category = Category(id=str(uuid4()), code=code, name_en=label[:180], normalized_name=normalized, name_ar='',
                            parent_id=parent_id, active=True)
        pending[depth].append(category)
        by_parent_label[key] = [category]
        by_code[code] = category
        return category, True

    return resolve, pending


def _preview_response(batch: ImportBatch, rows: Sequence[ImportedRow], sheets: list[dict], headers: list[str], mapping: dict[str, str], selected_sheet: str | None, categories_to_create: int, arabic_labels_to_add: int) -> dict:
    counts = {'create': 0, 'unchanged': 0, 'invalid': 0, 'duplicate': 0}
    samples: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    for item in rows:
        counts[item.status] = counts.get(item.status, 0) + 1
        raw = item.raw
        if len(samples) < 25:
            samples.append({'row': item.row_number, 'discipline': raw.get('_discipline'),
                            'main_category': raw.get('_main'), 'subcategory': raw.get('_subcategory'),
                            'main_category_ar': raw.get('_main_ar'), 'subcategory_ar': raw.get('_subcategory_ar'),
                            'status': item.status, 'issue': item.issue})
        if item.issue and len(issues) < 200:
            issues.append({'row': item.row_number, 'reason': item.issue})
    return {'batch_id': batch.id, 'status': batch.status, 'discipline': next((item.raw.get('_discipline') for item in rows if item.raw.get('_discipline')), None),
            'sheets': sheets, 'selected_sheet': selected_sheet, 'headers': headers, 'mapping': mapping,
            'rows': batch.rows, 'valid': counts['create'] + counts['unchanged'], 'invalid': counts['invalid'],
            'create': counts['create'], 'unchanged': counts['unchanged'], 'duplicate_rows': counts['duplicate'],
            'categories_to_create': categories_to_create, 'arabic_labels_to_add': arabic_labels_to_add,
            'sample': samples, 'issues': issues}


@router.post('/preview', response_model=CategoryImportPreview)
def preview_categories(file: UploadFile = File(...), discipline: str | None = Form(default=None),
                       sheet_name: str | None = Form(default=None), column_mapping: str | None = Form(default=None),
                       user: User = Depends(admin_user), db: Session = Depends(get_db)):
    content = file.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, f'Maximum import size is {MAX_BYTES // 1_000_000} MB')
    mapping = {}
    if column_mapping:
        try:
            mapping = json.loads(column_mapping)
            if len(column_mapping) > 4000 or not isinstance(mapping, dict) or any(key not in ALIASES or not isinstance(value, str) for key, value in mapping.items()):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, 'Column mapping must map category fields to workbook headers') from exc
    filename = file.filename or ''
    if filename.casefold().endswith('.xlsx'):
        raw_rows, sheets, selected = _read_xlsx(content, mapping)
        if not selected:
            if sheet_name:
                raw_rows, sheets, selected = _read_xlsx_selected(content, mapping, sheet_name)
            else:
                return {'status': 'needs_sheet_selection', 'sheets': sheets}
    elif filename.casefold().endswith('.csv'):
        raw_rows, sheets, selected = _read_csv(content, mapping)
    else:
        raise HTTPException(415, 'Upload an XLSX or UTF-8 CSV file')
    if sheet_name and selected and selected['name'] != sheet_name:
        # Re-run discovery with explicit worksheet selection when a workbook has multiple valid tabs.
        if not filename.casefold().endswith('.xlsx'):
            raise HTTPException(422, 'CSV does not support worksheet selection')
        raw_rows, sheets, selected = _read_xlsx_selected(content, mapping, sheet_name)
    if not raw_rows:
        raise HTTPException(422, 'The selected worksheet contains no category rows')
    headers = selected['headers']
    if any(_header(value) not in headers for value in mapping.values()):
        raise HTTPException(422, 'Column mapping references a header that is not present in the selected worksheet')
    detected = {key: next((header for header in headers if header in aliases), '') for key, aliases in ALIASES.items()}
    detected.update({key: _header(value) for key, value in mapping.items()})
    chosen_discipline = DISCIPLINES.get((discipline or '').casefold())
    existing = db.scalars(select(Category).where(Category.active.is_(True))).all()
    existing_paths = _existing_path_map(existing)
    planned = set(existing_paths)
    prepared: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    categories_to_create = 0
    arabic_labels_to_add = 0
    arabic_seen: set[tuple[str, ...]] = set()
    for source in raw_rows:
        main = _resolve_values(source, mapping, 'main_category')
        main_ar = _resolve_values(source, mapping, 'main_category_ar')
        sub = _resolve_values(source, mapping, 'subcategory')
        sub_ar = _resolve_values(source, mapping, 'subcategory_ar')
        raw_discipline = _resolve_values(source, mapping, 'discipline')
        resolved_discipline = DISCIPLINES.get(raw_discipline.casefold()) if raw_discipline else chosen_discipline
        row_number = int(source.get('_source_row', len(prepared) + 2))
        issue = ''
        if not resolved_discipline:
            issue = 'Select Civil, Electrical, or Mechanical, or provide a valid discipline column.'
        elif raw_discipline and not DISCIPLINES.get(raw_discipline.casefold()):
            issue = 'Discipline must be Civil, Electrical, or Mechanical.'
        elif not main:
            issue = 'Missing main category name.'
        elif len(main) > 180 or len(sub) > 180:
            issue = 'Category name exceeds the 180-character limit.'
        status = 'invalid' if issue else 'create'
        to_add = 0
        if not issue and resolved_discipline is not None:
            parts = [main] + ([sub] if sub and _normal(sub)!=_normal(main) else [])
            normalized_path = tuple(_normal(part) for part in parts)
            if normalized_path in seen:
                status, issue = 'duplicate', 'Duplicate category path in this workbook; it will be created only once.'
            else:
                seen.add(normalized_path)
                for end in range(1, len(parts) + 1):
                    prefix = tuple(_normal(part) for part in parts[:end])
                    if prefix not in planned:
                        planned.add(prefix)
                        categories_to_create += 1
                        to_add += 1
                arabic_parts = [main_ar, sub_ar]
                for end in range(1, len(parts) + 1):
                    prefix = tuple(_normal(part) for part in parts[:end])
                    arabic_label = arabic_parts[end - 1]
                    existing_category = existing_paths.get(prefix)
                    if arabic_label and prefix not in arabic_seen and (existing_category is None or not existing_category.name_ar.strip()):
                        arabic_seen.add(prefix)
                        arabic_labels_to_add += 1
                status = 'create' if to_add else 'unchanged'
        raw = {'_source_row': row_number, '_discipline': resolved_discipline, '_main': main, '_main_ar': main_ar,
               '_subcategory': sub, '_subcategory_ar': sub_ar,
               '_sheet': selected['name'], '_mapping': detected}
        prepared.append({'row': row_number, 'raw': raw, 'status': status, 'issue': issue})
    batch = ImportBatch(kind='categories', filename=filename[:200], status='preview', rows=len(prepared),
                        errors=sum(item['status'] in {'invalid', 'duplicate'} for item in prepared), initiated_by=user.id)
    db.add(batch)
    try:
        db.flush()
        staged_rows: list[ImportedRow] = []
        insert_rows = []
        for item in prepared:
            staged = ImportedRow(batch_id=batch.id, row_number=item['row'], raw=item['raw'],
                                 status=item['status'], issue=item['issue'] or None)
            staged_rows.append(staged)
            insert_rows.append({'batch_id': batch.id, 'row_number': item['row'], 'raw': item['raw'],
                                'status': item['status'], 'issue': item['issue'] or None})
        if insert_rows:
            db.execute(insert(ImportedRow), insert_rows)
        audit(db, user, 'category_import.previewed', entity_id=batch.id, rows=len(prepared), categories_to_create=categories_to_create)
        db.commit()
    except ProgrammingError as exc:
        db.rollback()
        _raise_migration_required(exc)
    return _preview_response(batch, staged_rows, sheets, headers, detected, selected['name'], categories_to_create, arabic_labels_to_add)


def _read_xlsx_selected(content: bytes, mapping: dict[str, str], sheet_name: str):
    rows, sheets, selected = _read_xlsx(content, mapping)
    if selected:
        if selected['name'] != sheet_name:
            raise HTTPException(422, 'Selected worksheet does not contain a supported category header')
        return rows, sheets, selected
    # Discover all eligible sheets again and read the exact admin-selected one.
    book = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        for sheet in book.worksheets:
            if sheet.title != sheet_name or REFERENCE.search(sheet.title):
                continue
            column_count = max(1, sheet.max_column or 1)
            header_row = None
            headers = []
            for row_index, values in enumerate(sheet.iter_rows(min_row=1, max_row=15, max_col=column_count, values_only=True), 1):
                candidate = [_header(value) for value in values]
                if _header_match(candidate, mapping):
                    header_row, headers = row_index, candidate
                    break
            if header_row is None:
                break
            selected = {'name': sheet.title, 'eligible': True, 'header_row': header_row,
                        'row_count': max(0, (sheet.max_row or 0) - header_row),
                        'headers': [value for value in headers if value], '_sheet': sheet, '_headers': headers}
            if selected['row_count'] > MAX_ROWS:
                raise HTTPException(413, f'Worksheet exceeds the {MAX_ROWS}-row import limit')
            rows = []
            for row_number, values in enumerate(sheet.iter_rows(min_row=header_row + 1, max_col=column_count, values_only=True), header_row + 1):
                row: dict[str, object] = {header: str(value).strip()[:MAX_CELL_LENGTH] if value is not None else ''
                                          for header, value in zip(headers, values) if header}
                if any(row.values()):
                    row['_source_row'] = row_number
                    rows.append(row)
            return rows, sheets, selected
        raise HTTPException(422, 'Selected worksheet does not contain a supported category header')
    finally:
        book.close()


@router.post('/{batch_id}/confirm', response_model=CategoryImportResult)
def confirm_categories(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    try:
        batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.kind == 'categories').with_for_update())
    except ProgrammingError as exc:
        db.rollback()
        _raise_migration_required(exc)
    if not batch:
        raise HTTPException(404, 'Category import preview not found')
    if batch.initiated_by != user.id:
        raise HTTPException(403, 'Only the administrator who created this preview may confirm it')
    if batch.status != 'preview':
        raise HTTPException(409, 'This category import preview has already been processed')
    rows = db.scalars(select(ImportedRow).where(ImportedRow.batch_id == batch.id).order_by(ImportedRow.row_number)).all()
    resolve_category, pending_categories = _category_resolver(db)
    categories_created = unchanged = errors = arabic_labels_added = 0
    try:
        for row in rows:
            if row.status not in {'create', 'unchanged'}:
                if row.status == 'invalid':
                    errors += 1
                continue
            discipline = str(row.raw['_discipline'])
            main = str(row.raw['_main'])
            sub = str(row.raw.get('_subcategory') or '')
            main_category, made = resolve_category(main, None, discipline)
            categories_created += int(made)
            main_ar = str(row.raw.get('_main_ar') or '').strip()
            if main_ar and not main_category.name_ar:
                main_category.name_ar = main_ar[:180]
                arabic_labels_added += 1
            if sub and _normal(sub)!=_normal(main):
                subcategory, made = resolve_category(sub, main_category, discipline)
                categories_created += int(made)
                sub_ar = str(row.raw.get('_subcategory_ar') or '').strip()
                if sub_ar and not subcategory.name_ar:
                    subcategory.name_ar = sub_ar[:180]
                    arabic_labels_added += 1
            if made:
                row.status = 'created'
            else:
                row.status = 'unchanged'
                unchanged += 1
        for category_layer in pending_categories:
            if category_layer:
                db.add_all(category_layer)
                db.flush()
        batch.created = categories_created
        batch.categories_created = categories_created
        batch.unchanged_count = unchanged
        batch.errors = errors
        batch.status = 'completed_with_errors' if errors else 'completed'
        audit(db, user, 'category_import.completed', entity_id=batch.id, rows=batch.rows,
              categories_created=categories_created, arabic_labels_added=arabic_labels_added,
              unchanged=unchanged, errors=errors)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'A category identity changed during confirmation. Preview the spreadsheet again.') from exc
    except ProgrammingError as exc:
        db.rollback()
        _raise_migration_required(exc)
    if categories_created or arabic_labels_added:
        catalog_cache.invalidate()
    return {'batch_id': batch.id, 'status': batch.status, 'rows': batch.rows,
            'categories_created': categories_created, 'unchanged': unchanged, 'errors': errors,
            'arabic_labels_added': arabic_labels_added}
