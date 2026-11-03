"""Private, durable supplier rate imports using existing import/outbox tables."""
import csv
from io import BytesIO,StringIO
from itertools import islice
from zipfile import ZipFile
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,File,UploadFile,HTTPException,Query
from sqlalchemy import select,or_,func
from sqlalchemy.orm import Session
from app.database import get_db
from app.import_storage import get_import_storage
from app.models import ImportBatch,ImportedRow,Material,Category,PriceBookItem,User,Outbox
from app.schemas import PriceIn,RateImportMapping
from app.response_contracts import RateImportOut
from app.security import current_user,audit
from app.modules.pricebooks.service import supplier,validate_material,save_rate,utc
from app.modules.materials.service import normalize_text,normalize_unit

router=APIRouter()
MAX_ROWS=10000
MAX_COLUMNS=120
ALIASES={'main category':'main','main_category':'main','subcategory':'sub','sub category':'sub','description':'description','material description':'description','unit':'unit','unit price':'unit_price','unit_price':'unit_price','material_code':'code','material code':'code','brand':'brand','model':'model','lead time':'lead_days','lead_days':'lead_days','lead time (days)':'lead_days','price valid until':'valid_until','valid until':'valid_until','valid_until':'valid_until','price basis':'price_basis','price_basis':'price_basis','offered specifications':'offered_specs'}

def owned_batch(db,user,org_id,batch_id):
    supplier(db,user,org_id)
    batch=db.get(ImportBatch,batch_id)
    if not batch or batch.kind!='supplier_rates' or (batch.import_options or {}).get('org_id')!=org_id:raise HTTPException(404,'Import not found')
    return batch

def progress(db,batch,limit=25,offset=0):
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id).order_by(ImportedRow.row_number).limit(limit).offset(offset)).all()
    return {'batch_id':batch.id,'status':batch.status,'rows':batch.rows,'valid':max(0,batch.rows-batch.errors),'errors':batch.errors,'saved':batch.created+batch.updated_count,'completed_rows':batch.completed_rows,'failure_reason':batch.failure_reason,'items':[{'row':r.row_number,'description':str(r.raw.get('description') or r.raw.get('code') or ''),'unit':str(r.raw.get('unit') or ''),'unit_price':str(r.raw.get('unit_price') or ''),'material_id':r.raw.get('_material_id'),'action':r.raw.get('_action','create'),'status':r.status,'issue':r.issue} for r in rows],'limit':limit,'offset':offset}

@router.post('/{org_id}/bulk-import',response_model=RateImportOut)
def upload(org_id:str,file:UploadFile=File(...),dry_run:bool=True,user:User=Depends(current_user),db:Session=Depends(get_db)):
    supplier(db,user,org_id)
    if not dry_run:raise HTTPException(409,'Upload a preview and confirm its batch before saving rates')
    suffix='.csv' if (file.filename or '').lower().endswith('.csv') else '.xlsx' if (file.filename or '').lower().endswith('.xlsx') else None
    if not suffix:raise HTTPException(415,'CSV or XLSX required')
    batch=ImportBatch(kind='supplier_rates',filename=(file.filename or 'rates')[:200],status='uploaded',stage='parse',initiated_by=user.id,import_options={'org_id':org_id})
    db.add(batch);db.flush();key=f'{org_id}/{batch.id}{suffix}'
    try:size,digest=get_import_storage().write(key,file.file,8_000_000)
    except ValueError as exc:raise HTTPException(413,'Import too large') from exc
    batch.source_storage_key=key;batch.source_bytes=size;batch.source_sha256=digest.hex()
    audit(db,user,'price_book.import_uploaded',org_id,batch.id);db.commit();return progress(db,batch)

@router.get('/{org_id}/imports/{batch_id}',response_model=RateImportOut)
def status(org_id:str,batch_id:str,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),user:User=Depends(current_user),db:Session=Depends(get_db)):
    return progress(db,owned_batch(db,user,org_id,batch_id),limit,offset)

def row_payload(db,org_id,row,mapped=None):
    material=db.get(Material,mapped) if mapped else None
    if material is None:raise ValueError('Unresolved Material Master item. Choose an existing item; no master records will be created.')
    material,unit=validate_material(db,org_id,material.id,row.get('unit') or None)
    main=db.get(Category,material.main_category_id or material.category_id)
    sub=db.get(Category,material.subcategory_id or (material.category_id if main and main.parent_id else None))
    if main and main.parent_id:sub=main;main=db.get(Category,main.parent_id)
    if row.get('main') and normalize_text(row['main']) not in {normalize_text(main.name_en if main else ''),normalize_text(main.name_ar if main else '')}:raise ValueError('Mapped material is outside the row main category')
    if row.get('sub') and normalize_text(row['sub']) not in {normalize_text(sub.name_en if sub else ''),normalize_text(sub.name_ar if sub else '')}:raise ValueError('Mapped material is outside the row subcategory')
    expiry=row.get('valid_until') or None
    if isinstance(expiry,str):expiry=datetime.fromisoformat(expiry.replace('Z','+00:00'))
    if expiry and expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
    payload=PriceIn(org_id=org_id,material_id=material.id,unit=unit,unit_price=row.get('unit_price'),brand=str(row.get('brand') or ''),model=str(row.get('model') or ''),offered_specs=str(row.get('offered_specs') or ''),lead_days=row.get('lead_days') if row.get('lead_days') not in (None,'') else 7,valid_until=expiry,price_basis=row.get('price_basis') or 'supply')
    if expiry and utc(expiry)<=datetime.now(timezone.utc):raise ValueError('Expired rate')
    return payload

