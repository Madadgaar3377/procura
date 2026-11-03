"""Staged, repeatable Material Master imports for admin workbooks."""
from __future__ import annotations

import csv
import json
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from time import perf_counter
from typing import Any
from zipfile import BadZipFile, ZipFile
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from openpyxl import load_workbook
from sqlalchemy import case, func, insert, or_, select
from sqlalchemy.orm import Session, aliased

from app import catalog_cache
from app.database import get_db
from app.import_storage import get_import_storage
from app.models import Category, ImportBatch, ImportedRow, Material, Member, Organization, User, MeasurementUnit
from app.schemas import MaterialImportPreview, MaterialImportResult, MaterialSearchPage
from app.security import admin_user, audit, current_user
from app.serializers import record
from app.modules.materials.service import (
    UNIT_NAMES, create_material_record, material_identity,
    normalize_category_name, normalize_unit, resolve_category,
)

router = APIRouter(prefix='/materials', tags=['Material Master Imports'])
MAX_BYTES = 8_000_000
MAX_ROWS = 30_000
MAX_COLUMNS = 120
MAX_CELL_LENGTH = 10_000
DISCIPLINES = {'civil': 'Civil', 'electrical': 'Electrical', 'mechanical': 'Mechanical'}
FIELD_ALIASES: dict[str, set[str]] = {
    'material_name': {'material name', 'description', 'description/pacification', 'description / pacification',
                      'description/specification', 'description / specification', 'item description', 'material description', 'name', 'item name'},
    'main_category': {'main category', 'category', 'category name', 'level 1', 'division'},
    'subcategory': {'subcategory', 'sub category', 'sub-category', 'sub category name', 'level 2', 'group'},
    'source_code': {'sn', 's no', 'serial no', 'serial number', 'material id', 'material code', 'source material id', 'source material code', 'material source id', 'item code', 'source code', 'item number', 'material number'},
    'expenditure_code': {'expenditure', 'expenditures', 'expenditure code', 'expenditure classification', 'expenditure classification code', 'classification code'},
    'unit': {'unit', 'uom', 'unit of measure', 'unit of measurement'},
    'specifications': {'technical specifications', 'technical specification', 'specifications', 'specification', 'technical description'},
    'discipline': {'discipline', 'trade', 'material discipline'},
}
REFERENCE_SHEET = re.compile(r'(^|[^a-z])(reference|suggested|proposal|lookup|legend|classification map|expenditures?)([^a-z]|$)', re.IGNORECASE)


def _clean(value: object) -> str:
    if value is None:
        return ''
    value = str(value).strip()
    if len(value) > MAX_CELL_LENGTH:
        return value[:MAX_CELL_LENGTH]
    return value


def _header(value: object) -> str:
    text = unicodedata.normalize('NFKC', _clean(value)).casefold()
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s/-]', '', text)).strip()


