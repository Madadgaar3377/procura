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
from openpyxl import load_workbook, Workbook
from sqlalchemy import case, delete, func, insert, or_, select, text
from sqlalchemy.orm import Session, aliased
from sqlalchemy.exc import DBAPIError, IntegrityError
from fastapi.responses import StreamingResponse
from math import ceil

from app import catalog_cache
from app.database import get_db
from app.import_storage import get_import_storage
from app.models import Category, ImportBatch, ImportedRow, Material, MaterialRevision, MeasurementUnit, Member, Organization, User
from app.schemas import MaterialImportPreview, MaterialImportResult, MaterialSearchPage
from app.security import admin_user, audit, current_user
from app.serializers import record
from app.modules.materials.service import (
    create_material_record, material_identity,
    normalize_category_name, normalize_unit, resolve_category,
)

router = APIRouter(prefix='/materials', tags=['Material Master Imports'])
JOB_ROWS = 300
BATCH_ROWS = 30
MATERIAL_WORKER_LOCK = 717341393
MAX_BYTES = 8_000_000
MAX_ROWS = 30_000
MAX_COLUMNS = 120
MAX_CELL_LENGTH = 10_000
DISCIPLINES = {'civil': 'Civil', 'electrical': 'Electrical', 'mechanical': 'Mechanical'}
FIELD_ALIASES: dict[str, set[str]] = {
    'material_name': {'material name', 'description', 'description/pacification', 'description / pacification',
                      'description/specification', 'description / specification', 'item description', 'material description', 'name', 'item name'},
    'main_category': {'main category', 'category', 'main', 'category name', 'level 1', 'division'},
    'subcategory': {'subcategory', 'sub category', 'sub-category', 'sub category name', 'level 2', 'group'},
    'source_code': {'sn', 's no', 'serial no', 'serial number', 'material id', 'material code', 'source material id', 'source material code', 'material source id', 'item code', 'source code', 'item number', 'material number'},
    'expenditure_code': {'expenditure', 'expenditures', 'expenditure code', 'expenditure classification', 'expenditure classification code', 'classification code'},
    'unit': {'unit', 'uom', 'unit of measure', 'unit of measurement'},
    'specifications': {'technical specifications', 'technical specification', 'specifications', 'specification', 'technical description'},
    'discipline': {'discipline', 'trade', 'material discipline'},
}
REFERENCE_SHEET = re.compile(r'(^|[^a-z])(example|instructions|reference|suggested|proposal|lookup|legend|classification map|expenditures?)([^a-z]|$)', re.IGNORECASE)


def _clean(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value,float) and value.is_integer():
        value=int(value)
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
        if field in explicit:
            chosen = explicit[field]
            if not chosen:
                continue
            if _header(chosen) not in normalized:
                raise HTTPException(422, f'Column mapping for {field} does not match a workbook header')
            result[field] = normalized[_header(chosen)]
            continue
        match = next((h for h in headers if _header(h) in aliases), None)
        if match:
            result[field] = match
    return result


class _XlsSheetAdapter:
    def __init__(self,sheet):
        self._sheet=sheet;self.title=sheet.name;self.max_row=sheet.nrows;self.max_column=sheet.ncols
    def iter_rows(self,min_row=1,max_row=None,max_col=None,values_only=True):
        del values_only
        stop=min(self.max_row,max_row if max_row is not None else self.max_row)
        width=min(self.max_column,max_col if max_col is not None else self.max_column)
        for row_idx in range(min_row-1,stop):
            yield tuple(self._sheet.cell_value(row_idx,col) for col in range(width))