@router.put('/{org_id}/imports/{batch_id}/rows/{row_number}',response_model=RateImportOut)
def map_row(org_id:str,batch_id:str,row_number:int,payload:RateImportMapping,user:User=Depends(current_user),db:Session=Depends(get_db)):
    batch=owned_batch(db,user,org_id,batch_id)
    if batch.status!='preview':raise HTTPException(409,'Only a preview can be mapped')
    row=db.scalar(select(ImportedRow).where(ImportedRow.batch_id==batch.id,ImportedRow.row_number==row_number).with_for_update())
    if not row:raise HTTPException(404,'Import row not found')
    try:rate=row_payload(db,org_id,row.raw,payload.material_id)
    except (ValueError,HTTPException) as exc:raise HTTPException(422,str(exc.detail) if isinstance(exc,HTTPException) else str(exc)) from exc
    old=row.status;row.raw={**row.raw,**preview_values(db,rate)};row.status='ready';row.issue=None
    if old=='invalid':batch.errors=max(0,batch.errors-1)
    db.commit();return progress(db,batch)

def preview_values(db,payload):
    cached=db.info.get(('rate_preview',payload.org_id))
    existing=cached.get((payload.material_id,payload.price_basis)) if cached is not None else db.scalar(select(PriceBookItem).where(PriceBookItem.org_id==payload.org_id,PriceBookItem.material_id==payload.material_id,PriceBookItem.price_basis==payload.price_basis,PriceBookItem.active.is_(True)))
    return {'_material_id':payload.material_id,'_action':'update' if existing else 'create','_rate_id':existing.id if existing else None,'_version':existing.version if existing else None,'_payload':payload.model_dump(mode='json')}

@router.post('/{org_id}/imports/{batch_id}/confirm',response_model=RateImportOut)
def confirm(org_id:str,batch_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    supplier(db,user,org_id,lock=True)
    batch=owned_batch(db,user,org_id,batch_id)
    db.refresh(batch,with_for_update=True)
    if batch.status in {'queued','applying','completed','completed_with_errors'}:return progress(db,batch)
    if batch.status!='preview' or not batch.rows or batch.errors:raise HTTPException(409,'Resolve all preview issues before confirming rates')
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id)).all();seen=set()
    for row in rows:
        key=(row.raw.get('_material_id'),row.raw.get('_payload',{}).get('price_basis'))
        if key in seen:raise HTTPException(409,'Duplicate material and price basis in this file. Upload a corrected file.')
        seen.add(key)
    batch.status='queued';batch.stage='apply'
    db.commit();return progress(db,batch)

def parse_rows(batch):
    data=get_import_storage().read(batch.source_storage_key)
    if batch.source_storage_key.endswith('.csv'):
        iterator=csv.reader(StringIO(data.decode('utf-8-sig')))
        headers=next(iterator,[])
        if not headers or len(headers)>MAX_COLUMNS:
            raise ValueError('Import exceeds the safe column limit')
        result=[]
        for row in islice(iterator,MAX_ROWS+1):
            if len(row)>MAX_COLUMNS or len(row)>len(headers):
                raise ValueError('Import row exceeds its header column limit')
            result.append(dict(zip(headers,row)))
    else:
        with ZipFile(BytesIO(data)) as archive:
            if len(archive.infolist())>500 or sum(item.file_size for item in archive.infolist())>64_000_000:raise ValueError('Workbook exceeds safe expanded size')
        from openpyxl import load_workbook
        book=load_workbook(BytesIO(data),read_only=True,data_only=False,keep_links=False)
        try:
            sheet=book.active
            if sheet is None or (sheet.max_column and sheet.max_column>MAX_COLUMNS):
                raise ValueError('Import exceeds the safe column limit')
            if sheet.max_row and sheet.max_row>MAX_ROWS+1:
                raise ValueError('Import exceeds the safe row limit')
            # Explicit iteration bounds also apply when dimensions are absent.
            iterator=sheet.iter_rows(max_col=MAX_COLUMNS,max_row=MAX_ROWS+2,values_only=True)
            headers=[str(v or '').strip() for v in next(iterator,[])]
            result=[dict(zip(headers,row)) for row in islice(iterator,MAX_ROWS+1) if any(v is not None for v in row)]
        finally:book.close()
    if not result or len(result)>MAX_ROWS:raise ValueError('Import must contain between 1 and 10000 rows')
    return [{ALIASES.get(str(k).strip().lower(),str(k).strip().lower()):(v.isoformat() if isinstance(v,datetime) else v) for k,v in row.items() if k is not None} for row in result]