def _normal(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _digest(*values: str) -> str:
    return sha256('\x1f'.join(values).encode('utf-8')).hexdigest()


def _column_map(headers: list[str], explicit: dict[str, str] | None = None) -> dict[str, str]:
    normalized = {_header(h): h for h in headers}
    result: dict[str, str] = {}
    explicit = explicit or {}
    for field, aliases in FIELD_ALIASES.items():
        chosen = explicit.get(field)
        if chosen:
            if _header(chosen) not in normalized:
                raise HTTPException(422, f'Column mapping for {field} does not match a workbook header')
            result[field] = normalized[_header(chosen)]
            continue
        match = next((h for h in headers if _header(h) in aliases), None)
        if match:
            result[field] = match
    return result


def _read_xlsx(content: bytes, selected_name: str | None = None, explicit: dict[str, str] | None = None,
               progress=None) -> tuple[list[dict], list[dict]]:
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 3_000 or sum(entry.file_size for entry in entries) > 128_000_000:
                raise HTTPException(413, 'Workbook expands beyond the safe parsing limit')
            if any(entry.file_size > 64_000_000 for entry in entries):
                raise HTTPException(413, 'Workbook contains an oversized worksheet or asset')
    except BadZipFile as exc:
        raise HTTPException(422, 'The uploaded XLSX file is not a valid workbook') from exc
    try:
        book = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise HTTPException(422, 'The uploaded XLSX workbook could not be read') from exc
    details: list[dict] = []
    sheets: list[dict] = []
    try:
        for sheet in book.worksheets:
            header_row = None
            headers: list[str] = []
            selected_map: dict[str, str] = {}
            fallback_header: tuple[int,list[str],int,int] | None = None
            if sheet.max_column and sheet.max_column>MAX_COLUMNS:
                raise HTTPException(413,f'Worksheet {sheet.title} exceeds the {MAX_COLUMNS}-column import limit')
            scan_columns=max(1,min(sheet.max_column or MAX_COLUMNS+1,MAX_COLUMNS+1))
            for row_number, cells in enumerate(sheet.iter_rows(min_row=1, max_row=30, max_col=scan_columns, values_only=True), start=1):
                candidate = [_clean(c) for c in cells]
                mapping = _column_map(candidate,explicit)
                used_columns=max((index+1 for index,value in enumerate(candidate) if value),default=1)
                if used_columns>MAX_COLUMNS:
                    raise HTTPException(413,f'Worksheet {sheet.title} exceeds the {MAX_COLUMNS}-column import limit')
                if 'material_name' in mapping and ('main_category' in mapping or 'subcategory' in mapping):
                    header_row, headers, selected_map = row_number, candidate, mapping
                    column_count=used_columns
                    break
                populated=sum(bool(value) for value in candidate)
                if populated>=2 and (fallback_header is None or populated>fallback_header[2]):
                    fallback_header=(row_number,candidate,populated,used_columns)
            if header_row is None and fallback_header:
                header_row,headers,_,column_count=fallback_header
            explicit_reference = bool(REFERENCE_SHEET.search(sheet.title))
            eligible = header_row is not None and not explicit_reference
            detail = {'name': sheet.title, 'eligible': eligible, 'header_row': header_row,
                      'headers': [x for x in headers if x], 'reason': ('Reference/suggested worksheets are excluded from material imports.' if explicit_reference else None)}
            if eligible:
                assert header_row is not None
                detail['_map'] = selected_map
                detail['_column_count'] = column_count
                detail['row_count'] = max(0, (sheet.max_row or header_row) - header_row)
            else:
                detail['row_count'] = 0
                detail['headers'] = [x for row in sheet.iter_rows(min_row=1, max_row=5, max_col=20, values_only=True) for x in [_clean(c) for c in row] if x][:30]
            sheets.append(detail)
        eligible_sheets=[item for item in sheets if item['eligible']]
        target=next((item for item in eligible_sheets if item['name']==selected_name),None) if selected_name else (eligible_sheets[0] if len(eligible_sheets)==1 else None)
        if selected_name and target is None:
            raise HTTPException(422,'Selected worksheet is not an eligible material data sheet')
        if target:
            sheet=next(item for item in book.worksheets if item.title==target['name'])
            max_row=sheet.max_row
            if max_row and max_row-target['header_row']>MAX_ROWS:
                raise HTTPException(413,f'Worksheet {sheet.title} exceeds the {MAX_ROWS:,} row limit')
            rows=[]
            nonempty=0
            for row_number,cells in enumerate(sheet.iter_rows(min_row=target['header_row']+1,max_row=max_row,max_col=target['_column_count'],values_only=True),start=target['header_row']+1):
                if row_number-target['header_row']>MAX_ROWS:
                    raise HTTPException(413,f'Worksheet {sheet.title} exceeds the {MAX_ROWS:,} row limit')
                values=['' if c is None else str(c) for c in cells]
                row={target['headers'][i]:values[i] for i in range(min(len(target['headers']),len(values))) if target['headers'][i]}
                if any(row.values()):
                    nonempty+=1;row['_row']=row_number;row['_sheet']=sheet.title;rows.append(row)
                if progress and (row_number-target['header_row'])%500==0:
                    total=max(0,max_row-target['header_row']) if max_row else 0
                    progress(row_number-target['header_row'],total)
            target['row_count']=nonempty;target['_rows']=rows
            if progress:progress(max(0,max_row-target['header_row']) if max_row else nonempty,max(0,max_row-target['header_row']) if max_row else 0)
    finally:
        book.close()
    return sheets, details


def _read_csv(content: bytes, explicit: dict[str,str] | None = None) -> tuple[list[dict], list[dict]]:
    try:
        text = content.decode('utf-8-sig')
        reader = csv.DictReader(StringIO(text))
        headers = [h for h in (reader.fieldnames or []) if h]
        mapping = _column_map(headers,explicit)
        rows: list[dict[str, Any]] = []
        for number, row in enumerate(reader, start=2):
            if number - 1 > MAX_ROWS:
                raise HTTPException(413, f'CSV exceeds the {MAX_ROWS:,} row limit')
            cleaned: dict[str, Any] = {_clean(k): '' if v is None else str(v) for k, v in row.items() if k}
            if any(cleaned.values()):
                cleaned['_row'] = number
                cleaned['_sheet'] = 'CSV'
                rows.append(cleaned)
        return [{'name': 'CSV', 'eligible': True, 'header_row': 1, 'row_count': len(rows), 'headers': headers, '_rows': rows, '_map': mapping}], rows
    except UnicodeDecodeError as exc:
        raise HTTPException(422, 'CSV must use UTF-8 encoding') from exc


def _discover(content: bytes, filename: str, sheet_name: str | None = None,
              explicit: dict[str,str] | None = None, progress=None) -> list[dict]:
    if filename.casefold().endswith('.xlsx'):
        sheets, _ = _read_xlsx(content,sheet_name,explicit,progress)
        return sheets
    if filename.casefold().endswith('.csv'):
        sheets, _ = _read_csv(content,explicit)
        return sheets
    raise HTTPException(415, 'Upload an .xlsx or UTF-8 .csv file')


def _resolve_discipline(value: str | None, filename: str, sheet_name: str | None) -> str | None:
    if value:
        found = DISCIPLINES.get(value.strip().casefold())
        if not found:
            raise HTTPException(422, 'Discipline must be Civil, Electrical, or Mechanical')
        return found
    evidence = re.sub(r'[^a-z]+', ' ', f'{filename} {sheet_name or ""}'.casefold())
    matches = [label for key, label in DISCIPLINES.items() if re.search(rf'\b{key}\b', evidence)]
    return matches[0] if len(matches) == 1 else None


def _category_code(parent_id: str | None, label: str, discipline: str) -> str:
    return 'IMP-' + _digest(discipline.casefold(), parent_id or 'root', _normal(label))[:28].upper()


def _resolve_category(db: Session, label: str, parent: Category | None, discipline: str,
                      cache: dict[tuple[str | None, str], Category] | None = None) -> tuple[Category, bool]:
    del discipline
    return resolve_category(db,label,parent,create=True,cache=cache)


def _materials_by_identity(db: Session, identities: list[str], descriptions: list[str] | None = None) -> dict[str, Material]:
    """Resolve current and legacy fingerprints without rewriting historical IDs.

    Candidate lookup is bounded by the indexed normalized description, rather than
    scanning the catalogue. Archived records remain reserved to avoid resurrection.
    """
    found: dict[str, Material] = {}
    for start in range(0, len(identities), 500):
        chunk = identities[start:start + 500]
        if chunk:
            for material in db.scalars(select(Material).where(Material.source_identity.in_(chunk))).all():
                found[material.source_identity] = material
    names = list({ _normal(name) for name in descriptions or [] })
    wanted = set(identities)
    for start in range(0, len(names), 500):
        chunk = names[start:start + 500]
        candidates = db.scalars(select(Material).where(or_(
            Material.normalized_description.in_(chunk), func.lower(Material.name_en).in_(chunk)
        ))).all()
        category_ids = {key for m in candidates for key in (m.main_category_id, m.subcategory_id) if key}
        categories = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_(category_ids))).all()}
        for material in candidates:
            main = categories.get(material.main_category_id)
            sub = categories.get(material.subcategory_id)
            if not main:
                continue
            identity = material_identity(main_category=main.name_en, subcategory=sub.name_en if sub else '',
                                         description=material.name_en, unit=normalize_unit(material.unit), specifications='')
            if identity in wanted:
                found.setdefault(identity, material)
    return found


