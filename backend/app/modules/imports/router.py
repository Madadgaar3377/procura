from app.search import ListSearch
import csv
import json
import re
from collections.abc import Sequence
from io import BytesIO, StringIO
from typing import Any
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import catalog_cache
from app.database import get_db
from app.models import (
    Category,
    Contact,
    ImportBatch,
    ImportedRow,
    ImportedSupplierTrade,
    Material,
    Organization,
    User,
)
from app.response_contracts import ImportBatchOut, SupplierImportConfirmation
from app.security import admin_user, audit
from app.serializers import record

router=APIRouter(prefix='/imports',tags=['Supplier & Material Imports'])
from app.modules.imports.materials import router as material_imports

router.include_router(material_imports)

ALIASES={
    'supplier_name':{'suppliers name','supplier name','company name','company','supplier','legal name'},
    'trade':{'trade / products','trade/products','trade/ products','trade','services','material'},
    'contact_name':{'contact person','contact','representative'},
    'phone':{'mobile / phone','mobile/phone','mobile','phone','telephone'},
    'email':{'email','email address','e-mail'},
    'website':{'website','web site'},
    'city':{'location','city','region'},
    'type':{'supplier / manufacturer','supplier/manufacturer','type'},
    'brands':{'brands','brand'},
    'material_code':{'material code','code','item code'},
    'material_name':{'material name','name','description'},
    'category_code':{'category code','category'},
    'unit':{'unit','uom'},
}

def cleaned(value): return str(value).strip() if value is not None else ''
def normalized_header(value):
    return re.sub(r'\s*/\s*','/',re.sub(r'\s+',' ',cleaned(value).casefold()))

def _find_header(sheet, kind: str, header_hints: tuple[str,...]):
    for header_row, header_cells in enumerate(sheet.iter_rows(min_row=1,max_row=15,max_col=120,values_only=True),start=1):
        headers=[normalized_header(v) for v in header_cells]
        if kind=='suppliers':
            valid=any(header in ALIASES['supplier_name'] for header in headers)
        else:
            valid='material code' in headers and any(header in ALIASES['material_name'] for header in headers)
        if header_hints and any(hint in headers for hint in header_hints):
            valid=True
        if valid:
            return header_row,headers
    return None,None


def extract(content:bytes,filename:str,kind:str,header_hints:tuple[str,...]=(),sheet_name:str|None=None):
    if filename.lower().endswith('.xlsx'):
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries=archive.infolist()
                if len(entries)>3000 or sum(entry.file_size for entry in entries)>128_000_000 or any(entry.file_size>64_000_000 for entry in entries):
                    raise HTTPException(413,'Workbook expands beyond the safe parsing limit')
        except BadZipFile as exc:
            raise HTTPException(422,'The uploaded XLSX file is not a valid workbook') from exc
        try:
            wb=load_workbook(BytesIO(content),read_only=True,data_only=True,keep_links=False)
        except Exception as exc:
            raise HTTPException(422,'The uploaded XLSX workbook could not be read') from exc
        try:
            candidates=[]
            for sheet in wb.worksheets:
                if sheet.max_column>120:
                    raise HTTPException(413,f'Worksheet {sheet.title} exceeds the 120-column limit')
                header_row,headers=_find_header(sheet,kind,header_hints)
                reference=bool(re.search(r'(^|[^a-z])(reference|suggested|lookup|legend|expenditure)([^a-z]|$)',sheet.title.casefold()))
                is_eligible=header_row is not None and not reference
                candidates.append({'name':sheet.title,'eligible':is_eligible,'header_row':header_row,
                                   'row_count':max(0,(sheet.max_row or 0)-(header_row or 0)),
                                   'headers':[h for h in (headers or []) if h],
                                   'reason':'Reference worksheets are excluded.' if reference else None,
                                   '_sheet':sheet,'_headers':headers})
            eligible=[candidate for candidate in candidates if candidate['eligible']]
            if sheet_name:
                selected=next((candidate for candidate in eligible if candidate['name']==sheet_name),None)
                if selected is None:
                    raise HTTPException(422,'Selected worksheet does not contain a supported import header')
            elif len(eligible)==1:
                selected=eligible[0]
            elif len(eligible)>1:
                return [],[{key:value for key,value in candidate.items() if not key.startswith('_')} for candidate in candidates]
            else:
                raise HTTPException(422,'Could not find a supported supplier/material header row in the first 15 rows')
            header_row=selected['_headers']
            start=selected['header_row']+1
            if (selected['_sheet'].max_row or 0)-selected['header_row']>30000:
                raise HTTPException(413,'Worksheet exceeds the 30,000-row import limit')
            rows=[]
            for cells in selected['_sheet'].iter_rows(min_row=start,max_col=120,values_only=True):
                row={header:cleaned(value) for header,value in zip(header_row,cells) if header}
                if any(row.values()): rows.append(row)
            return rows,[{key:value for key,value in candidate.items() if not key.startswith('_')} for candidate in candidates]
        finally:
            wb.close()
    if filename.lower().endswith('.csv'):
        try: text=content.decode('utf-8-sig')
        except UnicodeDecodeError as exc: raise HTTPException(422,'CSV must use UTF-8 encoding') from exc
        reader=csv.DictReader(StringIO(text))
        headers=[normalized_header(value) for value in (reader.fieldnames or [])]
        if not _find_csv_header(headers,kind,header_hints):
            raise HTTPException(422,'CSV does not contain the required import headers')
        rows=[]
        for index,row in enumerate(reader,start=2):
            if index>30001: raise HTTPException(413,'CSV exceeds the 30,000-row import limit')
            cleaned_row={normalized_header(k):cleaned(v) for k,v in row.items() if k}
            if any(cleaned_row.values()): rows.append(cleaned_row)
        return rows,[{'name':'CSV','eligible':True,'header_row':1,'row_count':len(rows),'headers':headers}]
    raise HTTPException(415,'Upload .xlsx or .csv')