def process_rate_import(db,event):
    batch=db.scalar(select(ImportBatch).where(ImportBatch.id==event.payload.get('batch_id'),ImportBatch.kind=='supplier_rates').with_for_update())
    if not batch:return
    user=db.get(User,batch.initiated_by);org_id=(batch.import_options or {}).get('org_id')
    if not user or not user.is_active:batch.status='failed';batch.failure_reason='Import owner is inactive';return
    try:supplier(db,user,org_id,lock=True)
    except HTTPException:batch.status='failed';batch.failure_reason='Import authorization is no longer valid';return
    if event.event=='pricebook.import.parse':
        if batch.status!='uploaded':return
        try:rows=parse_rows(batch)
        except Exception:batch.status='failed';batch.failure_reason='Could not read a safe CSV/XLSX file. Check its format and row limits.';return
        names={normalize_text(str(row.get('description') or '')) for row in rows};codes={str(row.get('code') or '').upper().strip() for row in rows}
        materials=db.scalars(select(Material).where(Material.active.is_(True),or_(Material.code.in_(codes),Material.normalized_description.in_(names),func.lower(Material.name_en).in_(names))).limit(50001)).all()
        if len(materials)>50000:batch.status='failed';batch.failure_reason='Too many ambiguous material matches. Include material codes.';return
        ids=[m.id for m in materials]
        db.info[('rate_preview',org_id)]={(r.material_id,r.price_basis):r for r in db.scalars(select(PriceBookItem).where(PriceBookItem.org_id==org_id,PriceBookItem.active.is_(True),PriceBookItem.material_id.in_(ids))).all()}
        db.info['pricing_categories']={c.id:c for c in db.scalars(select(Category)).all()}
        by_code={};by_name={}
        for material in materials:by_code.setdefault(material.code.upper(),[]).append(material);by_name.setdefault(normalize_text(material.name_en),[]).append(material)
        seen=set()
        for number,raw in enumerate(rows,2):
            row=ImportedRow(batch_id=batch.id,row_number=number,raw=raw,status='invalid');db.add(row)
            candidates=by_code.get(str(raw.get('code') or '').upper().strip(),[]) if raw.get('code') else by_name.get(normalize_text(str(raw.get('description') or '')),[])
            valid=[]
            for material in candidates:
                try:valid.append(row_payload(db,org_id,raw,material.id))
                except (ValueError,HTTPException):pass
            try:
                if len(valid)!=1:raise ValueError('Unresolved or ambiguous material, unit or approved capability. Map an existing item.')
                payload=valid[0];key=(payload.material_id,payload.price_basis)
                if key in seen:raise ValueError('Duplicate material and price basis in file')
                seen.add(key);row.raw={**raw,**preview_values(db,payload)};row.status='ready'
            except ValueError as exc:row.issue=str(exc);batch.errors+=1
        batch.rows=len(rows);batch.status='preview';batch.stage='preview';audit(db,user,'price_book.import_previewed',org_id,batch.id,rows=batch.rows,errors=batch.errors)
        return
    if batch.status not in {'queued','applying'}:return
    batch.status='applying'
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id,ImportedRow.status=='ready').order_by(ImportedRow.row_number).limit(250)).all()
    for row in rows:
        try:
            with db.begin_nested():
                payload=PriceIn.model_validate(row.raw['_payload']);existing=db.get(PriceBookItem,row.raw['_rate_id']) if row.raw.get('_rate_id') else None
                if row.raw.get('_rate_id') and not existing:raise ValueError('Rate changed after preview')
                save_rate(db,user,payload,existing,expected_version=row.raw.get('_version'))
            row.status='saved'
            if existing:batch.updated_count+=1
            else:batch.created+=1
        except (ValueError,HTTPException) as exc:row.status='invalid';row.issue=str(exc.detail) if isinstance(exc,HTTPException) else 'Rate changed after preview';batch.errors+=1
        batch.completed_rows+=1
    if db.scalar(select(ImportedRow.id).where(ImportedRow.batch_id==batch.id,ImportedRow.status=='ready')):
        batch.status='applying'
    else:batch.status='completed_with_errors' if batch.errors else 'completed';batch.stage='completed';audit(db,user,'price_book.import_completed',org_id,batch.id,saved=batch.created+batch.updated_count,errors=batch.errors)