def _row_identities(rows: Sequence[ImportedRow]) -> list[str]:
    values: list[str] = []
    for row in rows:
        identity = row.raw.get('_identity')
        if isinstance(identity, str):
            values.append(identity)
    return values


def _row_value(row: dict, mapping: dict[str, str], key: str) -> str:
    return _clean(row.get(mapping.get(key, ''), ''))


def _prepared(rows: list[dict], mapping: dict[str, str], discipline: str,
              known_units: set[str] | None = None) -> list[dict]:
    known_units = set(UNIT_NAMES) | (known_units or set())
    output = []
    for row in rows:
        name = str(row.get(mapping.get('material_name',''), '') or '')
        main = _row_value(row, mapping, 'main_category')
        sub = _row_value(row, mapping, 'subcategory')
        source = _row_value(row, mapping, 'source_code')
        expenditure = _row_value(row, mapping, 'expenditure_code')
        unit = _row_value(row, mapping, 'unit')
        specs = _row_value(row, mapping, 'specifications')
        row_discipline = _row_value(row, mapping, 'discipline')
        if row_discipline:
            inferred = _resolve_discipline(row_discipline, '', '')
            if not inferred:
                output.append({'row': row['_row'], 'status': 'invalid', 'issue': 'Unrecognized discipline value on this row', 'raw': row})
                continue
            if inferred != discipline:
                output.append({'row': row['_row'], 'status': 'invalid', 'issue': 'Row discipline conflicts with the selected import discipline', 'raw': row})
                continue
        issue = ''
        if not name:
            issue = 'Missing material description'
        elif len(_normal(name)) < 3:
            issue = 'Material description must contain 3 to 250 characters'
        elif not main:
            issue = 'Missing main category; a subcategory alone cannot safely establish its parent.'
        elif not unit:
            issue = 'Missing unit of measurement'
        elif any(len(v) > cap for v, cap in ((name, 250), (main, 180), (sub, 180), (unit, 30), (source, 120), (expenditure, 120))):
            issue = 'A mapped value exceeds the supported field length'
        canonical_unit=normalize_unit(unit)
        if not issue and canonical_unit not in known_units:
            issue=f'Unit {canonical_unit} is not configured. Add or map this unit in Material Master before importing.'
        if issue:
            output.append({'row': row['_row'], 'status': 'invalid', 'issue': issue, 'raw': row})
            continue
        parent_name = main
        category_name = sub or main
        identity = material_identity(main_category=parent_name,subcategory=sub,description=name,unit=canonical_unit,specifications=specs)
        duplicate_fingerprint = identity
        output.append({'row': row['_row'], 'status': 'valid', 'issue': '', 'raw': row, 'name': name, 'main': parent_name,
                       'subcategory': sub, 'category_name': category_name, 'source_code': source or None,
                       'expenditure_code': expenditure or None, 'unit': canonical_unit, 'source_unit': unit or '', 'specs': specs or '',
                       'identity': identity, 'duplicate_fingerprint': duplicate_fingerprint})
    return output


def _public_sheets(sheets: list[dict]) -> list[dict]:
    return [{k: v for k, v in item.items() if not k.startswith('_')} for item in sheets]