def _read_xlsx(content: bytes, selected_name: str | None = None, explicit: dict[str, str] | None = None,
               progress=None, legacy_xls: bool = False) -> tuple[list[dict], list[dict]]:
    if legacy_xls:
        try:
            import xlrd
            book=xlrd.open_workbook(file_contents=content,on_demand=True)
            worksheets=[_XlsSheetAdapter(sheet) for sheet in book.sheets()]
        except ImportError as exc:
            raise HTTPException(503,'Legacy XLS support is not installed on this server') from exc
        except Exception as exc:
            raise HTTPException(422,'The uploaded XLS workbook could not be read') from exc
    else:
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
            worksheets=book.worksheets
        except Exception as exc:
            raise HTTPException(422, 'The uploaded XLSX workbook could not be read') from exc
    details: list[dict] = []
    sheets: list[dict] = []
    try:
        for sheet in worksheets:
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
        targets=eligible_sheets if selected_name=='__all__' else ([next((item for item in eligible_sheets if item['name']==selected_name),None)] if selected_name else ([eligible_sheets[0]] if len(eligible_sheets)==1 else []))
        target=targets[0] if len(targets)==1 and selected_name!='__all__' else None
        if selected_name and target is None:
            if selected_name!='__all__' or not targets:
                raise HTTPException(422,'Selected worksheet is not an eligible material data sheet')
        if selected_name=='__all__':
            combined=[];source_headers=[];total=0
            for item in targets:
                sheet=next(sheet for sheet in worksheets if sheet.title==item['name'])
                max_row=sheet.max_row;mapping=_column_map(item['headers'],explicit)
                if 'material_name' not in mapping or not ({'main_category','subcategory'}&mapping.keys()):continue
                source_headers.extend(header for header in item['headers'] if header and header not in source_headers)
                if max_row and max_row-item['header_row']>MAX_ROWS:
                    raise HTTPException(413,f'Worksheet {sheet.title} exceeds the {MAX_ROWS:,} row limit')
                nonempty=0
                for row_number,cells in enumerate(sheet.iter_rows(min_row=item['header_row']+1,max_row=max_row,max_col=item['_column_count'],values_only=True),start=item['header_row']+1):
                    values=['' if c is None else str(c) for c in cells]
                    raw={item['headers'][i]:values[i] for i in range(min(len(item['headers']),len(values))) if item['headers'][i]}
                    if not any(raw.values()):continue
                    row={field:raw.get(header,'') for field,header in mapping.items()}
                    row['_row']=row_number;row['_sheet']=sheet.title;combined.append(row);nonempty+=1;total+=1
                    if total>MAX_ROWS*10:raise HTTPException(413,'Combined worksheets exceed the 300,000-row import limit')
                    if progress and total%500==0:progress(total,total)
                item['row_count']=nonempty
            if not combined:raise HTTPException(422,'No eligible material rows were found in the selected worksheets')
            aggregate={'name':'All eligible worksheets','eligible':True,'header_row':1,
                'headers':list(dict.fromkeys(key for row in combined for key in row if not key.startswith('_'))),
                'row_count':len(combined),'_rows':combined,'_map':{key:key for key in FIELD_ALIASES if any(key in row for row in combined)}}
            sheets.append(aggregate)
            details.append(aggregate)
            if progress:progress(total,total)
        elif target:
            sheet=next(item for item in worksheets if item.title==target['name'])
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
        if legacy_xls:book.release_resources()
        else:book.close()
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
    if filename.casefold().endswith(('.xlsx','.xls')):
        sheets, _ = _read_xlsx(content,sheet_name,explicit,progress,filename.casefold().endswith('.xls'))
        return sheets
    if filename.casefold().endswith('.csv'):
        sheets, _ = _read_csv(content,explicit)
        return sheets
    raise HTTPException(415, 'Upload an .xlsx, .xls, or UTF-8 .csv file')


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
        category_ids = {key for m in candidates for key in (m.category_id, m.main_category_id, m.subcategory_id) if key}
        categories = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_(category_ids))).all()}
        parent_ids={c.parent_id for c in categories.values() if c.parent_id}
        categories.update({c.id:c for c in db.scalars(select(Category).where(Category.id.in_(parent_ids)))})
        for material in candidates:
            leaf=categories.get(material.category_id)
            main = categories.get(material.main_category_id) or (categories.get(leaf.parent_id) if leaf and leaf.parent_id else leaf)
            sub = categories.get(material.subcategory_id) or (leaf if leaf and leaf.parent_id else None)
            if not main:
                continue
            identity = material_identity(main_category=main.name_en, subcategory=sub.name_en if sub else '',
                                         description=material.name_en, unit=normalize_unit(material.unit), specifications='')
            if identity in wanted:
                if identity in found and found[identity] is not None and found[identity].id != material.id:
                    found[identity] = None
                elif identity not in found:
                    found[identity] = material
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