def _find_csv_header(headers:list[str],kind:str,header_hints:tuple[str,...])->bool:
    if kind=='suppliers':
        valid=any(header in ALIASES['supplier_name'] for header in headers)
    else:
        valid='material code' in headers and any(header in ALIASES['material_name'] for header in headers)
    return valid or bool(header_hints and any(hint in headers for hint in header_hints))

def field(row,name):
    for a in ALIASES[name]:
        if row.get(a): return row[a]
    return ''

def supplier_norm(value):
    return ' '.join(cleaned(value).casefold().split())

def supplier_trade_key(row:dict)->tuple[str,...]:
    return tuple(supplier_norm(field(row,key)) for key in ('trade','brands','type','city'))

def supplier_contact_key(row:dict)->tuple[str,str]|None:
    email=supplier_norm(field(row,'email'))
    phone=re.sub(r'[^0-9]','',field(row,'phone'))
    name=supplier_norm(field(row,'contact_name'))
    return ('email',email) if email else ('phone',phone) if phone else ('name',name) if name else None

def supplier_row_key(row:dict)->tuple[Any,...]:
    return (supplier_norm(field(row,'supplier_name')),supplier_trade_key(row),supplier_contact_key(row),
            supplier_norm(field(row,'website')))


def _apply_supplier_rows(db:Session,user:User,batch:ImportBatch,rows:Sequence[ImportedRow])->tuple[int,int,int]:
    """Apply validated, persisted supplier rows without reparsing the uploaded workbook."""
    names={supplier_norm(field(item.raw,'supplier_name')) for item in rows
           if item.status=='valid' and field(item.raw,'supplier_name')}
    companies=db.scalars(select(Organization).where(Organization.source=='imported',
                                  Organization.name_normalized.in_(names))).all() if names else []
    company_by_name={company.name_normalized:company for company in companies}
    org_ids=[company.id for company in companies]
    trade_keys:dict[str,set[tuple[str,...]]]={}
    contact_keys:dict[str,set[tuple[str,str]]]={}
    if org_ids:
        for trade in db.scalars(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id.in_(org_ids))).all():
            trade_keys.setdefault(trade.org_id,set()).add(tuple(supplier_norm(v) for v in
                (trade.raw_trade,trade.raw_brands,trade.raw_type,trade.raw_location)))
        for contact in db.scalars(select(Contact).where(Contact.org_id.in_(org_ids))).all():
            key=('email',supplier_norm(contact.email)) if contact.email else ('phone',re.sub(r'[^0-9]','',contact.phone or '')) if contact.phone else ('name',supplier_norm(contact.name)) if contact.name else None
            if key:contact_keys.setdefault(contact.org_id,set()).add(key)
    created=unchanged=errors=0
    batch.status='processing'
    try:
        for item in rows:
            if item.status!='valid':
                if item.status=='invalid':errors+=1
                continue
            row=item.raw
            name=field(row,'supplier_name')
            if not name:
                item.status='invalid';item.issue='Missing supplier name';errors+=1;continue
            n=supplier_norm(name)
            company=company_by_name.get(n)
            if not company:
                company=Organization(name=name,name_normalized=n,city=field(row,'city') or None,
                    website=field(row,'website') or None,source='imported',is_supplier=True,is_buyer=False)
                db.add(company);db.flush();company_by_name[n]=company;created+=1
            made_any=False
            trade_key=supplier_trade_key(row)
            known_trades=trade_keys.setdefault(company.id,set())
            if trade_key not in known_trades:
                db.add(ImportedSupplierTrade(org_id=company.id,import_batch_id=batch.id,raw_trade=field(row,'trade'),
                    raw_brands=field(row,'brands'),raw_type=field(row,'type'),raw_location=field(row,'city')))
                known_trades.add(trade_key);made_any=True
            contact_key=supplier_contact_key(row)
            if contact_key:
                known_contacts=contact_keys.setdefault(company.id,set())
                if contact_key not in known_contacts:
                    db.add(Contact(org_id=company.id,name=field(row,'contact_name') or None,
                        phone=field(row,'phone') or None,email=field(row,'email') or None,permission_to_notify=False))
                    known_contacts.add(contact_key);made_any=True
            if not made_any:unchanged+=1
            item.status='created' if made_any else 'unchanged'
        batch.created=created;batch.unchanged_count=unchanged;batch.errors=errors;batch.status='completed'
        audit(db,user,'import.completed',entity_id=batch.id,kind='suppliers',rows=batch.rows,created=created)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return created,unchanged,errors