def _preview_view(batch: ImportBatch, rows: Sequence[ImportedRow], status_counts: Counter[str] | None = None,
                  issue_rows: Sequence[ImportedRow] | None = None) -> dict[str, Any]:
    status_counts = status_counts or Counter(row.status for row in rows)
    sample: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    sheet_name = None
    for item in rows:
        raw = item.raw
        mapping = raw.get('_mapping', mapping)
        sheet_name = raw.get('_sheet', sheet_name)
        if len(sample) < 30:
            sample.append({'row': item.row_number, 'status': 'unchanged' if item.status == 'skip' else item.status, 'name': raw.get('_name', ''),
                           'category': raw.get('_category', ''), 'subcategory': raw.get('_subcategory', ''),
                           'unit': raw.get('_unit', ''), 'issue': item.issue})
    for item in issue_rows if issue_rows is not None else rows:
        if item.issue and len(issues) < 500:
            issues.append({'row': item.row_number, 'reason': item.issue})
    for item in rows:
        if item.status in {'unchanged', 'skip'} and len(warnings) < 500:
            warnings.append({'row': item.row_number, 'reason': 'This material matches an existing imported record and will be skipped.'})
    completed = sum(status_counts[k] for k in ('created', 'updated', 'unchanged', 'invalid', 'duplicate'))
    metadata=batch.preview_metadata or {}
    return {'batch_id': batch.id, 'status': batch.status, 'stage': batch.stage, 'failure_reason': batch.failure_reason,
            'discipline': batch_kind_discipline(rows) or metadata.get('discipline'),
            'sheets':metadata.get('sheets',[]),'headers':metadata.get('headers',[]),
            'selected_sheet': sheet_name or metadata.get('selected_sheet'),
            'mapping': mapping or metadata.get('mapping',{}), 'new_categories':metadata.get('new_categories',[]),
            'upload_duration_ms':batch.upload_duration_ms,'parse_duration_ms':batch.parse_duration_ms,'apply_duration_ms':batch.apply_duration_ms,
            'rows': batch.rows, 'valid': sum(status_counts[k] for k in ('valid', 'create', 'update', 'skip', 'created', 'updated', 'unchanged')),
            'invalid': status_counts['invalid'], 'create': status_counts['create'], 'update': status_counts['update'],
            'unchanged': status_counts['unchanged'] + status_counts['skip'], 'duplicate_rows': status_counts['duplicate'],
            'created': batch.created, 'updated_count': batch.updated_count, 'unchanged_count': batch.unchanged_count,
            'categories_created': batch.categories_created, 'completed_rows': completed,
            'progress_percent': int(completed * 100 / batch.rows) if batch.rows else (100 if batch.status in {'preview','completed','completed_with_errors'} else 0),
            'updated_at': batch.updated_at, 'sample': sample, 'issues': issues, 'warnings': warnings}


def batch_kind_discipline(rows: Sequence[ImportedRow]) -> str | None:
    return next((row.raw.get('_discipline') for row in rows if row.raw.get('_discipline')), None)


def _apply_row(db: Session, item: ImportedRow, identity_map: dict[str, Material],
               category_cache: dict[tuple[str | None, str], Category], unit_cache:dict[str,str],batch_id: str) -> tuple[str, int]:
    raw = item.raw
    identity = raw.get('_identity')
    if not identity:
        item.status, item.issue = 'invalid', 'Import identity could not be resolved'
        return 'invalid', 0
    if identity in identity_map:
        item.status, item.issue = 'unchanged', None
        return 'unchanged', 0
    discipline = str(raw['_discipline'])
    main = str(raw.get('_category') or raw.get('_subcategory') or '')
    sub = str(raw.get('_subcategory') or '')
    categories_created = 0
    # Workbook trade is provenance, never an additional category level.
    main_category,made=resolve_category(db,main,None,create=True,cache=category_cache)
    categories_created+=int(made)
    material_category = main_category
    subcategory = None
    if sub and normalize_category_name(sub)!=normalize_category_name(main):
        subcategory, made = resolve_category(db, sub, main_category, create=True, cache=category_cache)
        categories_created += int(made)
        material_category = subcategory
    material = identity_map.get(identity)
    name = raw.get('_name', '')
    unit = raw.get('_unit') or ''
    specs = raw.get('_specs') or ''
    source_code = raw.get('_source_code')
    expenditure = raw.get('_expenditure_code')
    if material is None:
        material,created=create_material_record(db,actor=None,main_category=main_category,subcategory=subcategory,
                     description=name,unit=unit,source_unit=raw.get('_source_unit') or unit,specifications=specs,
                     arabic_description=raw.get('_arabic_description',''),source_sn=source_code,
                     expenditure_code=expenditure,discipline=discipline,import_batch_id=batch_id,
                     import_row_number=item.row_number,unit_cache=unit_cache)
        identity_map[identity] = material
        item.status = 'created' if created else 'unchanged'
        item.issue = None
        return ('created' if created else 'unchanged'), categories_created
    item.status = 'unchanged'
    item.issue = None
    return 'unchanged', categories_created