def _prepared(rows: list[dict], mapping: dict[str, str], discipline: str) -> list[dict]:
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
        elif sub and _normal(main) in DISCIPLINES and _normal(sub) in DISCIPLINES:
            issue = 'Do not nest discipline names as both main category and subcategory; keep Discipline in its own column.'
        elif not unit:
            issue = 'Missing unit of measurement'
        elif any(len(v) > cap for v, cap in ((name, 250), (main, 180), (sub, 180), (unit, 30), (source, 120), (expenditure, 120))):
            issue = 'A mapped value exceeds the supported field length'
        canonical_unit=normalize_unit(unit)
        if not issue and (not canonical_unit or not re.fullmatch(r'[\w./%-]+',canonical_unit,flags=re.UNICODE)):
            issue='Unit contains unsupported characters; preserve/correct the unit value before importing.'
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
    metadata=batch.preview_metadata or {}
    completed=batch.completed_rows if batch.stage in {'apply','complete','parse'} else 0
    total_jobs=ceil(batch.rows/JOB_ROWS) if batch.rows else 0
    current_job=min(completed//JOB_ROWS+1,total_jobs) if total_jobs else 0
    job_start=max(0,current_job-1)*JOB_ROWS
    batches_in_job=ceil(min(JOB_ROWS,max(0,batch.rows-job_start))/BATCH_ROWS)
    current_batch=min((completed-job_start)//BATCH_ROWS+1,batches_in_job) if batches_in_job else 0
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
            'errors':batch.errors,'conflicts':status_counts['conflict'],
            'existing_categories_reused':metadata.get('existing_categories_reused',0),
            'jobs_total':total_jobs,'current_job':current_job,'current_batch':current_batch,
            'batches_in_job':batches_in_job,'job_size':JOB_ROWS,'batch_size':BATCH_ROWS,
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
        material = identity_map[identity]
        if material is None or not material.active:
            raise HTTPException(409, 'Ambiguous or archived material identity; review the master records')
        changes = {}
        for field, incoming in (('expenditure_code', raw.get('_expenditure_code')),
                                ('source_sn', raw.get('_source_code')),
                                ('source_material_code', raw.get('_source_code'))):
            current = getattr(material, field)
            if current and incoming and current != incoming:
                raise HTTPException(409, 'Existing material metadata conflicts; review before updating')
            if incoming and not current:
                changes[field] = incoming
        if changes:
            from app.modules.materials.service import material_snapshot
            for field, value in changes.items():
                setattr(material, field, value)
            material.version += 1
            db.add(MaterialRevision(material_id=material.id, version=material.version,
                action='import_enriched', snapshot=material_snapshot(material)))
            item.status, item.issue = 'updated', None
            return 'updated', 0
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
                     import_row_number=item.row_number,unit_cache=unit_cache,create_unknown_unit=True)
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
    # A parse can be retried after a worker restart. Remove any preview rows
    # committed by an interrupted attempt before starting a clean parse.
    db.execute(delete(ImportedRow).where(ImportedRow.batch_id==batch.id))
    batch.completed_rows=0
    db.commit()
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
    mapping=selected.get('_map') or _column_map(selected['headers'],explicit)
    if 'material_name' not in mapping or not ({'main_category','subcategory'}&mapping.keys()):
        metadata.update({'headers':selected['headers'],'mapping':mapping,'selected_sheet':selected['name']})
        batch.status='needs_review';batch.preview_metadata=metadata;batch.rows=selected['row_count'];batch.stage='preview'
        batch.parse_duration_ms=int((perf_counter()-started)*1000);batch.updated_at=datetime.now(UTC);db.commit();return
    discipline=_resolve_discipline(options.get('discipline'),batch.filename,selected['name'])
    metadata.update({'headers':selected['headers'],'mapping':mapping,
        'selected_sheet':'__all__' if selected_name=='__all__' else selected['name'],'discipline':discipline})
    discipline = discipline or ''
    prepared=_prepared(selected['_rows'],mapping,discipline)
    seen:set[str]=set()
    existing_materials=_materials_by_identity(db,[item['identity'] for item in prepared if item.get('identity')],
                                                [item['name'] for item in prepared if item.get('name')])
    source_codes = {item['source_code'] for item in prepared if item.get('source_code')}
    source_owners = {}
    for start in range(0, len(source_codes), 500):
        codes = list(source_codes)[start:start+500]
        for material in db.scalars(select(Material).where(or_(Material.source_sn.in_(codes), Material.source_material_code.in_(codes)))):
            for code in (material.source_sn, material.source_material_code):
                if code: source_owners.setdefault(code, set()).add(material.id)
    source_seen={}
    for item in prepared:
        if item['status'] != 'valid': continue
        identity = item['identity']
        if identity in seen:
            item['status'], item['issue'] = 'duplicate', 'Duplicate material identity in this workbook; first valid row retained.'
            continue
        seen.add(identity)
        source=item.get('source_code')
        if source and source in source_seen and source_seen[source]!=identity:
            item['status'],item['issue']='conflict','Source code is reused for different materials in this workbook.'
            continue
        if source:source_seen[source]=identity
        existing = existing_materials.get(identity)
        owners = source_owners.get(item.get('source_code'), set())
        if identity in existing_materials and (existing is None or not existing.active):
            item['status'], item['issue'] = 'conflict', 'Ambiguous or archived material identity; master records require review.'
        elif owners and (not existing or owners != {existing.id}):
            item['status'], item['issue'] = 'conflict', 'Source code already identifies a different material; description/classification will not be overwritten.'
        elif existing:
            incoming = (('expenditure_code', item.get('expenditure_code')), ('source_sn', item.get('source_code')))
            if any(getattr(existing, field) and value and getattr(existing, field) != value for field, value in incoming):
                item['status'], item['issue'] = 'conflict', 'Existing material metadata conflicts; review required.'
            else:
                item['status'] = 'update' if any(value and not getattr(existing, field) for field, value in incoming) else 'skip'
        else:
            item['status'] = 'create'
    categories=db.scalars(select(Category).where(Category.active.is_(True))).all()
    category_by_id={item.id:item for item in categories}
    existing_paths=set()
    for category in categories:
        parts=[category.name_en];parent_id=category.parent_id;seen_parent={category.id}
        while parent_id and parent_id in category_by_id and parent_id not in seen_parent:
            parent=category_by_id[parent_id];seen_parent.add(parent_id);parts.append(parent.name_en);parent_id=parent.parent_id
        existing_paths.add(tuple(normalize_category_name(part) for part in reversed(parts)))
    planned=set(existing_paths);new_categories=[];reused_paths=set()
    path_counts=Counter()
    for category in categories:
        parent=category_by_id.get(category.parent_id)
        path_counts[((normalize_category_name(parent.name_en),) if parent else ())+ (normalize_category_name(category.name_en),)] += 1
    for item in prepared:
        if item['status'] not in {'create','skip','update'}:continue
        raw=item['raw'];main=_row_value(raw,mapping,'main_category');sub=_row_value(raw,mapping,'subcategory')
        path=[main]
        if sub and normalize_category_name(sub)!=normalize_category_name(path[-1]):path.append(sub)
        for depth in range(1,len(path)+1):
            prefix=tuple(normalize_category_name(value) for value in path[:depth])
            if path_counts[prefix]>1:
                item['status'],item['issue']='conflict','Duplicate category identity requires review before this row can be imported.'
            if prefix in existing_paths:reused_paths.add(prefix)
            if prefix not in planned:
                planned.add(prefix);new_categories.append({'path':' / '.join(path[:depth]),'depth':depth})
    row_values=[]
    for item in prepared:
        raw=dict(item.get('raw',{}))
        raw.update({'_discipline':discipline,'_mapping':mapping,'_sheet':raw.get('_sheet') or selected['name'],
                    '_identity':item.get('identity'),'_name':item.get('name',''),
                    '_category':item.get('main',''),'_subcategory':item.get('subcategory',''),
                    '_unit':item.get('unit',''),'_source_unit':item.get('source_unit',''),
                    '_specs':item.get('specs',''),'_source_code':item.get('source_code'),
                    '_expenditure_code':item.get('expenditure_code')})
        row_values.append({'batch_id':batch.id,'row_number':item['row'],'raw':raw,
                           'status':item['status'],'issue':item['issue'] or None})
    errors=sum(row['status'] in {'invalid','duplicate','conflict'} for row in row_values)
    batch.rows=len(row_values);batch.errors=errors;batch.completed_rows=0;batch.updated_at=datetime.now(UTC)
    db.commit()
    # Keep each JSONB/JSON executemany small for hosted PostgreSQL connections.
    # Commits also make real progress visible while the full preview is stored.
    for offset in range(0,len(row_values),100):
        chunk=row_values[offset:offset+100]
        db.execute(insert(ImportedRow),chunk)
        batch.completed_rows=offset+len(chunk)
        batch.updated_at=datetime.now(UTC)
        db.commit()
    batch.status='preview';batch.stage='preview';batch.preview_metadata={**metadata,'new_categories':new_categories,'existing_categories_reused':len(reused_paths)}
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


def _material_lock(db):
    # Transaction-scoped: compatible with transaction poolers, released on crash.
    if db.bind.dialect.name == 'postgresql':
        return db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': MATERIAL_WORKER_LOCK})
    if db.bind.dialect.name=='sqlite':
        connection=db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN')
    return True


def _preload_maps(db):
    grouped = {}
    for category in db.scalars(select(Category).where(Category.active.is_(True))):
        grouped.setdefault((category.parent_id, normalize_category_name(category.name_en)), []).append(category)
    categories = {key: values[0] for key, values in grouped.items() if len(values) == 1}
    units = {code: code for code in db.scalars(select(MeasurementUnit.code).where(MeasurementUnit.active.is_(True)))}
    return categories, units


def process_queued_material_imports(db: Session, batch_limit: int = 1, chunk_size: int = BATCH_ROWS) -> list[str]:
    """Each invocation runs at most 300 rows/job, committing every <=30 rows.

    The durable cursor, row outcomes, business changes and counters commit together.
    Another worker can safely continue after ANY commit or rollback without replaying
    completed rows. No HTTP request runs this application loop.
    """
    completed = []
    size = max(1, min(chunk_size, BATCH_ROWS))
    for _ in range(max(1, batch_limit)):
        batch_id = None
        stage = 'parse'
        try:
            if not _material_lock(db):
                db.rollback(); break
            stale = datetime.now(UTC)-timedelta(minutes=10)
            batch = db.scalar(select(ImportBatch).where(ImportBatch.kind=='materials',
                (ImportBatch.status.in_({'uploaded','queued'})) |
                ((ImportBatch.status=='processing') & (ImportBatch.updated_at<stale)))
                .order_by(ImportBatch.created_at,ImportBatch.id).limit(1).with_for_update(skip_locked=True))
            if not batch:
                db.rollback(); break
            batch_id = batch.id
            stage = 'parse' if batch.status=='uploaded' else batch.stage
            if stage == 'parse':
                batch.status='processing';batch.stage='parse';batch.updated_at=datetime.now(UTC);db.commit()
                _stage_material_preview(db,batch)
                continue
            if stage != 'apply': raise ValueError('Unknown material import stage')
            if (batch.import_options or {}).get('cursor_version')!=2:
                # Old workers counted successes rather than source-order positions.
                # Revisit terminal rows without applying them; preserve all IDs/counters.
                batch.completed_rows=0
                batch.import_options={**(batch.import_options or {}),'cursor_version':2}
            # Cursor-derived jobs persist without a second queue/table or row copy.
            job_end = min(batch.rows, ((batch.completed_rows//JOB_ROWS)+1)*JOB_ROWS)
            while True:
                if not _material_lock(db):
                    db.rollback(); break
                batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id).with_for_update())
                db.refresh(batch)
                if batch.status not in {'queued','processing'} or batch.stage!='apply':
                    db.rollback(); break
                if batch.completed_rows >= job_end:
                    db.rollback(); break
                pending=list(db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id)
                    .order_by(ImportedRow.row_number,ImportedRow.id).offset(batch.completed_rows)
                    .limit(min(size,job_end-batch.completed_rows))))
                identity_map=_materials_by_identity(db,_row_identities(pending),[row.raw.get('_name','') for row in pending])
                codes={r.raw.get('_source_code') for r in pending if r.raw.get('_source_code')}
                owners={}
                for m in db.scalars(select(Material).where(or_(Material.source_sn.in_(codes),Material.source_material_code.in_(codes)))):
                    for code in (m.source_sn,m.source_material_code):
                        if code:owners.setdefault(code,set()).add(m.id)
                category_cache,unit_cache=_preload_maps(db)
                apply_started=perf_counter()
                for row in pending:
                    if row.status in {'invalid','duplicate','conflict','created','updated','unchanged'}:continue
                    try:
                        with db.begin_nested():
                            expected=identity_map.get(row.raw.get('_identity'))
                            owned=owners.get(row.raw.get('_source_code'),set())
                            if owned and (not expected or owned!={expected.id}):
                                raise HTTPException(409,'Source code changed since preview; review required')
                            result,made=_apply_row(db,row,identity_map,category_cache,unit_cache,batch.id)
                            db.flush()
                        if row.raw.get('_source_code') and identity_map.get(row.raw.get('_identity')):
                            owners.setdefault(row.raw['_source_code'],set()).add(identity_map[row.raw['_identity']].id)
                        batch.created+=result=='created';batch.updated_count+=result=='updated'
                        batch.unchanged_count+=result=='unchanged';batch.categories_created+=made
                    except (HTTPException,IntegrityError) as exc:
                        row.status='conflict' if isinstance(exc,IntegrityError) or exc.status_code==409 else 'invalid'
                        row.issue=(str(exc.detail) if isinstance(exc,HTTPException) else 'Concurrent identity conflict; review or retry this row.')[:300]
                        batch.errors+=1
                        identity_map=_materials_by_identity(db,_row_identities(pending),[r.raw.get('_name','') for r in pending])
                        category_cache,unit_cache=_preload_maps(db)
                batch.completed_rows+=len(pending)
                batch.apply_duration_ms+=int((perf_counter()-apply_started)*1000)
                batch.updated_at=datetime.now(UTC);batch.retry_count=0;batch.failure_reason=None
                if batch.completed_rows>=batch.rows or not pending:
                    batch.status='completed_with_errors' if batch.errors else 'completed';batch.stage='complete'
                    audit(db,db.get(User,batch.initiated_by),'material_import.confirmed',entity_id=batch.id,
                          rows=batch.rows,created=batch.created,updated=batch.updated_count,errors=batch.errors)
                else: batch.status='queued'
                db.commit()
                # Each committed page becomes searchable immediately.
                catalog_cache.invalidate()
                if batch.stage=='complete':
                    completed.append(batch.id)
                    break
        except Exception as exc:
            db.rollback()
            if batch_id:
                batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id).with_for_update())
                if batch and batch.status not in {'completed','completed_with_errors'}:
                    batch.retry_count+=1
                    transient=isinstance(exc,(DBAPIError,OSError))
                    batch.status=('queued' if stage=='apply' else 'uploaded') if transient and batch.retry_count<3 else 'failed'
                    batch.stage=stage;batch.failure_reason=str(exc.detail)[:500] if isinstance(exc,HTTPException) else f'Material import {stage} failed ({type(exc).__name__}). Saved batches are preserved.'
                    batch.updated_at=datetime.now(UTC);db.commit()
            logging.getLogger(__name__).warning('Material import failed batch=%s stage=%s type=%s',batch_id,stage,type(exc).__name__)
    cleanup_material_import_sources(db)
    return completed


