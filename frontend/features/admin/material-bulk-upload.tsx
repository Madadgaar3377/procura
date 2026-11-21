"use client";
import {useEffect,useRef,useState} from 'react';
import {IconUpload} from '@tabler/icons-react';
import {Button} from '@/components/ui/button';
import {AdminModal,ModalHeader,ModalBody,ModalFooter} from '@/features/shared/admin-modal';
import {DownloadAction,ProgressPanel} from '@/features/shared/admin-upload';
import {useApi} from '@/lib/api/use-api';
import {api,upload} from '@/lib/api/client';
import {useLocale} from '@/lib/locale';
import type {Schemas} from '@/lib/api/generated/schema';

type Progress=Schemas['MaterialImportPreview'];
const running=(status?:string)=>['uploaded','queued','processing'].includes(status||'');
const finished=(status?:string)=>['completed','completed_with_errors'].includes(status||'');

export function MaterialBulkUpload({open,onClose,onDone}:{open:boolean;onClose:()=>void;onDone:()=>void}){
 const {t,arabic}=useLocale();
 const categories=useApi<Schemas['CategoryOut'][]>('taxonomy/categories');
 const [groupMapping,setGroupMapping]=useState<Record<string,{main_category_id:string;subcategory_id:string|null}>>({});
 const [file,setFile]=useState<File>(),[result,setResult]=useState<Progress>(),[batchId,setBatchId]=useState<string>();
 const [posting,setPosting]=useState(false),[recovering,setRecovering]=useState(false),[error,setError]=useState('');
 const [percent,setPercent]=useState<number>(),[discipline,setDiscipline]=useState(''),[pollVersion,setPollVersion]=useState(0);
 const submitting=useRef(false),done=useRef<string|undefined>(undefined),onDoneRef=useRef(onDone);onDoneRef.current=onDone;
 const busy=posting||recovering||running(result?.status);
 useEffect(()=>{
  if(!open||batchId)return;
  let active=true;const controller=new AbortController();setRecovering(true);
  api<Progress[]>('imports/materials/recent',{signal:controller.signal}).then(rows=>{
   if(active&&rows[0]){setResult(rows[0]);setBatchId(rows[0].batch_id||undefined);}
  }).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:t('Could not load saved import.'));}).finally(()=>{if(active)setRecovering(false);});
  return()=>{active=false;controller.abort();};
 },[open]);
 useEffect(()=>{
  if(!open||!batchId)return;
  let stopped=false,timer:ReturnType<typeof setTimeout>;const controller=new AbortController();
  async function poll(){
   try{
    const next=await api<Progress>(`imports/materials/${batchId}`,{signal:controller.signal});if(stopped)return;
    setResult(next);setError('');
    if(finished(next.status)&&done.current!==batchId){done.current=batchId;onDoneRef.current();window.dispatchEvent(new Event('procura:data-changed'));}
    if(!running(next.status))return;
   }catch(cause){if(stopped)return;setError((cause instanceof Error?cause.message:'')+' '+t('Saved progress is preserved. Reconnecting automatically.'));}
   if(!stopped)timer=setTimeout(()=>void poll(),2000);
  }
  void poll();return()=>{stopped=true;clearTimeout(timer);controller.abort();};
 },[open,batchId,pollVersion]);
 async function preview(){
  if(!file||busy||submitting.current)return;
  submitting.current=true;setPosting(true);setError('');setPercent(0);
  try{
   const body=new FormData();body.set('file',file);body.set('sheet_name','__all__');body.set('approved_only','true');if(discipline)body.set('discipline',discipline);
   const next=await upload<Progress>('imports/materials/preview',body,setPercent);
   setResult(next);setBatchId(next.batch_id||undefined);setPollVersion(v=>v+1);
  }catch(cause){setError(cause instanceof Error?cause.message:t('Import preview failed'));}
  finally{submitting.current=false;setPosting(false);setPercent(undefined);}
 }
 async function mapGroups(){
  if(!batchId||posting)return;setPosting(true);setError('');
  try{const body=new FormData();body.set('discipline',discipline);body.set('sheet_name','__all__');body.set('group_mapping',JSON.stringify(groupMapping));const next=await upload<Progress>(`imports/materials/${batchId}/preview-options`,body,()=>{});setResult(next);setPollVersion(v=>v+1);}
  catch(cause){setError(cause instanceof Error?cause.message:t('Mapping failed'));}finally{setPosting(false);}
 }
 async function action(kind:'confirm'|'retry'){
  if(!batchId||posting||submitting.current)return;
  submitting.current=true;setPosting(true);setError('');
  try{await api(`imports/materials/${batchId}/${kind}`,{method:'POST'});setResult(previous=>previous?{...previous,status:kind==='retry'&&previous.stage==='parse'?'uploaded':'queued'}:previous);setPollVersion(v=>v+1);}
  catch(cause){setError(cause instanceof Error?cause.message:t('Import could not be queued'));}
  finally{submitting.current=false;setPosting(false);}
 }
 function select(next?:File){
  if(!next||busy)return;
  if(!/\.(xlsx|csv)$/i.test(next.name)){setError(t('Choose an .xlsx or .csv file.'));return;}
  if(next.size>8_000_000){setError(t('Maximum workbook size is 8 MB.'));return;}
  setGroupMapping({});setFile(next);setResult(undefined);setBatchId(undefined);setError('');done.current=undefined;
 }
 const stats:[string,number][]=[['Total rows',result?.rows||0],['Completed rows',result?.completed_rows||0],
  ['New categories',result?.new_categories?.length||0],['Existing categories reused',result?.existing_categories_reused||0],
  ['New materials',result?.create||0],['Existing materials',(result?.unchanged||0)+(result?.update||0)],
  ['New records',result?.created||0],['Updated records',result?.updated_count||0],['Existing records reused',result?.unchanged_count||0],
  ['Duplicate rows',result?.duplicate_rows||0],['Invalid rows',result?.invalid||0],['Conflicts requiring review',result?.conflicts||0],['Failed records',result?.errors||0]];
 return <AdminModal open={open} size="lg" normalize={false} busy={posting} onRequestClose={onClose} aria-labelledby="material-bulk-title" className="material-bulk-dialog">
  <ModalHeader onClose={onClose} disabled={posting}><h2 id="material-bulk-title">{t('Bulk Upload')}</h2><p>{t('Upload once, preview, then import all materials in the background.')}</p></ModalHeader>
  <ModalBody>
   <DownloadAction path="imports/materials/template" filename="civil-materials-template.xlsx" onError={setError}>{t('Download Excel Template')}</DownloadAction>
   <label className="field">{t('Choose Excel File')}<input type="file" accept=".xlsx,.csv" disabled={busy} onChange={e=>{select(e.target.files?.[0]);e.target.value='';}}/></label>
   {file&&<p><bdi>{file.name}</bdi></p>}
   <label className="field">{t('Discipline')}<select value={discipline} disabled={busy||!!result} onChange={e=>setDiscipline(e.target.value)}><option value="">{t('Auto-detect from Excel')}</option>{['Civil','Electrical','Mechanical'].map(value=><option key={value} value={value}>{t(value)}</option>)}</select></label>
   {recovering&&<p role="status">{t('Loading saved import...')}</p>}
   {posting&&percent!==undefined&&<ProgressPanel status="uploading" stage={t('Uploading workbook')} percent={percent}/>}
   {error&&<p role="alert" className="form-error">{error}</p>}
   {result&&<>
    <ProgressPanel status={result.status} stage={t(result.stage==='parse'?'Validating workbook':result.status==='preview'?'Review the preview before importing':result.status==='failed'?'Import needs attention':finished(result.status)?'Import complete':'Importing materials')} percent={result.progress_percent}>
     <p role="status" aria-live="polite">{t('Current job')}: {result.current_job} / {result.jobs_total} · {t('Current batch')}: {result.current_batch} / {result.batches_in_job}</p>
     <p>{t('Processed')}: {result.completed_rows} / {result.rows}</p>
    </ProgressPanel>
    <dl className="admin-stat-grid material-bulk-stats">{stats.map(([label,value])=><div key={label}><dt>{t(label)}</dt><dd>{value.toLocaleString(arabic?'ar':'en')}</dd></div>)}</dl>
    {result.failure_reason&&<p role="alert">{t(result.failure_reason)}</p>}
    {!!result.detected_groups?.length&&result.status==='preview'&&<section><h3>{arabic?'المجموعات المكتشفة - ربط بالفئات المعتمدة':'Detected groups - map to approved categories'}</h3>{result.detected_groups.map((group,i)=>{const key=String(group.key);const target=groupMapping[key]||{main_category_id:String(group.main_category_id||''),subcategory_id:group.subcategory_id?String(group.subcategory_id):null};return <div className="form-grid" key={key}><p>{String(group.main_category)} / {String(group.subcategory)} ({String(group.rows)})</p><label className="field">{t('Main category')}<select aria-label={`${t('Main category')} ${i+1}`} value={target.main_category_id} onChange={e=>setGroupMapping(current=>({...current,[key]:{main_category_id:e.target.value,subcategory_id:null}}))}><option value="">{t('Choose main category')}</option>{categories.data?.filter(c=>!c.parent_id).map(c=><option key={c.id} value={c.id}>{arabic&&c.name_ar?c.name_ar:c.name_en}</option>)}</select></label><label className="field">{t('Subcategory')}<select value={target.subcategory_id||''} aria-label={`${t('Subcategory')} ${i+1}`} onChange={e=>setGroupMapping(current=>({...current,[key]:{...target,subcategory_id:e.target.value||null}}))}><option value="">{t('Choose subcategory')}</option>{categories.data?.filter(c=>c.parent_id===target.main_category_id).map(c=><option key={c.id} value={c.id}>{arabic&&c.name_ar?c.name_ar:c.name_en}</option>)}</select></label></div>;})}<Button disabled={posting||!Object.keys(groupMapping).length} onClick={()=>void mapGroups()}>{arabic?'تطبيق الربط وإعادة المعاينة':'Apply mappings and preview again'}</Button></section>}
    {!!result.new_categories?.length&&<details><summary>{t('New categories')}</summary><ul>{result.new_categories.map((item,i)=><li key={i}>{String(item.path||'')}</li>)}</ul></details>}
    {!!result.sample?.length&&<div className="table-wrap"><table className="data-table"><caption>{t('Preview sample; all rows are included in the totals.')}</caption><thead><tr>{['Row','Discipline','Main category','Subcategory','Description','Unit','Status'].map(label=><th key={label}>{t(label)}</th>)}</tr></thead><tbody>{result.sample.map((row,i)=><tr key={i}>{['row','discipline','category','subcategory','name','unit','status'].map(key=><td key={key}>{String(row[key]??'')}</td>)}</tr>)}</tbody></table></div>}
    {!!result.issues?.length&&<details><summary>{t('Rows requiring review')}</summary><ul>{result.issues.map((issue,i)=><li key={i}>{String(issue.row)}: {String(issue.reason)}</li>)}</ul></details>}
    {batchId&&!!result.errors&&<DownloadAction path={`imports/materials/${batchId}/errors.xlsx`} filename="failed-material-rows.xlsx" onError={setError}>{t('Download Failed Rows')}</DownloadAction>}
    <p className="workflow-help">{t('Jobs contain up to 300 rows. Progress is saved after every batch of 30. Closing or refreshing does not cancel the import.')}</p>
   </>}
  </ModalBody>
  <ModalFooter><Button type="button" variant="outline" disabled={posting} onClick={onClose}>{t('Close')}</Button>
   {result?.status==='failed'?<Button disabled={posting} onClick={()=>void action('retry')}>{t('Retry import')}</Button>:result?.status==='preview'?<Button disabled={posting||!result.valid} onClick={()=>void action('confirm')}>{t('Import Materials')}</Button>:<Button disabled={!file||busy} onClick={()=>void preview()}><IconUpload size={18}/>{t('Preview Import')}</Button>}
  </ModalFooter>
 </AdminModal>;
}