def _stage_material_preview(db: Session, batch: ImportBatch) -> None:
    storage=get_import_storage()
    if not batch.source_storage_key or not storage.exists(batch.source_storage_key):
        raise ValueError('The private staged workbook is missing')
    content=storage.read(batch.source_storage_key)
    if batch.source_sha256 and sha256(content).hexdigest()!=batch.source_sha256:
        raise ValueError('The staged workbook checksum does not match')
    options=batch.import_options or {}
    selected_name=options.get('sheet_name')
    explicit=options.get('column_mapping') or {}
    started=perf_counter()
    def report_progress(done:int,total:int):
        batch.completed_rows=done
        batch.rows=total
        batch.updated_at=datetime.now(UTC)
        db.commit()
    sheets=_discover(content,batch.filename,selected_name,explicit,report_progress)
    eligible=[sheet for sheet in sheets if sheet['eligible']]
    metadata={'sheets':_public_sheets(sheets),'headers':[],'mapping':{},'selected_sheet':None,'discipline':None,'new_categories':[]}
    if not eligible:
        batch.status='needs_review';batch.preview_metadata=metadata;batch.rows=0;batch.stage='preview'
        batch.parse_duration_ms=int((perf_counter()-started)*1000);batch.updated_at=datetime.now(UTC);db.commit();return
    if not selected_name and not batch.filename.casefold().endswith('.csv') and len(eligible)>1:
        batch.status='needs_sheet_selection';batch.preview_metadata=metadata;batch.rows=0;batch.stage='preview'
        batch.parse_duration_ms=int((perf_counter()-started)*1000);batch.updated_at=datetime.now(UTC);db.commit();return
    selected=next((item for item in eligible if item.get('_rows') is not None),None)
    if selected is None:
        raise ValueError('The selected worksheet could not be parsed')
    mapping=_column_map(selected['headers'],explicit)
    if 'material_name' not in mapping or not ({'main_category','subcategory'}&mapping.keys()):
        metadata.update({'headers':selected['headers'],'mapping':mapping,'selected_sheet':selected['name']})
        batch.status='needs_review';batch.preview_metadata=metadata;batch.rows=selected['row_count'];batch.stage='preview'
        batch.parse_duration_ms=int((perf_counter()-started)*1000);batch.updated_at=datetime.now(UTC);db.commit();return
    discipline=_resolve_discipline(options.get('discipline'),batch.filename,selected['name'])
    metadata.update({'headers':selected['headers'],'mapping':mapping,'selected_sheet':selected['name'],'discipline':discipline})
    discipline = discipline or ''
    known_units=set(db.scalars(select(MeasurementUnit.code).where(MeasurementUnit.active.is_(True))).all())
    prepared=_prepared(selected['_rows'],mapping,discipline,known_units)
    seen:set[str]=set()
    existing_materials=_materials_by_identity(db,[item['identity'] for item in prepared if item.get('identity')],
                                                [item['name'] for item in prepared if item.get('name')])
    for item in prepared:
        if item['status']!='valid':continue
        identity=item['identity']
        if identity in seen:
            item['status']='duplicate';item['issue']='Duplicate material row in this workbook; remove or correct the duplicate before importing.'
            continue
        seen.add(identity)
        if identity in existing_materials:
            item['status']='skip'
        else:
            item['status']='create'
    categories=db.scalars(select(Category).where(Category.active.is_(True))).all()
    category_by_id={item.id:item for item in categories}
    existing_paths=set()
    for category in categories:
        parts=[category.name_en];parent_id=category.parent_id;seen_parent={category.id}
        while parent_id and parent_id in category_by_id and parent_id not in seen_parent:
            parent=category_by_id[parent_id];seen_parent.add(parent_id);parts.append(parent.name_en);parent_id=parent.parent_id
        existing_paths.add(tuple(normalize_category_name(part) for part in reversed(parts)))
    planned=set(existing_paths);new_categories=[]
    for item in prepared:
        if item['status'] not in {'create','skip'}:continue
        raw=item['raw'];main=_row_value(raw,mapping,'main_category');sub=_row_value(raw,mapping,'subcategory')
        path=[main]
        if sub and normalize_category_name(sub)!=normalize_category_name(path[-1]):path.append(sub)
        for depth in range(1,len(path)+1):
            prefix=tuple(normalize_category_name(value) for value in path[:depth])
            if prefix not in planned:
                planned.add(prefix);new_categories.append({'path':' / '.join(path[:depth]),'depth':depth})
    row_values=[]
    for item in prepared:
        raw=dict(item.get('raw',{}))
        raw.update({'_discipline':discipline,'_mapping':mapping,'_sheet':selected['name'],
                    '_identity':item.get('identity'),'_name':item.get('name',''),
                    '_category':item.get('main',''),'_subcategory':item.get('subcategory',''),
                    '_unit':item.get('unit',''),'_source_unit':item.get('source_unit',''),
                    '_specs':item.get('specs',''),'_source_code':item.get('source_code'),
                    '_expenditure_code':item.get('expenditure_code')})
        row_values.append({'batch_id':batch.id,'row_number':item['row'],'raw':raw,
                           'status':item['status'],'issue':item['issue'] or None})
    if row_values:db.execute(insert(ImportedRow),row_values)
    errors=sum(row['status'] in {'invalid','duplicate'} for row in row_values)
    batch.rows=len(row_values);batch.errors=errors;batch.completed_rows=0
    batch.status='preview';batch.stage='preview';batch.preview_metadata={**metadata,'new_categories':new_categories}
    batch.parse_duration_ms=int((perf_counter()-started)*1000);batch.updated_at=datetime.now(UTC)
    audit(db,db.get(User,batch.initiated_by),'material_import.previewed',entity_id=batch.id,rows=batch.rows,errors=errors)
    db.commit()
    logging.getLogger(__name__).info('Material import parsed batch=%s rows=%s parse_ms=%s',batch.id,batch.rows,batch.parse_duration_ms)