@router.post('/{kind}',status_code=201)
def upload(kind:str,file:UploadFile=File(...),dry_run:bool=True,column_mapping:str|None=None,sheet_name:str|None=Form(default=None),user:User=Depends(admin_user),db:Session=Depends(get_db)):
    if kind not in {'suppliers','materials'}:raise HTTPException(422,'Import type must be suppliers or materials')
    raw=file.file.read(8_000_001)
    if len(raw)>8_000_000:raise HTTPException(413,'Max import size is 8 MB')
    mapping={}
    if column_mapping:
        if len(column_mapping)>4000:raise HTTPException(422,'Column mapping is too large')
        try:
            mapping=json.loads(column_mapping)
            if not isinstance(mapping,dict) or any(k not in ALIASES or not isinstance(v,str) for k,v in mapping.items()):raise ValueError()
        except (ValueError,TypeError):raise HTTPException(422,'Column mapping must map supported fields to header names')
    rows,sheets=extract(raw,file.filename or '',kind,tuple(normalized_header(v) for v in mapping.values()),sheet_name)
    eligible_sheets=[sheet for sheet in sheets if sheet.get('eligible')]
    if not sheet_name and len(eligible_sheets)>1:
        return {'dry_run':True,'status':'needs_sheet_selection','rows':0,'created':0,'errors':0,
                'preview':[],'issues':[],'warnings':[],'headers':[],'sheets':sheets}
    if not rows:raise HTTPException(422,'Spreadsheet is empty')
    source_headers=list(rows[0].keys())
    if any(normalized_header(header) not in source_headers for header in mapping.values()):
        raise HTTPException(422,'Column mapping references a header that is not present in the selected worksheet')
    detected_mapping={field_name:next((header for header in source_headers if header in aliases),'')
                      for field_name,aliases in ALIASES.items()}
    if mapping:
        for row in rows:
            for key,header in mapping.items():
                value=row.get(normalized_header(header),'')
                for alias in ALIASES[key]:row[alias]=value
            
        detected_mapping.update({key:normalized_header(header) for key,header in mapping.items()})
    preview:list[dict[str,Any]]=[];issues:list[dict[str,Any]]=[];warnings:list[dict[str,Any]]=[];created=0;unchanged=0;valid_count=0
    seen=set();seen_supplier_rows=set()
    staged_supplier_preview=kind=='suppliers' and dry_run
    batch=ImportBatch(kind=kind,filename=(file.filename or '')[:200],rows=len(rows),initiated_by=user.id,
                      status='preview' if staged_supplier_preview else 'completed')
    if not dry_run or staged_supplier_preview:db.add(batch);db.flush()
    supplier_by_name:dict[str,Organization]={}
    trade_keys_by_org:dict[str,set[tuple[str,...]]]={}
    contact_keys_by_org:dict[str,set[tuple[str,str]]]={}
    if kind=='suppliers':
        supplier_names={supplier_norm(field(row,'supplier_name')) for row in rows if field(row,'supplier_name')}
        if supplier_names:
            companies=db.scalars(select(Organization).where(Organization.source=='imported',Organization.name_normalized.in_(supplier_names))).all()
            supplier_by_name={company.name_normalized:company for company in companies}
            org_ids=[company.id for company in companies]
            if org_ids:
                for trade in db.scalars(select(ImportedSupplierTrade).where(ImportedSupplierTrade.org_id.in_(org_ids))).all():
                    trade_keys_by_org.setdefault(trade.org_id,set()).add(tuple(supplier_norm(v) for v in (trade.raw_trade,trade.raw_brands,trade.raw_type,trade.raw_location)))
                for contact in db.scalars(select(Contact).where(Contact.org_id.in_(org_ids))).all():
                    key=('email',supplier_norm(contact.email)) if contact.email else ('phone',re.sub(r'[^0-9]','',contact.phone or '')) if contact.phone else ('name',supplier_norm(contact.name)) if contact.name else None
                    if key: contact_keys_by_org.setdefault(contact.org_id,set()).add(key)
    for index,row in enumerate(rows,2):
        name=field(row,'supplier_name') if kind=='suppliers' else field(row,'material_name')
        code=field(row,'material_code') if kind=='materials' else ''
        status='valid';reason=''
        if not name:status='invalid';reason='Missing supplier/material name'
        if kind=='materials' and not code:status='invalid';reason='Missing material code'
        if kind=='materials' and not field(row,'category_code'):status='invalid';reason='Missing category code'
        if kind=='suppliers' and name:
            name_key=supplier_norm(name);row_key=supplier_row_key(row)
            if row_key in seen_supplier_rows:
                status='duplicate';reason='Exact duplicate supplier row in this workbook; it will be skipped.'
            seen_supplier_rows.add(row_key)
            if name_key in supplier_by_name or name_key in seen:
                warnings.append({'row':index,'reason':'Supplier company already exists or is repeated; new capability/contact details are deduplicated.'})
            seen.add(name_key)
            phone=field(row,'phone');email=field(row,'email')
            if phone and not re.fullmatch(r'\+?[0-9]{8,15}',re.sub(r'[\s()\-]','',phone)):
                status='invalid';reason='Invalid contact phone'
            if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
                status='invalid';reason='Invalid contact email'
        if kind=='materials' and status=='valid':
            if not db.scalar(select(Category.id).where(Category.code==field(row,'category_code').upper())):
                status='invalid';reason='Category code not found'
            elif db.scalar(select(Material.id).where(Material.code==code.upper())) or code.upper() in seen:
                warnings.append({'row':index,'reason':'Duplicate material code: existing material will be preserved'})
            seen.add(code.upper())
        if status=='valid' and not dry_run:
            if kind=='suppliers':
                n=supplier_norm(name)
                company=supplier_by_name.get(n)
                made_any=False
                if not company:
                    company=Organization(name=name,name_normalized=n,city=field(row,'city') or None,website=field(row,'website') or None,source='imported',is_supplier=True,is_buyer=False)
                    db.add(company);db.flush();supplier_by_name[n]=company;created+=1
                org_trade_keys=trade_keys_by_org.setdefault(company.id,set())
                trade_key=supplier_trade_key(row)
                if trade_key not in org_trade_keys:
                    db.add(ImportedSupplierTrade(org_id=company.id,import_batch_id=batch.id,raw_trade=field(row,'trade'),raw_brands=field(row,'brands'),raw_type=field(row,'type'),raw_location=field(row,'city')))
                    org_trade_keys.add(trade_key);made_any=True
                contact_key=supplier_contact_key(row)
                if contact_key:
                    org_contact_keys=contact_keys_by_org.setdefault(company.id,set())
                    if contact_key not in org_contact_keys:
                        db.add(Contact(org_id=company.id,name=field(row,'contact_name') or None,phone=field(row,'phone') or None,email=field(row,'email') or None,permission_to_notify=False))
                        org_contact_keys.add(contact_key);made_any=True
                if not made_any: unchanged+=1
            else:
                cat=db.scalar(select(Category).where(Category.code==field(row,'category_code').upper()))
                if not cat:status='invalid';reason='Category code not found'
                elif not db.scalar(select(Material.id).where(Material.code==code.upper())):
                    db.add(Material(code=code.upper(),category_id=cat.id,name_en=name,unit=field(row,'unit') or 'No.'))
                    created+=1
        if not dry_run or staged_supplier_preview:
            db.add(ImportedRow(batch_id=batch.id,row_number=index,raw=row,status=status,issue=reason or None))
        if status=='invalid':issues.append({'row':index,'reason':reason})
        if status=='valid':valid_count+=1
        if len(preview)<10:preview.append({'row':index,'status':status,'name':name,'issue':reason})
    if staged_supplier_preview:
        batch.errors=len(issues)
        audit(db,user,'supplier_import.previewed',entity_id=batch.id,rows=len(rows),errors=len(issues))
        db.commit()
    elif not dry_run:
        batch.created=created;batch.errors=len(issues);batch.unchanged_count=unchanged
        audit(db,user,'import.completed',entity_id=batch.id,kind=kind,rows=len(rows),created=created)
        db.commit()
        if kind=='materials' and created:
            catalog_cache.invalidate()
    return {'dry_run':dry_run,'status':'preview' if dry_run else 'completed','batch_id':batch.id if staged_supplier_preview or not dry_run else None,
            'rows':len(rows),'valid':valid_count,
            'created':created,'unchanged':unchanged,'errors':len(issues),'preview':preview,'issues':issues[:100],
            'warnings':warnings[:100],'headers':source_headers,'mapping':{key:value for key,value in detected_mapping.items() if value},'sheets':sheets,
            'selected_sheet':sheet_name or (eligible_sheets[0]['name'] if eligible_sheets else None)}

@router.get('/batches',response_model=list[ImportBatchOut])
def batches(search:ListSearch=Depends(),user:User=Depends(admin_user),db:Session=Depends(get_db)):
    return [record(x) for x in db.scalars(search.apply(select(ImportBatch),ImportBatch.filename,ImportBatch.kind,ImportBatch.status,order=(ImportBatch.created_at.desc(),ImportBatch.id))).all()]


@router.post('/suppliers/{batch_id}/confirm',status_code=200,response_model=SupplierImportConfirmation)
def confirm_supplier_import(batch_id:str,user:User=Depends(admin_user),db:Session=Depends(get_db)):
    batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id,ImportBatch.kind=='suppliers').with_for_update())
    if not batch:raise HTTPException(404,'Supplier import preview not found')
    if batch.initiated_by!=user.id:raise HTTPException(403,'Only the administrator who created this preview may confirm it')
    if batch.status!='preview':raise HTTPException(409,'This supplier import preview has already been processed')
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id).order_by(ImportedRow.row_number)).all()
    created,unchanged,errors=_apply_supplier_rows(db,user,batch,rows)
    return {'batch_id':batch.id,'status':batch.status,'rows':batch.rows,'created':created,
            'unchanged':unchanged,'errors':errors}
