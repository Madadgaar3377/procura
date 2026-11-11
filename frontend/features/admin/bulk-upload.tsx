"use client";
import { useEffect, useRef, useState } from 'react';
import { IconDownload, IconUpload } from '@tabler/icons-react';
import { api, apiUrl, upload, uploadStream } from '@/lib/api/client';
import { Button } from '@/components/ui/button';

type ImportMessage = { total_rows:number; batch_size:number; completed_batches:number; batches_total:number; imported:number; skipped:number; incomplete:number; failed:number; progress_percent:number; issues:{row:number;reason:string}[]; total_supplier_records_identified?:number; new_suppliers_saved?:number; existing_suppliers_reused?:number; suppliers_incomplete?:number; contacts_created?:number; contacts_updated?:number; category_assignments_created?:number; capabilities_saved?:number; capabilities_skipped?:number; categories_created?:number; materials_created?:number; pending_category_mappings?:number; supplier_failures?:number; warnings?:{row:number;reason:string;capability_saved?:boolean}[] };
type Progress = ImportMessage & {batch_id?:string;status?:string;stage?:string;message?:string;suppliers_updated?:number;failed_rows?:number;responsibilities_created?:number;responsibilities_reused?:number;capabilities_reused?:number;pending_material_mappings?:number};
type Result = ImportMessage & { batch_id:string };