def _fail_material_batch(db:Session,batch_id:str,exc:Exception,stage:str)->None:
    db.rollback()
    batch=db.get(ImportBatch,batch_id)
    if not batch:return
    batch.status='failed';batch.stage=stage
    detail=exc.detail if isinstance(exc,HTTPException) else str(exc)
    batch.failure_reason=str(detail)[:500]
    batch.updated_at=datetime.now(UTC)
    db.commit()


def process_queued_material_imports(db: Session, batch_limit: int = 1, chunk_size: int = 500) -> list[str]:
    """Resume uploaded previews and apply confirmed material batches in bounded commits."""
    log=logging.getLogger(__name__);completed=[];now=datetime.now(UTC)
    for _ in range(max(1,batch_limit)):
        stale=now-timedelta(minutes=10)
        batch=db.scalar(select(ImportBatch).where(
            ImportBatch.kind=='materials',
            (ImportBatch.status.in_({'uploaded','queued'}))|((ImportBatch.status=='processing')&(ImportBatch.updated_at<stale)),
        ).order_by(ImportBatch.created_at).limit(1).with_for_update(skip_locked=True))
        if not batch:break
        if batch.status=='uploaded':batch.stage='parse'
        elif batch.status=='queued':batch.stage='apply'
        stage=batch.stage
        batch.status='processing';batch.updated_at=now;db.commit()
        try:
            if stage=='parse':
                _stage_material_preview(db,batch)
                continue
            if stage!='apply':raise ValueError('Unknown material import worker stage')
            chunk_size=max(1,min(chunk_size,1000))
            pending=db.scalars(select(ImportedRow).where(
                ImportedRow.batch_id==batch.id,ImportedRow.status.in_({'create','skip','valid','update'}),
            ).order_by(ImportedRow.row_number).limit(chunk_size)).all()
            identity_map=_materials_by_identity(db,_row_identities(pending),[row.raw.get('_name','') for row in pending])
            category_cache:dict[tuple[str|None,str],Category]={}
            unit_cache:dict[str,str]={}
            created=updated=unchanged=categories_created=0
            apply_started=perf_counter()
            for row in pending:
                result,made=_apply_row(db,row,identity_map,category_cache,unit_cache,batch.id)
                categories_created+=made;created+=result=='created';updated+=result=='updated';unchanged+=result=='unchanged'
            batch.created+=created;batch.updated_count+=updated;batch.unchanged_count+=unchanged
            batch.categories_created+=categories_created;batch.completed_rows+=len(pending)
            batch.apply_duration_ms+=int((perf_counter()-apply_started)*1000)
            batch.updated_at=datetime.now(UTC)
            remaining=db.scalar(select(func.count()).select_from(ImportedRow).where(
                ImportedRow.batch_id==batch.id,ImportedRow.status.in_({'create','skip','valid','update'}))) or 0
            if remaining==0:
                batch.status='completed_with_errors' if batch.errors else 'completed';batch.stage='complete'
                batch.completed_rows=batch.rows;batch.updated_at=datetime.now(UTC)
                audit(db,db.get(User,batch.initiated_by),'material_import.confirmed',entity_id=batch.id,
                      rows=batch.rows,created=batch.created,unchanged=batch.unchanged_count,errors=batch.errors)
                db.commit();completed.append(batch.id)
                if created or updated:catalog_cache.invalidate()
                if batch.source_storage_key:
                    get_import_storage().delete(batch.source_storage_key);batch.source_storage_key=None;db.commit()
                log.info('Material import applied batch=%s rows=%s apply_ms=%s',batch.id,batch.rows,batch.apply_duration_ms)
            else:
                batch.status='queued';db.commit()
                if created or updated:catalog_cache.invalidate()
        except Exception as exc:
            log.exception('Material import worker failed batch=%s stage=%s error=%s',batch.id,stage,type(exc).__name__)
            _fail_material_batch(db,batch.id,exc,stage)
    cleanup_material_import_sources(db)
    return completed


def cleanup_material_import_sources(db:Session,retention_days:int=7)->int:
    cutoff=datetime.now(UTC)-timedelta(days=max(1,retention_days))
    old=db.scalars(select(ImportBatch).where(ImportBatch.kind=='materials',ImportBatch.source_storage_key.is_not(None),ImportBatch.updated_at<cutoff)).all()
    storage=get_import_storage();deleted=0
    for batch in old:
        try:storage.delete(batch.source_storage_key)
        except (OSError,ValueError):logging.getLogger(__name__).warning('Could not clean private material import file batch=%s',batch.id)
        batch.source_storage_key=None
        if batch.status in {'uploaded','needs_review','needs_discipline','needs_sheet_selection','preview'}:
            batch.status='expired';batch.failure_reason='Import preview expired after seven days.'
        deleted+=1
    if deleted:db.commit()
    return deleted


