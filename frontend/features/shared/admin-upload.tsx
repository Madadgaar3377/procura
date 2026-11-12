"use client";
import {useEffect,useRef,useState} from 'react';
import {IconAlertTriangle,IconCheck,IconDownload,IconFileSpreadsheet,IconLoader2,IconUpload} from '@tabler/icons-react';
import {Button} from '@/components/ui/button';
import {apiUrl} from '@/lib/api/client';
import {ApiError} from '@/lib/api/errors';
import {useLocale} from '@/lib/locale';

export function UploadDropzone({file,disabled,onSelect,onError}:{file?:File;disabled?:boolean;onSelect:(file:File)=>void;onError:(message:string)=>void}){
 const {arabic}=useLocale(),input=useRef<HTMLInputElement>(null),[dragging,setDragging]=useState(false);
 const choose=(selected?:File)=>{if(!selected||disabled)return;if(!/\.(xlsx|xls)$/i.test(selected.name)){onError(arabic?'اختر ملف Excel بصيغة .xlsx أو .xls.':'Choose an Excel .xlsx or .xls file.');return;}if(selected.size>8_000_000){onError(arabic?'الحد الأقصى لحجم الملف هو 8 ميجابايت.':'Maximum workbook size is 8 MB.');return;}onSelect(selected);};
 return <div className={`admin-upload-zone${dragging?' is-dragging':''}${disabled?' is-disabled':''}`} aria-disabled={disabled||undefined}
  onDragOver={event=>{event.preventDefault();if(!disabled)setDragging(true);}} onDragLeave={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node))setDragging(false);}}
  onDrop={event=>{event.preventDefault();setDragging(false);if(event.dataTransfer.files.length>1){onError(arabic?'اختر ملفاً واحداً في كل مرة.':'Choose one workbook at a time.');return;}choose(event.dataTransfer.files[0]);}}>
  <span className="admin-upload-icon"><IconUpload size={25}/></span><strong>{arabic?'اسحب ملف Excel وأفلته هنا':'Drag & drop your Excel file'}</strong><p>{arabic?'ملفات .xlsx و.xls حتى 8 ميجابايت':'.xlsx and .xls workbooks, up to 8 MB'}</p>
  <input ref={input} className="sr-only" type="file" accept=".xlsx,.xls" disabled={disabled} aria-label={arabic?'اختيار ملف Excel':'Select Excel workbook'} tabIndex={-1} onChange={event=>{choose(event.target.files?.[0]);event.target.value='';}}/>
  <Button type="button" variant="outline" disabled={disabled} onClick={()=>input.current?.click()}>{arabic?(file?'استبدال الملف':'استعراض الملفات'):(file?'Replace file':'Browse files')}</Button>
  {file&&<div className="admin-selected-file" aria-live="polite"><IconFileSpreadsheet size={21}/><div><strong>{file.name}</strong><span>{new Intl.NumberFormat(arabic?'ar-SA':'en-US',{maximumFractionDigits:1}).format(file.size/1024)} KB</span></div><IconCheck size={18}/></div>}
 </div>;
}

export function DownloadAction({path,filename,children,onError,disabled=false}:{path:string;filename:string;children:React.ReactNode;onError:(message:string)=>void;disabled?:boolean}){
 const {arabic}=useLocale(),[loading,setLoading]=useState(false),pending=useRef(false),controller=useRef<AbortController|null>(null);
 useEffect(()=>()=>controller.current?.abort(),[]);
 async function download(){if(pending.current||disabled)return;pending.current=true;setLoading(true);controller.current=new AbortController();try{
  const response=await fetch(apiUrl(path),{credentials:'include',cache:'no-store',signal:controller.current.signal});
  if(!response.ok){const body=await response.json().catch(()=>null);throw new ApiError(response.status,body?.detail,response.headers.get('Retry-After'),response.headers.get('X-Request-ID'));}
  const blob=await response.blob(),url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(error){if(!controller.current?.signal.aborted)onError(error instanceof Error?error.message:(arabic?'تعذر إعداد التنزيل.':'Could not prepare download.'));}finally{pending.current=false;setLoading(false);}}
 return <Button type="button" variant="outline" className="admin-download" disabled={disabled||loading} onClick={()=>void download()} aria-busy={loading}>{loading?<IconLoader2 className="admin-spin" size={17}/>:<IconDownload size={17}/>}<span>{loading?(arabic?'جارٍ إعداد التنزيل…':'Preparing download…'):children}</span></Button>;
}

export function StatusBadge({status}:{status?:string}){
 const {arabic}=useLocale();const labels:Record<string,[string,string]>={queued:['Queued','في قائمة الانتظار'],processing:['Processing','قيد المعالجة'],uploading:['Uploading','جارٍ الرفع'],validating:['Validating','جارٍ التحقق'],completed:['Completed','مكتمل'],completed_with_errors:['Needs review','بحاجة إلى مراجعة'],failed:['Failed','تعذر الاستيراد']};
 const label=labels[status||'']||['Ready','جاهز'];return <span className={`admin-status admin-status-${status||'ready'}`}>{status==='failed'||status==='completed_with_errors'?<IconAlertTriangle size={14}/>:status==='completed'?<IconCheck size={14}/>:<i/>}{label[arabic?1:0]}</span>;
}
export const actualNumber=(value:unknown):number|undefined=>typeof value==='number'&&Number.isFinite(value)?value:undefined;
export function ProgressPanel({status,stage,percent,batches,totalBatches,children}:{status?:string;stage:string;percent?:number;batches?:number;totalBatches?:number;children?:React.ReactNode}){
 const {arabic}=useLocale(),valid=percent!==undefined&&percent>=0&&percent<=100;
 return <section className="admin-progress-panel" aria-label={arabic?'تقدم الاستيراد':'Import progress'}><div className="admin-progress-heading"><StatusBadge status={status}/><span>{valid?`${percent}%`:(arabic?'جارٍ العمل…':'Working…')}</span></div><p role="status" aria-live="polite">{stage}</p><progress max={100} value={valid?percent:undefined} aria-label={arabic?'تقدم الاستيراد':'Import progress'}/>{batches!==undefined&&totalBatches!==undefined&&<p className="admin-batch-count">{arabic?'الدُفعات المكتملة':'Completed batches'} <strong>{batches} / {totalBatches}</strong></p>}{children}</section>;
}