def cleanup_material_import_sources(db:Session,retention_days:int=7)->int:
    cutoff=datetime.now(UTC)-timedelta(days=max(1,retention_days))
    old=db.scalars(select(ImportBatch).where(ImportBatch.kind=='materials',ImportBatch.source_storage_key.is_not(None),ImportBatch.updated_at<cutoff,ImportBatch.status.not_in({'queued','processing','uploaded'}))).all()
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


TEMPLATE_HEADERS = ['SN','Main Category','Sub Category','Description/Pacification','UNIT','Expenditures']


def _xlsx_download(book, filename):
    output=BytesIO();book.save(output);output.seek(0)
    return StreamingResponse(output,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':f'attachment; filename="{filename}"'})


@router.get('/template')
def material_category_template(user:User=Depends(admin_user)):
    """Civil Coding-compatible six-column workbook; examples are never imported."""
    book=Workbook();sheet=book.active;sheet.title='Civil Coding';sheet.append(TEMPLATE_HEADERS)
    sheet.freeze_panes='A2';sheet.auto_filter.ref='A1:F1'
    example=book.create_sheet('Example (reference)');example.append(TEMPLATE_HEADERS)
    for number,size in enumerate((10,12,14),1):
        example.append([number,'Reinforcement & Prestressing','Reinforcing Steel',f'Rebar Deformed ASTM A615 Gr60 diameter {size}mm 12m','KG','03-20'])
    instructions=book.create_sheet('Instructions')
    for message in [
        'Fill the Civil Coding worksheet, one material per row. Keep the six column headers unchanged.',
        'SN is an optional source material identifier. Use a stable unique code or leave blank; do not reuse it for another material.',
        'Main Category, Description/Pacification and UNIT are required. Sub Category belongs to its main category.',
        'Description/Pacification must include size/specification. Different sizes are distinct materials.',
        'Expenditures is an optional code, preserved as text (including leading zeroes). Format codes as Text in Excel.',
        'The Example and Instructions sheets are reference only; they are excluded from import.',
        'Upload once, preview, then confirm. Jobs contain at most 300 rows; each 30-row batch commits progress.',
        'Duplicate rows are reported. Ambiguous identities or conflicting classifications require review and are never overwritten.',
        'Existing IDs and RFQ/supplier references are preserved. Closing or refreshing does not cancel confirmed work.'
    ]:instructions.append([message])
    for page in (sheet,example):
        for column,width in zip('ABCDEF',(22,42,42,90,16,25)):page.column_dimensions[column].width=width
        for row in page.iter_rows(min_row=2):
            row[0].number_format='@';row[5].number_format='@'
    instructions.column_dimensions['A'].width=120
    return _xlsx_download(book,'civil-materials-template.xlsx')


@router.get('/recent',response_model=list[MaterialImportPreview])
def recent_material_imports(user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Recover the current admin's latest imports after refresh, with no storage paths."""
    batches=db.scalars(select(ImportBatch).where(ImportBatch.kind=='materials',ImportBatch.initiated_by==user.id)
        .order_by(ImportBatch.created_at.desc(),ImportBatch.id).limit(10)).all()
    return [_preview_view(batch,[]) for batch in batches]


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
    if suffix not in {'.xlsx', '.xls', '.csv'}:
        raise HTTPException(415, 'Upload an .xlsx, .xls, or UTF-8 .csv file')
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
    if suffix == '.xls' and not content_prefix.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        storage.delete(key)
        raise HTTPException(422, 'The uploaded XLS file is not a valid legacy workbook')
    if suffix == '.csv':
        try:
            storage.read(key).decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            storage.delete(key)
            raise HTTPException(422, 'CSV must use UTF-8 encoding') from exc
    # Serialize identical-file recovery across concurrent upload requests.
    if db.bind.dialect.name=='postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':717341394})
    existing=db.scalar(select(ImportBatch).where(ImportBatch.kind=='materials',ImportBatch.initiated_by==user.id,
        ImportBatch.source_sha256==digest.hex(),ImportBatch.status.in_({'uploaded','processing','preview','queued'}))
        .order_by(ImportBatch.created_at.desc()).limit(1))
    if existing:
        storage.delete(key)
        result=_preview_view(existing,[])
        db.commit()
        return result
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
    if batch.status not in {'needs_review', 'needs_discipline', 'needs_sheet_selection', 'failed', 'preview'} or not batch.source_storage_key:
        raise HTTPException(409, 'This workbook is not waiting for preview changes')
    if batch.stage=='apply' or batch.created or batch.updated_count or batch.unchanged_count:
        raise HTTPException(409, 'Confirmed imports must be resumed with retry, never reparsed')
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
    batch.import_options={**(batch.import_options or {}),'cursor_version':2}
    batch.status = 'queued'; batch.stage = 'apply'; batch.completed_rows = 0; batch.retry_count=0; batch.failure_reason=None; batch.updated_at = datetime.now(UTC)
    audit(db, user, 'material_import.queued', entity_id=batch.id, rows=batch.rows)
    db.commit()
    return {'batch_id': batch.id, 'status': 'queued', 'rows': batch.rows, 'created': 0,
            'updated': 0, 'unchanged': 0, 'errors': batch.errors, 'categories_created': 0}