@router.post('/preview', response_model=MaterialImportPreview, status_code=202)
def preview(
    file: UploadFile = File(...), discipline: str | None = Form(default=None), sheet_name: str | None = Form(default=None),
    column_mapping: str | None = Form(default=None), user: User = Depends(admin_user), db: Session = Depends(get_db),
):
    """Stage an admin workbook locally and return a background job (202).

    Map Main Category, Subcategory, Description and Unit. Workbook trade is
    optional provenance; it never creates another category level. Poll the batch
    for real parsing/application progress, then confirm the validated preview.
    """
    explicit: dict[str, str] = {}
    if column_mapping:
        if len(column_mapping) > 4000:
            raise HTTPException(422, 'Column mapping is too large')
        try:
            explicit = json.loads(column_mapping)
            if not isinstance(explicit, dict) or any(k not in FIELD_ALIASES or not isinstance(v, str) for k, v in explicit.items()):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, 'Column mapping must map supported fields to header names') from exc
    filename = Path(file.filename or 'material-import').name
    suffix = Path(filename).suffix.casefold()
    if suffix not in {'.xlsx', '.csv'}:
        raise HTTPException(415, 'Upload an .xlsx or UTF-8 .csv file')
    storage = get_import_storage()
    job_id = str(uuid4())
    key = f'{job_id}/source{suffix}'
    started = perf_counter()
    try:
        size, digest = storage.write(key, file.file, MAX_BYTES)
    except ValueError as exc:
        raise HTTPException(413, 'Maximum import size is 8 MB') from exc
    if not size:
        storage.delete(key)
        raise HTTPException(422, 'The uploaded file is empty')
    content_prefix = storage.read(key)[:8]
    if suffix == '.xlsx' and not content_prefix.startswith(b'PK'):
        storage.delete(key)
        raise HTTPException(422, 'The uploaded XLSX file is not a valid workbook')
    if suffix == '.csv':
        try:
            storage.read(key).decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            storage.delete(key)
            raise HTTPException(422, 'CSV must use UTF-8 encoding') from exc
    batch = ImportBatch(id=job_id, kind='materials', filename=filename[:200], status='uploaded', stage='parse',
        rows=0, completed_rows=0, initiated_by=user.id, source_storage_key=key, source_bytes=size,
        source_sha256=digest.hex(), import_options={'discipline': discipline, 'sheet_name': sheet_name, 'column_mapping': explicit},
        upload_duration_ms=int((perf_counter() - started) * 1000), updated_at=datetime.now(UTC))
    db.add(batch)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    return {'batch_id': batch.id, 'status': 'uploaded', 'rows': 0, 'completed_rows': 0, 'progress_percent': 0}


@router.post('/{batch_id}/preview-options', response_model=MaterialImportPreview, status_code=202)
def update_preview_options(batch_id: str, discipline: str | None = Form(default=None), sheet_name: str | None = Form(default=None),
                           column_mapping: str | None = Form(default=None), user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.kind == 'materials').with_for_update())
    if not batch:
        raise HTTPException(404, 'Material import not found')
    if batch.initiated_by != user.id:
        raise HTTPException(403, 'Only the administrator who uploaded this workbook may change its preview')
    if batch.status not in {'needs_review', 'needs_discipline', 'needs_sheet_selection', 'failed'} or not batch.source_storage_key:
        raise HTTPException(409, 'This workbook is not waiting for preview changes')
    explicit: dict[str, str] = {}
    if column_mapping:
        try:
            explicit = json.loads(column_mapping)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, 'Column mapping is invalid') from exc
        if not isinstance(explicit, dict) or any(k not in FIELD_ALIASES or not isinstance(v, str) for k, v in explicit.items()):
            raise HTTPException(422, 'Column mapping must map supported fields to header names')
    db.execute(__import__('sqlalchemy').delete(ImportedRow).where(ImportedRow.batch_id == batch.id))
    batch.import_options = {'discipline': discipline, 'sheet_name': sheet_name, 'column_mapping': explicit}
    batch.status = 'uploaded'; batch.stage = 'parse'; batch.failure_reason = None
    batch.rows = 0; batch.errors = 0; batch.completed_rows = 0; batch.preview_metadata = None
    batch.updated_at = datetime.now(UTC)
    db.commit()
    return {'batch_id': batch.id, 'status': 'uploaded', 'rows': 0, 'completed_rows': 0, 'progress_percent': 0}


@router.post('/{batch_id}/confirm', response_model=MaterialImportResult)
def confirm(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.kind == 'materials').with_for_update())
    if not batch:
        raise HTTPException(404, 'Material import preview not found')
    if batch.initiated_by != user.id:
        raise HTTPException(403, 'Only the administrator who created this preview may confirm it')
    if batch.status != 'preview':
        raise HTTPException(409, 'This import preview is not ready to confirm')
    batch.status = 'queued'; batch.stage = 'apply'; batch.completed_rows = 0; batch.updated_at = datetime.now(UTC)
    audit(db, user, 'material_import.queued', entity_id=batch.id, rows=batch.rows)
    db.commit()
    return {'batch_id': batch.id, 'status': 'queued', 'rows': batch.rows, 'created': 0,
            'updated': 0, 'unchanged': 0, 'errors': batch.errors, 'categories_created': 0}


