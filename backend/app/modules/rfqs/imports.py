"""Buyer RFQ staging using the existing durable import tables and private storage.

No master writes, RFQ publication, supplier matching, or notifications happen here.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import uuid
import unicodedata
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, or_, and_, text
from sqlalchemy.orm import Session
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from app.database import get_db
from app.import_storage import get_import_storage
from app.models import Category, ImportBatch, ImportedRow, Material, MeasurementUnit, Organization, RFQ, User
from app.security import current_user, org_member, audit, as_utc
from app.rate_limit import enforce_rate_limit
from app.schemas import RFQLineIn
from app.modules.imports.materials import _discover, _column_map, _header, _xlsx_download, FIELD_ALIASES
from app.modules.materials.service import normalize_text, UNIT_NAMES
from .material_matching import (
    attributes,
    canonical_unit,
    category_pair,
    classify_unmatched_batch,
    compatible,
    rerank,
    shortlist,
)
from .import_schemas import (RFQImportJob, RFQImportRow, RFQImportOptions, RFQImportEdit,
                             RFQImportRetry, RFQImportReview, RFQImportConfirmation)

router=APIRouter(prefix='/imports',tags=['Buyer RFQ Material Imports'])
KIND='rfq_materials'
MAX_BYTES=5_000_000
MAX_ROWS=300
BATCH_ROWS=30
HEADERS=['Main Category','Subcategory','Description / Specification','Unit','Quantity']


def buyer(db,user,org_id):
    org_member(db,user,org_id,{'owner','manager','buyer'})
    org=db.get(Organization,org_id)
    if not org or not org.is_active or not org.is_buyer:raise HTTPException(403,'An active buyer workspace is required')


def owned(db,user,batch_id,lock=False):
    statement=select(ImportBatch).where(ImportBatch.id==batch_id,ImportBatch.kind==KIND)
    batch=db.scalar(statement.with_for_update() if lock else statement)
    if not batch or batch.initiated_by!=user.id:raise HTTPException(404,'RFQ import not found')
    buyer(db,user,batch.import_options['org_id'])
    rfq_id=batch.import_options.get('rfq_id')
    if rfq_id:
        rfq=db.get(RFQ,rfq_id)
        if not rfq or rfq.buyer_org_id!=batch.import_options['org_id']:raise HTTPException(404,'RFQ draft not found')
        if rfq.status!='draft':raise HTTPException(409,'This RFQ is no longer a draft')
    return batch


def limit(request,user,scope='rfq-import-edit',count=600):
    enforce_rate_limit(request,scope=scope,identity=user.id,limit=count,window_seconds=60)


def row_view(row):
    return {'id':row.id,'row_number':row.row_number,'status':row.status,'issue':row.issue,**row.raw}


def view(db,batch,include_rows=True):
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id).order_by(ImportedRow.row_number,ImportedRow.id)).all()
    active=[r for r in rows if not r.raw.get('removed')]
    return {'id':batch.id,'status':batch.status,'stage':batch.stage,'total':len(rows) or batch.rows,
            'processed':sum(r.status not in {'queued','processing'} for r in rows),
            'matched':sum(r.status=='high_confidence' or bool(r.raw.get('material_id') and r.raw.get('accepted')) for r in active),
            'needs_review':sum(r.status=='needs_review' and not r.raw.get('accepted') for r in active),
            'unmatched':sum(r.status=='no_match' for r in active),
            'failed':sum(r.status=='failed' for r in active),
            'failure_reason':batch.failure_reason,'ai_enabled':batch.import_options.get('ai_enabled',True),
            'headers':(batch.preview_metadata or {}).get('headers',[]),
            'column_mapping':batch.import_options.get('column_mapping',{}),'rows':[row_view(r) for r in rows] if include_rows else []}


@router.get('/template',response_class=StreamingResponse)
def template(org_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    buyer(db,user,org_id)
    book=Workbook();sheet=book.active;sheet.title='Materials';sheet.append(HEADERS)
    examples=[['Plumbing','Pipes','Boosted Cold Water (BCW) Pipe Ø20','m',128.38],['Plumbing','Pipe Fittings','BCW Pipe Tee Ø32','nr',4],['Electrical','Cables','Copper Cable 4C x 16mm²','m',150],['Fire Fighting','Pipes','Fire Fighting Steel Pipe DN100','m',50]]
    for row in examples:sheet.append(row)
    sheet.freeze_panes='A2';sheet.auto_filter.ref='A1:E5'
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='2563EB')
    for column,width in zip('ABCDE',[28,28,75,14,18]):sheet.column_dimensions[column].width=width
    for row in sheet.iter_rows(min_row=2):row[4].number_format='0.000'
    instructions=book.create_sheet('Instructions')
    for value in ['Examples only: replace or delete the four example materials before uploading.','Description / Specification, Unit and Quantity are required. Main Category and Subcategory are optional.','Use actual approved Main Category and Subcategory labels when known. You can correct category suggestions in the preview.','Use positive quantities with up to three decimal places. Keep diameter, grade, standard and rating in the description.','Section headings, repeated headers and empty rows are skipped. No internal material IDs are required.','Limit: 300 materials, 5 MB, XLSX/XLS/UTF-8 CSV. No global materials are created. AI is optional; review every uncertain match.','Nothing is published or sent to suppliers until you finish the RFQ and publish it.']:
        instructions.append([value])
    instructions.append(['Existing approved category terminology (reference only):'])
    categories=db.scalars(select(Category).where(Category.active.is_(True)).order_by(Category.name_en)).all()
    by_id={c.id:c for c in categories}
    for c in categories:instructions.append([f'{by_id[c.parent_id].name_en} / {c.name_en}' if c.parent_id in by_id else c.name_en])
    instructions.column_dimensions['A'].width=120
    return _xlsx_download(book,'rfq-materials-template.xlsx')


@router.get('/recent',response_model=list[RFQImportJob])
def recent(org_id:str,draft_key:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    buyer(db,user,org_id)
    # JSON scope is checked in SQL as well as by every subsequent operation.
    batches=db.scalars(select(ImportBatch).where(ImportBatch.kind==KIND,ImportBatch.initiated_by==user.id,
        ImportBatch.import_options['org_id'].as_string()==org_id,ImportBatch.import_options['draft_key'].as_string()==draft_key)
        .order_by(ImportBatch.created_at.desc()).limit(5)).all()
    return [view(db,b) for b in batches]


@router.post('',response_model=RFQImportJob,status_code=202)
def upload_job(request:Request,file:UploadFile=File(...),org_id:str=Form(...),draft_key:str=Form(...),
               rfq_id:str|None=Form(None),ai_enabled:bool=Form(True),user:User=Depends(current_user),db:Session=Depends(get_db)):
    buyer(db,user,org_id);limit(request,user,'rfq-import-upload',6)
    if not 1<=len(draft_key)<=100:raise HTTPException(422,'Invalid draft key')
    if rfq_id:
        rfq=db.get(RFQ,rfq_id)
        if not rfq or rfq.buyer_org_id!=org_id:raise HTTPException(404,'RFQ draft not found')
        if rfq.status!='draft':raise HTTPException(409,'Only draft RFQs can import materials')
    suffix=Path(file.filename or '').suffix.casefold()
    if suffix not in {'.xlsx','.xls','.csv'}:raise HTTPException(415,'Choose an XLSX, XLS or UTF-8 CSV file')
    job_id=str(uuid.uuid4());key=f'rfqs/{org_id}/{job_id}/source{suffix}';storage=get_import_storage()
    try:size,digest=storage.write(key,file.file,MAX_BYTES)
    except ValueError:raise HTTPException(413,'Maximum RFQ workbook size is 5 MB')
    content=storage.read(key)
    try:
        if not size:raise HTTPException(422,'The file is empty')
        if suffix=='.xlsx' and not content.startswith(b'PK'):raise HTTPException(422,'The file is not an XLSX workbook')
        if suffix=='.xls' and not content.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):raise HTTPException(422,'The file is not an XLS workbook')
        if suffix=='.csv':
            try:content.decode('utf-8-sig')
            except UnicodeError:raise HTTPException(422,'CSV must use UTF-8 text')
            if b'\x00' in content:raise HTTPException(422,'CSV contains binary data')
            if content.count(b'\n')>2000:raise HTTPException(413,'The file exceeds the physical row safety limit')
        if db.get_bind().dialect.name=='postgresql':
            lock=int.from_bytes(sha256(f'{user.id}:{org_id}:{draft_key}'.encode()).digest()[:8],signed=True)
            db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
        existing=db.scalar(select(ImportBatch).where(ImportBatch.kind==KIND,ImportBatch.initiated_by==user.id,ImportBatch.source_sha256==digest.hex(),
            ImportBatch.import_options['org_id'].as_string()==org_id,ImportBatch.import_options['draft_key'].as_string()==draft_key,
            ImportBatch.import_options['ai_enabled'].as_boolean()==ai_enabled).order_by(ImportBatch.created_at.desc()).limit(1))
        if existing:storage.delete(key);return view(db,existing)
        batch=ImportBatch(id=job_id,kind=KIND,filename=Path(file.filename or 'materials').name[:200],status='uploaded',stage='parse',initiated_by=user.id,source_storage_key=key,source_bytes=size,source_sha256=digest.hex(),import_options={'org_id':org_id,'draft_key':draft_key,'rfq_id':rfq_id,'ai_enabled':ai_enabled,'column_mapping':{}})
        db.add(batch);db.commit();return view(db,batch)
    except Exception:
        db.rollback();storage.delete(key);raise


@router.get('/{batch_id}',response_model=RFQImportJob)
def get_job(batch_id:str,include_rows:bool=True,user:User=Depends(current_user),db:Session=Depends(get_db)):
    return view(db,owned(db,user,batch_id),include_rows)


def editable(batch):
    if batch.status not in {'preview','needs_mapping','failed'}:raise HTTPException(409,'Wait for processing to finish; confirmed imports cannot be edited')


@router.put('/{batch_id}/options',response_model=RFQImportJob,status_code=202)
def options(batch_id:str,payload:RFQImportOptions,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    limit(request,user);batch=owned(db,user,batch_id,True);editable(batch)
    if batch.stage!='parse':raise HTTPException(409,'Column mapping is available before matching starts')
    if any(key not in {'material_name','unit','quantity','main_category','subcategory','specifications'} or len(value)>200 for key,value in payload.column_mapping.items()):raise HTTPException(422,'Unsupported column mapping')
    batch.import_options={**batch.import_options,'column_mapping':payload.column_mapping};batch.status='uploaded';batch.failure_reason=None
    db.commit();return view(db,batch)


def check_lines(db,values):
    """Validate a whole preview using three bounded reference lookups, no writes."""
    categories={c.id:c for c in db.scalars(select(Category).where(Category.active.is_(True)))}
    materials={m.id:m for m in db.scalars(select(Material).where(Material.id.in_({v.get('material_id') for v in values}-{None})))}
    unit_rows=db.scalars(select(MeasurementUnit).where(MeasurementUnit.code.in_({v['unit'] for v in values}))).all()
    units={u.code for u in unit_rows if u.active}|(set(UNIT_NAMES)-{u.code for u in unit_rows if not u.active})
    for value in values:
        line=RFQLineIn.model_validate(value)
        if line.unit not in units:raise HTTPException(422,'Choose an active approved unit')
        main=categories.get(line.main_category_id);sub=categories.get(line.subcategory_id)
        if not main or main.parent_id or line.subcategory_id and (not sub or sub.parent_id!=main.id):raise HTTPException(422,'Choose an active category and its subcategory')
        if not sub and any(c.parent_id==main.id for c in categories.values()):raise HTTPException(422,'Choose a subcategory')
        if line.material_id:
            m=materials.get(line.material_id)
            if not m or not m.active or category_pair(m,categories)!=(main.id,sub.id if sub else None):raise HTTPException(422,'The selected master material is inactive or belongs to another category')
            if canonical_unit(m.unit)!=line.unit:raise HTTPException(422,'No approved unit conversion exists for this material')
            text=' '.join([m.name_en,m.name_ar or '',m.description or '',main.name_en,sub.name_en if sub else ''])
            if not compatible(line.description+' '+line.specifications,text):raise HTTPException(422,'The selected material has incompatible or unconfirmed technical attributes; keep this row custom or select another material')
            from .material_matching import technical_conflict
            if technical_conflict(line.description,m.name_en,line.specifications):raise HTTPException(422,'The selected material is technically different from the description (for example RGB vs daylight); keep this row custom or select another material')


@router.put('/{batch_id}/rows/{row_id}',response_model=RFQImportRow)
def edit_row(batch_id:str,row_id:str,payload:RFQImportEdit,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    limit(request,user);batch=owned(db,user,batch_id,True);editable(batch)
    row=db.scalar(select(ImportedRow).where(ImportedRow.id==row_id,ImportedRow.batch_id==batch.id).with_for_update())
    if not row:raise HTTPException(404,'Imported row not found')
    if row.raw.get('version',1)!=payload.expected_version:raise HTTPException(409,'This row changed; reload the preview')
    data=payload.model_dump(mode='json',exclude={'expected_version'});data['unit']=canonical_unit(data['unit'])
    check_lines(db,[data]);row.raw={**row.raw,**data,'attributes':attributes(data['description']+' '+data['specifications']),'version':payload.expected_version+1}
    row.status='needs_review' if payload.material_id else 'no_match';row.issue=None
    db.commit();return row_view(row)


@router.post('/{batch_id}/review',response_model=RFQImportJob)
def review(batch_id:str,payload:RFQImportReview,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    limit(request,user);batch=owned(db,user,batch_id,True);editable(batch)
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id)).all()
    chosen=[r for r in rows if r.id in payload.row_ids] if payload.row_ids else rows
    if payload.action=='accept_high_confidence':
        chosen=[r for r in chosen if r.status=='high_confidence' and not r.raw.get('removed')]
        check_lines(db,[r.raw for r in chosen])
    for row in chosen:
        row.raw={**row.raw,('removed' if payload.action=='remove' else 'accepted'):True,'version':row.raw.get('version',1)+1}
    db.commit();return view(db,batch)


@router.post('/{batch_id}/retry',response_model=RFQImportJob,status_code=202)
def retry(batch_id:str,payload:RFQImportRetry,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    limit(request,user,'rfq-import-retry',20);batch=owned(db,user,batch_id,True);editable(batch)
    if batch.stage=='parse':batch.status='uploaded'
    else:
        rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id)).all()
        for row in rows:
            if not row.raw.get('removed') and ((payload.row_ids and row.id in payload.row_ids) or (not payload.row_ids and row.status in {'failed','no_match','queued'})):
                row.status='queued';row.issue=None;row.raw={**row.raw,'accepted':False,'version':row.raw.get('version',1)+1}
        batch.status='queued'
    batch.failure_reason=None;db.commit();return view(db,batch)


@router.post('/{batch_id}/confirm',response_model=RFQImportConfirmation)
def confirm(batch_id:str,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    limit(request,user);batch=owned(db,user,batch_id,True)
    if batch.status not in {'preview','confirmed'}:raise HTTPException(409,'Wait for matching to finish')
    rows=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch.id).order_by(ImportedRow.row_number,ImportedRow.id)).all()
    rows=[r for r in rows if not r.raw.get('removed')]
    if not rows:raise HTTPException(422,'Select at least one imported material')
    if any(not r.raw.get('accepted') or r.status in {'failed','queued'} for r in rows):raise HTTPException(409,'Accept a match or keep each remaining row as a reviewed custom material')
    check_lines(db,[r.raw for r in rows])
    lines=[{'key':f'rfq-import:{batch.id}:{r.id}',**RFQLineIn.model_validate(r.raw).model_dump(mode='json')} for r in rows]
    batch.status='confirmed';db.commit()
    return {'batch_id':batch.id,'lines':lines}


def parse_rows(content,filename,column_mapping,categories):
    sheets=_discover(content,filename,None,column_mapping,require_categories=False)
    if not any(s.get('_rows') is not None for s in sheets):
        sheets=_discover(content,filename,'__all__',column_mapping,require_categories=False)
    sheet=next((s for s in reversed(sheets) if s.get('_rows') is not None),None)
    if not sheet:return [],[],{},'No readable data worksheet was found'
    mapping=sheet.get('_map') or _column_map(sheet['headers'],column_mapping)
    if not {'material_name','unit','quantity'}<=mapping.keys():return [],sheet['headers'],mapping,'Map Description, Unit and Quantity columns before matching'
    output=[];main_label='';sub_label='';previous_sheet=None
    for source in sheet['_rows']:
        if previous_sheet!=source.get('_sheet'):main_label='';sub_label='';previous_sheet=source.get('_sheet')
        get=lambda key:str(source.get(mapping.get(key,''),'') or '').strip()
        name=get('material_name');unit=get('unit');quantity=get('quantity');main=get('main_category');sub=get('subcategory')
        if _header(name) in FIELD_ALIASES['material_name'] and (_header(unit) in FIELD_ALIASES['unit'] or _header(quantity) in FIELD_ALIASES['quantity']):continue
        if not unit and not quantity and (name or main or sub):
            if main:main_label=main;sub_label=sub
            else:sub_label=sub or name
            continue
        if not any((name,unit,quantity)):continue
        if main and normalize_text(main)!=normalize_text(main_label):main_label=main;sub_label=''
        if sub:sub_label=sub
        roots=[c for c in categories.values() if not c.parent_id and normalize_text(c.name_en)==normalize_text(main_label) or not c.parent_id and c.name_ar and normalize_text(c.name_ar)==normalize_text(main_label)]
        children=[c for c in categories.values() if c.parent_id and normalize_text(sub_label) in {normalize_text(c.name_en),normalize_text(c.name_ar)} and (not roots or c.parent_id==roots[0].id)] if sub_label else []
        if len(children)==1 and not roots:roots=[categories[children[0].parent_id]]
        main_id=roots[0].id if len(roots)==1 else None;sub_id=children[0].id if len(children)==1 else None
        try:
            numeric=''.join(str(unicodedata.digit(c)) if c.isdecimal() else c for c in quantity).replace('٫','.').replace('٬','')
            if ',' in numeric:numeric=numeric.replace(',','') if '.' in numeric else numeric.replace(',','.')
            q=Decimal(numeric)
            RFQLineIn(description=name,unit=canonical_unit(unit),quantity=q)
            issue=None;quantity=format(q,'f')
        except (InvalidOperation,ValueError):issue='Description, approved unit and positive quantity (up to 3 decimals) are required'
        output.append({'row_number':source['_row'],'original_description':name,'description':name,'specifications':get('specifications'),
            'main_category_id':main_id,'subcategory_id':sub_id,'material_id':None,'unit':canonical_unit(unit),'quantity':quantity,
            'group':' / '.join(filter(None,[main_label,sub_label])),'attributes':attributes(name+' '+get('specifications')),
            'candidates':[],'confidence':0,'accepted':False,'removed':False,'ai_fallback':False,'version':1,'parse_issue':issue})
        if len(output)>MAX_ROWS:raise HTTPException(413,'Maximum RFQ import size is 300 material rows')
    if not output:raise HTTPException(422,'No material rows were found')
    return output,sheet['headers'],mapping,None


def process_rfq_import_jobs(db:Session):
    """Claim one 30-row chunk with a recoverable lease; release DB before Gemini."""
    now=datetime.now(UTC)
    lease=ImportBatch.import_options['lease_until'].as_string()
    batch=db.scalar(select(ImportBatch).where(ImportBatch.kind==KIND,or_(ImportBatch.status.in_({'uploaded','queued'}),and_(ImportBatch.status=='processing',lease<now.isoformat())))
        .order_by(ImportBatch.created_at).limit(1).with_for_update(skip_locked=True))
    if not batch:return None
    batch_id=batch.id;token=str(uuid.uuid4());stage=batch.stage;filename=batch.filename;storage_key=batch.source_storage_key;digest=batch.source_sha256
    options={**batch.import_options,'lease_token':token,'lease_until':(now+timedelta(seconds=90)).isoformat()}
    batch.import_options=options;batch.status='processing';db.commit()
    try:
        categories={c.id:c for c in db.scalars(select(Category).where(Category.active.is_(True)))}
        if stage=='parse':
            # Reference data is copied before storage I/O; no connection is held.
            from types import SimpleNamespace
            category_data={key:SimpleNamespace(id=c.id,parent_id=c.parent_id,name_en=c.name_en,name_ar=c.name_ar) for key,c in categories.items()}
            db.rollback();content=get_import_storage().read(storage_key)
            if sha256(content).hexdigest()!=digest:raise ValueError('Source checksum mismatch')
            values,headers,mapping,issue=parse_rows(content,filename,options.get('column_mapping'),category_data)
            batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id).with_for_update())
            if batch.import_options.get('lease_token')!=token:db.rollback();return batch_id
            batch.preview_metadata={'headers':headers};batch.failure_reason=issue
            if issue:batch.status='needs_mapping';db.commit();return batch_id
            for value in values:
                row_number=value.pop('row_number');db.add(ImportedRow(batch_id=batch_id,row_number=row_number,raw=value,status='failed' if value['parse_issue'] else 'queued',issue=value['parse_issue']))
            batch.rows=len(values);batch.stage='match';batch.status='queued';batch.completed_rows=0;db.commit();return batch_id
        pending=db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch_id,ImportedRow.status=='queued').order_by(ImportedRow.row_number,ImportedRow.id).limit(BATCH_ROWS)).all()
        copied=[{'id':r.id,**r.raw} for r in pending]
        invalid=set()
        for value in copied:
            try:RFQLineIn.model_validate(value)
            except ValueError:invalid.add(value['id'])
        units=db.scalars(select(MeasurementUnit).where(MeasurementUnit.code.in_({r['unit'] for r in copied}))).all();allowed={u.code for u in units if u.active}|(set(UNIT_NAMES)-{u.code for u in units if not u.active})
        from types import SimpleNamespace
        category_data={key:SimpleNamespace(id=c.id,parent_id=c.parent_id,name_en=c.name_en,name_ar=c.name_ar) for key,c in categories.items()}
        candidates=shortlist(db,copied,categories)
        for value,items in zip(copied,candidates):value['candidates']=items
        db.rollback()  # releases the pool connection before the remote request
        ambiguous=[v for v in copied if v['id'] not in invalid and v['candidates'] and v['candidates'][0]['score']<1 and v['unit'] in allowed]
        selected,fallback=rerank(ambiguous) if ambiguous and options.get('ai_enabled') else ({},False)
        unmatched_rows=[v for v in copied if v['id'] not in invalid and not v['candidates'] and not (v.get('main_category_id') and v.get('subcategory_id')) and v['unit'] in allowed]
        cat_suggestions,cat_fallback=classify_unmatched_batch(unmatched_rows,category_data,ai_enabled=bool(options.get('ai_enabled'))) if unmatched_rows else ({},False)
        batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id).with_for_update())
        if batch.import_options.get('lease_token')!=token:db.rollback();return batch_id
        stored_rows={r.id:r for r in db.scalars(select(ImportedRow).where(ImportedRow.batch_id==batch_id,ImportedRow.id.in_({v['id'] for v in copied})).with_for_update())}
        for value in copied:
            row=stored_rows[value['id']];items=value['candidates'];value.pop('id',None)
            issue=None
            if row.id in invalid:status='failed';issue='Description, approved unit and positive quantity (up to 3 decimals) are required'
            elif value['unit'] not in allowed:status='failed';issue='Choose an active approved unit'
            elif items:
                choice=items[0];confidence=choice['score']
                ai_choice=selected.get(row.id)
                if ai_choice:choice=next(c for c in items if c['id']==ai_choice[0]);confidence=ai_choice[1]
                # An ambiguous exact name across categories still requires review.
                high=confidence==1 and sum(c['score']==1 for c in items)==1
                status='high_confidence' if high else 'needs_review'
                value.update(material_id=choice['id'],main_category_id=choice['main_category_id'],subcategory_id=choice['subcategory_id'],confidence=confidence)
            else:
                suggestion=cat_suggestions.get(row.id)
                status='no_match'
                if suggestion:
                    value.update(
                        material_id=None,
                        main_category_id=value.get('main_category_id') or suggestion['main_category_id'],
                        subcategory_id=value.get('subcategory_id') or suggestion['subcategory_id'],
                        confidence=suggestion['confidence'],
                    )
                else:
                    value.update(material_id=None,confidence=0)
            value['ai_fallback']=(fallback and bool(items) and items[0]['score']<1) or (cat_fallback and row.id in {r['id'] for r in unmatched_rows})
            row.raw=value;row.status=status;row.issue=issue
        db.flush()
        remaining=db.scalar(select(ImportedRow.id).where(ImportedRow.batch_id==batch_id,ImportedRow.status=='queued').limit(1))
        batch.completed_rows=min(batch.rows,batch.completed_rows+len(copied));batch.status='queued' if remaining else 'preview';batch.failure_reason=None
        db.commit();return batch_id
    except Exception as exc:
        db.rollback();batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id).with_for_update())
        if batch and batch.import_options.get('lease_token')==token:
            batch.status='failed';batch.failure_reason=str(exc.detail)[:300] if isinstance(exc,HTTPException) else 'Processing was interrupted. Previously completed rows are preserved; retry this job.'
            db.commit()
        return batch_id