@router.post('/{batch_id}/retry',response_model=MaterialImportPreview,status_code=202)
def retry_material_import(batch_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """Resume a failed job from its last committed cursor, without reupload/reparse."""
    batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id,ImportBatch.kind=='materials').with_for_update())
    if not batch:raise HTTPException(404,'Material import not found')
    if batch.initiated_by!=user.id:raise HTTPException(403,'Only the uploading administrator may retry this import')
    if batch.status!='failed':raise HTTPException(409,'Only failed imports can be retried')
    batch.status='queued' if batch.stage=='apply' else 'uploaded'
    batch.retry_count=0;batch.failure_reason=None;batch.updated_at=datetime.now(UTC)
    audit(db,user,'material_import.retry',entity_id=batch.id,completed_rows=batch.completed_rows)
    db.commit()
    return _preview_view(batch,[])


@router.get('/{batch_id}/errors.xlsx')
def material_failed_rows(batch_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    """All invalid, duplicate and conflict rows, escaped against Excel formula execution."""
    batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id,ImportBatch.kind=='materials'))
    if not batch:raise HTTPException(404,'Material import not found')
    book=Workbook(write_only=True);page=book.create_sheet('Failed Materials')
    page.append([*TEMPLATE_HEADERS,'Source Sheet','Source Row','Reason'])
    def safe(value):
        value=str(value or '')
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id,
        ImportedRow.status.in_({'invalid','duplicate','conflict'})).order_by(ImportedRow.row_number,ImportedRow.id))
    for item in rows:
        raw=item.raw;mapping=raw.get('_mapping',{})
        fields=(('_source_code','source_code'),('_category','main_category'),('_subcategory','subcategory'),
                ('_name','material_name'),('_source_unit','unit'),('_expenditure_code','expenditure_code'))
        page.append([*[safe(raw.get(key) or _row_value(raw,mapping,field)) for key,field in fields],
                     safe(raw.get('_sheet')),item.row_number,safe(item.issue)])
    return _xlsx_download(book,f'material-import-{batch.id}-failed.xlsx')


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
    catalog_cache.set('material-search', params, response, ttl=300, epoch=cache_epoch)
    return response