@router.get('/{batch_id}', response_model=MaterialImportPreview)
def import_status(batch_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.kind == 'materials'))
    if not batch:
        raise HTTPException(404, 'Material import not found')
    base = select(ImportedRow).where(ImportedRow.batch_id == batch.id)
    status_counts = Counter({status: count for status, count in db.execute(
        select(ImportedRow.status, func.count()).where(ImportedRow.batch_id == batch.id).group_by(ImportedRow.status)).all()})
    sample = db.scalars(base.order_by(ImportedRow.row_number).limit(30)).all()
    issue_rows = db.scalars(base.where(ImportedRow.issue.is_not(None)).order_by(ImportedRow.row_number).limit(500)).all()
    return _preview_view(batch, sample, status_counts, issue_rows)


@router.get('', response_model=MaterialSearchPage)
def search_materials(
    q: str = '', discipline: str | None = None, category_id: str | None = None,
    limit: int = 25, offset: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db),
):
    if not user.is_admin:
        authorized = db.scalar(select(Member.id).join(Organization, Organization.id == Member.organization_id).where(
            Member.user_id == user.id, Organization.is_active.is_(True),
            or_(Organization.is_buyer.is_(True), Organization.is_supplier.is_(True)),
        ).limit(1))
        if not authorized:
            raise HTTPException(403, 'An active buyer or supplier organization is required to search materials')
    if len(q) > 100:
        raise HTTPException(422, 'Search text must be 100 characters or fewer')
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(422, 'Limit must be 1–100 and offset must be non-negative')
    if discipline:
        value = DISCIPLINES.get(discipline.casefold())
        if not value:
            raise HTTPException(422, 'Discipline must be Civil, Electrical, or Mechanical')
    else:
        value = None
    normalized_q = ' '.join(q.casefold().split())
    params = {'q': normalized_q, 'discipline': value, 'category_id': category_id,
              'limit': limit, 'offset': offset, 'sort': 'relevance-or-code-v1'}
    cache_epoch = catalog_cache.version()
    cached = catalog_cache.get('material-search', params, cache_epoch)
    if cached is not None:
        return cached
    parent_category = aliased(Category)
    query = select(Material).join(Category, Category.id == Material.category_id).outerjoin(
        parent_category, parent_category.id == Category.parent_id).where(Material.active.is_(True))
    if value:
        query = query.where(Material.discipline == value)
    if category_id:
        query = query.where(Material.category_id == category_id)
    if normalized_q:
        escaped = normalized_q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        prefix = f'{escaped}%'
        predicates = [Material.code.ilike(prefix, escape='\\'), Material.name_en.ilike(prefix, escape='\\'),
                      Material.name_ar.ilike(prefix, escape='\\'), Material.source_material_code.ilike(prefix, escape='\\'),
                      Material.expenditure_code.ilike(prefix, escape='\\'), Category.name_en.ilike(prefix, escape='\\'),
                      parent_category.name_en.ilike(prefix, escape='\\')]
        predicates.append(Material.name_en.ilike(f'%{escaped}%', escape='\\'))
        if len(normalized_q) >= 3:
            contains = f'%{escaped}%'
            predicates.extend([Material.code.ilike(contains, escape='\\'), Material.name_en.ilike(contains, escape='\\'),
                               Material.normalized_description.ilike(contains,escape='\\'),
                               Material.name_ar.ilike(contains, escape='\\'), Material.description.ilike(contains, escape='\\'),
                               Material.source_material_code.ilike(contains, escape='\\'), Material.expenditure_code.ilike(contains, escape='\\'),
                               Category.name_en.ilike(contains, escape='\\'), parent_category.name_en.ilike(contains, escape='\\')])
            if db.bind is not None and db.bind.dialect.name == 'postgresql':
                predicates.extend([Material.normalized_description.op('%')(normalized_q), Material.description.op('%')(normalized_q)])
        query = query.where(or_(*predicates))
        relevance_cases = [
            (func.lower(Material.code) == normalized_q, 0),
            (func.lower(Material.name_en) == normalized_q, 1),
            (func.lower(Material.code).like(prefix, escape='\\'), 2),
            (func.lower(Material.name_en).like(prefix, escape='\\'), 3),
        ]
        if len(normalized_q) >= 3:
            contains = f'%{escaped}%'
            relevance_cases.extend([
                (func.lower(Material.name_en).like(contains, escape='\\'), 4),
                (func.lower(Material.description).like(contains, escape='\\'), 5),
            ])
            relevance = case(*relevance_cases, else_=6)
        else:
            relevance = case(*relevance_cases, else_=4)
        ordering: tuple[Any, ...] = (relevance, Material.code, Material.id)
        if len(normalized_q) >= 3 and db.bind is not None and db.bind.dialect.name == 'postgresql':
            similarity = func.greatest(func.similarity(Material.normalized_description, normalized_q),
                                       func.similarity(Material.description, normalized_q))
            ordering = (relevance, similarity.desc(), Material.code, Material.id)
    else:
        ordering = (Material.code, Material.id)
    # Count is retained for API compatibility. Query predicates remain bounded;
    # a follow-up cursor contract can remove this count without breaking callers.
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    page = db.scalars(query.order_by(*ordering).limit(limit).offset(offset)).all()
    response = {'items': [record(m) for m in page], 'total': total, 'limit': limit, 'offset': offset}
    catalog_cache.set('material-search', params, response, epoch=cache_epoch)
    return response