export function BulkUpload({kind,open,onClose,onDone}:{kind:'materials'|'suppliers';open:boolean;onClose:()=>void;onDone:()=>void}) {
 const dialog=useRef<HTMLDialogElement>(null); const input=useRef<HTMLInputElement>(null);
 const [file,setFile]=useState<File>(); const [busy,setBusy]=useState(false); const [progress,setProgress]=useState(0); const [saving,setSaving]=useState(false);
 const [result,setResult]=useState<Result>(); const [live,setLive]=useState<Progress>(); const [error,setError]=useState('');
 const submitting=useRef(false);
 const [jobId,setJobId]=useState<string>();
 const [pollVersion,setPollVersion]=useState(0);
 const onDoneRef=useRef(onDone);
 onDoneRef.current=onDone;
 useEffect(()=>{
   if(!open||kind!=='suppliers')return;
   try{
     const stored=localStorage.getItem('procura:supplier-import');
     if(stored)setJobId(stored);
   }catch{/* Polling still works when storage is unavailable. */}
 },[open,kind]);

 function showJob(value:Progress){
   setLive(value);
   const active=value.status==='queued'||value.status==='processing';
   setBusy(active);setSaving(active);
   if(active)setResult(undefined);
   if(value.status==='completed'||value.status==='completed_with_errors')setResult(value as Result);
   if(value.status==='failed')setError(value.message||'Import paused. Retry to resume committed progress.');
 }

 async function refreshJob(){
   if(!jobId)return;
   try{
     const value=await api<Progress>(`imports/bulk/suppliers/jobs/${jobId}`);
     showJob(value);
     setError(value.status==='failed'?(value.message||'Import paused. Retry to resume.'):'');
     if(value.status==='queued'||value.status==='processing')setPollVersion(value=>value+1);
   }catch(e){
     setError(`${e instanceof Error?e.message:'Could not refresh progress.'} Saved progress is preserved. Refresh progress to reconnect.`);
   }
 }

 useEffect(()=>{
   if(!open||!jobId||kind!=='suppliers')return;
   let stopped=false;
   let timer:ReturnType<typeof setTimeout>;
   async function poll(){
     try{
       const value=await api<Progress>(`imports/bulk/suppliers/jobs/${jobId}`);
       if(stopped)return;
       showJob(value);
       setError(value.status==='failed'?(value.message||'Import paused. Retry to resume.'):'');
       if(value.status==='completed'||value.status==='completed_with_errors'){
         onDoneRef.current();return;
       }
       if(value.status==='failed')return;
     }catch(e){
       if(!stopped)setError(`${e instanceof Error?e.message:'Could not connect to API.'} Saved progress is preserved.`);
     }
     if(!stopped)timer=setTimeout(()=>void poll(),2500);
   }
   void poll();
   return()=>{stopped=true;clearTimeout(timer);};
 },[open,jobId,kind,pollVersion]);

 async function retryJob(){
   if(!jobId||submitting.current)return;
   submitting.current=true;
   try{
     const value=await api<Progress>(`imports/bulk/suppliers/jobs/${jobId}/retry`,{method:'POST'});
     showJob(value);setError('');
     setPollVersion(value=>value+1);
   }catch(e){setError(e instanceof Error?e.message:'Retry failed.');}
   finally{submitting.current=false;}
 }
 useEffect(()=>{if(open&&!dialog.current?.open)dialog.current?.showModal();else if(!open&&dialog.current?.open)dialog.current.close();},[open]);
 const title=kind==='materials'?'Materials':'Suppliers';
 async function submit(){if(!file||busy||submitting.current)return;submitting.current=true;setBusy(true);setSaving(false);setError('');setResult(undefined);setLive(undefined);setProgress(0);try{const form=new FormData();form.set('file',file);if(kind==='suppliers'){const value=await upload<Progress>('imports/bulk/suppliers/jobs',form,setProgress);showJob(value);if(value.batch_id){try{localStorage.setItem('procura:supplier-import',value.batch_id);}catch{}setJobId(value.batch_id);}return;}const value=await uploadStream<Result>(`imports/bulk/${kind}`,form,setProgress,event=>{if(event.type==='started'||event.type==='progress'){setSaving(true);setLive(event as unknown as Progress);}});setResult(value);setLive(value);onDone();}catch(e){setError(e instanceof Error?e.message:'The import failed. Committed progress is preserved.');setBusy(false);setSaving(false);}finally{submitting.current=false;if(kind==='materials'){setBusy(false);setSaving(false);}}}
 return <dialog ref={dialog} className="live-dialog bulk-upload-dialog" aria-labelledby={`bulk-${kind}-title`} onCancel={onClose} onClose={onClose}>
  <div className="live-form"><div className="panel-title"><div><h2 id={`bulk-${kind}-title`}>Bulk Upload {title}</h2><p>Upload an Excel workbook. Successful rows are saved to Procura immediately.</p></div><Button type="button" variant="outline" onClick={onClose}>Close</Button></div>
   <a className="bulk-template-link" href={apiUrl(`imports/bulk/${kind}/template`)}><IconDownload size={17}/>{kind==='materials'?'Download Excel Template':'Download Supplier Excel Template'}</a>{kind==='suppliers'&&<a className="bulk-template-link" href={apiUrl('imports/bulk/suppliers/export')}><IconDownload size={17}/>Export suppliers with contacts and categories</a>}
   <label className="field">Upload Excel File (.xlsx, .xls)<input ref={input} disabled={busy} type="file" accept=".xlsx,.xls" onChange={e=>{setFile(e.target.files?.[0]);setJobId(undefined);setLive(undefined);setResult(undefined);setError('');try{localStorage.removeItem('procura:supplier-import');}catch{}}}/></label>
   {file&&<p>{file.name} · {(file.size/1024).toFixed(0)} KB</p>}
   <div className="live-actions"><Button disabled={!file||busy} onClick={()=>void submit()}><IconUpload size={16}/>{busy?(saving?'Processing…':'Uploading…'):'Upload'}</Button></div>
   {busy&&!saving&&<div className="import-progress" role="status"><i/><span>{progress===100?'Validating Excel…':`Uploading workbook… ${progress}%`}</span></div>}
   {jobId&&<div className="live-actions"><Button variant="outline" onClick={()=>void refreshJob()}>Refresh progress</Button>{(live?.status==='failed'||(live?.status==='completed_with_errors'&&(live.supplier_failures??live.failed)>0))&&<Button onClick={()=>void retryJob()}>Retry failed rows</Button>}</div>}
   {live && (
        <section className="bulk-result" aria-live="polite">
          <h3>{busy ? 'Saving in small batches' : 'Import progress'}</h3>
          {live.status&&<p>Status: {live.status.replaceAll('_',' ')} · Stage: {live.stage?.replaceAll('_',' ')}{live.status==='queued'?' · Waiting for the import worker or saving the next batch.':''}</p>}
          {live.status==='completed'&&<p>Import completed. Successful suppliers and their relationships are saved.</p>}
          {live.status==='completed_with_errors'&&<p>Import needs review. Valid companies, contacts and raw employee responsibilities are saved. Review pending mappings and warnings; retry failed suppliers.</p>}
          <p>
            {kind === 'suppliers' ? (
              <>
                Supplier records identified: <strong>{live.total_supplier_records_identified ?? live.total_rows}</strong>
                {' ? '}New saved: <strong>{live.new_suppliers_saved ?? 0}</strong>
                {' ? '}Existing reused: <strong>{live.existing_suppliers_reused ?? 0}</strong>
              </>
            ) : (
              <>
                Total rows: <strong>{live.total_rows}</strong>
                {' ? '}Imported: <strong>{live.imported}</strong>
                {' ? '}Skipped: <strong>{live.skipped}</strong>
              </>
            )}
            {' ? '}Batch size: <strong>{live.batch_size}</strong>
            {' ? '}Completed batches: <strong>{live.completed_batches} / {live.batches_total}</strong>
          </p>
          {kind === 'suppliers' && (
            <p>
              Incomplete suppliers: <strong>{live.suppliers_incomplete ?? live.incomplete}</strong>
              {' · '}Suppliers updated: <strong>{live.suppliers_updated ?? 0}</strong>
              {' ? '}Contacts created: <strong>{live.contacts_created ?? 0}</strong>
              {' ? '}Existing contacts updated: <strong>{live.contacts_updated ?? 0}</strong>
              {' ? '}Contact-category assignments: <strong>{live.category_assignments_created ?? 0}</strong>
              {' ? '}Capabilities saved: <strong>{live.capabilities_saved ?? 0}</strong>
              {' · '}Capabilities reused: <strong>{live.capabilities_reused ?? 0}</strong>
              {' · '}Employee responsibilities saved: <strong>{live.responsibilities_created ?? 0}</strong>
              {' · '}Employee responsibilities reused: <strong>{live.responsibilities_reused ?? 0}</strong>
              {' ? '}Capability rows skipped: <strong>{live.capabilities_skipped ?? 0}</strong>
              {' ? '}Categories created: <strong>{live.categories_created ?? 0}</strong>
              {' ? '}Materials created: <strong>{live.materials_created ?? 0}</strong>
              {' ? '}Pending mappings: <strong>{live.pending_category_mappings ?? 0}</strong>
              {' · '}Pending material mappings: <strong>{live.pending_material_mappings ?? 0}</strong>
            </p>
          )}
          <p>
            Supplier failures: <strong>{kind === 'suppliers' ? (live.supplier_failures ?? live.failed) : live.failed}</strong>
            {kind==='suppliers'&&live.failed_rows!==undefined&&<> · Failed source rows: <strong>{live.failed_rows}</strong></>}
            {' ? '}Progress: <strong>{live.progress_percent}%</strong>
          </p>
          <progress max={100} value={live.progress_percent} aria-label="Import progress" />
          {!!live.issues?.length && <><h4>Supplier failures</h4><ul>{live.issues.slice(-10).map(issue => <li key={`${issue.row}-${issue.reason}`}>Row {issue.row}: {issue.reason}</li>)}</ul></>}
          {!!live.warnings?.length && <><h4>Warnings</h4><ul>{live.warnings.slice(-10).map(issue => <li key={`${issue.row}-${issue.reason}`}>Row {issue.row}: {issue.reason}</li>)}</ul></>}
        </section>
      )}
   {error&&<p className="notice warn" role="alert">{error}</p>}
   {result&&<section className="bulk-result" aria-live="polite"><h3>Import results</h3>{kind==='suppliers'?<><p>Total supplier records identified: <strong>{result.total_supplier_records_identified??result.total_rows}</strong> · New suppliers saved: <strong>{result.new_suppliers_saved??0}</strong> · Existing suppliers reused: <strong>{result.existing_suppliers_reused??0}</strong> · Incomplete profiles: <strong>{result.suppliers_incomplete??result.incomplete}</strong></p><p>Contacts created: <strong>{result.contacts_created??0}</strong> · Capabilities saved: <strong>{result.capabilities_saved??0}</strong> · Capability rows skipped: <strong>{result.capabilities_skipped??0}</strong> · Categories created: <strong>{result.categories_created??0}</strong> · Materials created: <strong>{result.materials_created??0}</strong> · Pending mappings: <strong>{result.pending_category_mappings??0}</strong> · Supplier failures: <strong>{result.supplier_failures??result.failed}</strong></p></>:<p>Total rows: <strong>{result.total_rows}</strong> · Successfully imported: <strong>{result.imported}</strong> · Skipped: <strong>{result.skipped}</strong> · Failed: <strong>{result.failed}</strong></p>}
    {!!result.warnings?.length&&<><a className="bulk-template-link" href={apiUrl(`imports/bulk/suppliers/${result.batch_id}/warnings`)}><IconDownload size={17}/>Download supplier warnings</a><h4>Warnings</h4><ul>{result.warnings.slice(0,20).map(issue=><li key={`${issue.row}-${issue.reason}`}>Row {issue.row}: {issue.reason}</li>)}</ul></>}
    {!!result.failed&&<><a className="bulk-template-link" href={apiUrl(`imports/bulk/${result.batch_id}/errors`)}><IconDownload size={17}/>Download Excel error report</a><ul>{result.issues.slice(0,20).map(issue=><li key={`${issue.row}-${issue.reason}`}>Row {issue.row}: {issue.reason}</li>)}</ul></>}
   </section>}
  </div>
 </dialog>;
}
