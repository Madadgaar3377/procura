"use client";
import { useEffect, useRef, useState } from 'react';
import { IconDownload, IconUpload } from '@tabler/icons-react';
import { apiUrl, uploadStream } from '@/lib/api/client';
import { Button } from '@/components/ui/button';

type Progress = { total_rows:number; batch_size:number; completed_batches:number; batches_total:number; imported:number; skipped:number; incomplete:number; failed:number; progress_percent:number; issues:{row:number;reason:string}[] };
type Result = Progress & { batch_id:string; incomplete_rows?:{row:number;reason:string;incomplete?:boolean}[] };

export function BulkUpload({kind,open,onClose,onDone}:{kind:'materials'|'suppliers';open:boolean;onClose:()=>void;onDone:()=>void}) {
 const dialog=useRef<HTMLDialogElement>(null); const input=useRef<HTMLInputElement>(null);
 const [file,setFile]=useState<File>(); const [busy,setBusy]=useState(false); const [progress,setProgress]=useState(0); const [saving,setSaving]=useState(false);
 const [result,setResult]=useState<Result>(); const [live,setLive]=useState<Progress>(); const [error,setError]=useState('');
 useEffect(()=>{if(open&&!dialog.current?.open)dialog.current?.showModal();else if(!open&&dialog.current?.open)dialog.current.close();},[open]);
 const title=kind==='materials'?'Materials':'Suppliers';
 async function submit(){if(!file||busy)return;setBusy(true);setSaving(false);setError('');setResult(undefined);setLive(undefined);setProgress(0);try{const form=new FormData();form.set('file',file);const value=await uploadStream<Result>(`imports/bulk/${kind}`,form,setProgress,event=>{if(event.type==='started'||event.type==='progress'){setSaving(true);setLive(event as unknown as Progress);}});setResult(value);setLive(value);onDone();}catch(e){setError(e instanceof Error?e.message:'The import failed. Completed batches are safe to retry.');}finally{setBusy(false);setSaving(false);}}
 return <dialog ref={dialog} className="live-dialog bulk-upload-dialog" aria-labelledby={`bulk-${kind}-title`} onCancel={onClose} onClose={onClose}>
  <div className="live-form"><div className="panel-title"><div><h2 id={`bulk-${kind}-title`}>Bulk Upload {title}</h2><p>Upload an Excel workbook. Successful rows are saved to Procura immediately.</p></div><Button type="button" variant="outline" onClick={onClose}>Close</Button></div>
   <a className="bulk-template-link" href={apiUrl(`imports/bulk/${kind}/template`)}><IconDownload size={17}/>{kind==='materials'?'Download Excel Template':'Download Supplier Excel Template'}</a>
   <label className="field">Upload Excel File (.xlsx, .xls)<input ref={input} type="file" accept=".xlsx,.xls" onChange={e=>{setFile(e.target.files?.[0]);setResult(undefined);setError('');}}/></label>
   {file&&<p>{file.name} · {(file.size/1024).toFixed(0)} KB</p>}
   <div className="live-actions"><Button disabled={!file||busy} onClick={()=>void submit()}><IconUpload size={16}/>{busy?'Uploading…':'Upload'}</Button></div>
   {busy&&!saving&&<div className="import-progress" role="status"><i/><span>Uploading workbook… {progress}%</span></div>}
   {live&&<section className="bulk-result" aria-live="polite"><h3>{busy?'Saving in small batches':'Import progress'}</h3><p>Total rows: <strong>{live.total_rows}</strong> · Batch size: <strong>{live.batch_size}</strong> · Completed batches: <strong>{live.completed_batches} / {live.batches_total}</strong></p><p>Successfully imported: <strong>{live.imported}</strong> · Incomplete: <strong>{live.incomplete}</strong> · Already existing: <strong>{live.skipped}</strong> · Failed: <strong>{live.failed}</strong> · Progress: <strong>{live.progress_percent}%</strong></p><progress max={100} value={live.progress_percent} aria-label="Import progress"/>{!!live.issues?.length&&<ul>{live.issues.slice(-10).map(issue=><li key={`${issue.row}-${issue.reason}`}>Row {issue.row}: {issue.reason}</li>)}</ul>}</section>}
   {error&&<p className="notice warn" role="alert">{error}</p>}
   {result&&<section className="bulk-result" aria-live="polite"><h3>Import results</h3><p>Total suppliers: <strong>{result.total_rows}</strong> · Successfully imported: <strong>{result.imported}</strong> · Imported with incomplete information: <strong>{result.incomplete}</strong> · Already existing: <strong>{result.skipped}</strong> · Failed: <strong>{result.failed}</strong></p>
    {!!result.incomplete&&<><a className="bulk-template-link" href={apiUrl(`imports/bulk/suppliers/${result.batch_id}/incomplete`)}><IconDownload size={17}/>Download incomplete supplier report</a><ul>{(result.incomplete_rows||[]).slice(0,20).map(issue=><li key={`${issue.row}-${issue.reason}`}>Row {issue.row}: {issue.reason}</li>)}</ul></>}
    {!!result.failed&&<><a className="bulk-template-link" href={apiUrl(`imports/bulk/${result.batch_id}/errors`)}><IconDownload size={17}/>Download Excel error report</a><ul>{result.issues.slice(0,20).map(issue=><li key={`${issue.row}-${issue.reason}`}>Row {issue.row}: {issue.reason}</li>)}</ul></>}
   </section>}
  </div>
 </dialog>;
}
