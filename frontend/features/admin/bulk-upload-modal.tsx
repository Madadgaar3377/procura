"use client";
import {useEffect,useRef,useState} from 'react';
import {IconAlertTriangle,IconCheck,IconChevronDown,IconInfoCircle,IconLoader2,IconRefresh,IconUpload} from '@tabler/icons-react';
import {api,upload,uploadStream} from '@/lib/api/client';
import {Button} from '@/components/ui/button';
import {useLocale} from '@/lib/locale';
import {AdminModal,ModalBody,ModalFooter,ModalHeader} from '@/features/shared/admin-modal';
import {actualNumber,DownloadAction,ProgressPanel,UploadDropzone} from '@/features/shared/admin-upload';

type Issue={row:number;reason:string;sheet?:string;severity?:string};
export type ImportProgress={batch_id?:string;status?:string;stage?:string;message?:string;total_rows?:number;batch_size?:number;completed_batches?:number;batches_total?:number;imported?:number;skipped?:number;incomplete?:number;failed?:number;progress_percent?:number;issues?:Issue[];warnings?:Issue[];total_supplier_records_identified?:number;new_suppliers_saved?:number;existing_suppliers_reused?:number;suppliers_updated?:number;suppliers_incomplete?:number;contacts_created?:number;contacts_updated?:number;category_assignments_created?:number;capabilities_saved?:number;capabilities_skipped?:number;categories_created?:number;materials_created?:number;pending_category_mappings?:number;pending_material_mappings?:number;supplier_failures?:number;failed_rows?:number;responsibilities_created?:number;responsibilities_reused?:number;capabilities_reused?:number};
const storageKey='procura:supplier-import';
const activeStatus=(status?:string)=>status==='queued'||status==='processing';
const terminalStatus=(status?:string)=>status==='completed'||status==='completed_with_errors'||status==='failed';

export function BulkUpload({kind,open,onClose,onDone}:{kind:'materials'|'suppliers';open:boolean;onClose:()=>void;onDone:()=>void}){
 const {arabic}=useLocale(),w=(en:string,ar:string)=>arabic?ar:en;
 const [file,setFile]=useState<File>(),[live,setLive]=useState<ImportProgress>(),[error,setError]=useState('');
 const [posting,setPosting]=useState(false),[phase,setPhase]=useState<'idle'|'uploading'|'validating'|'retrying'>('idle'),[uploadPercent,setUploadPercent]=useState<number>();
 const [jobId,setJobId]=useState<string>(),[pollVersion,setPollVersion]=useState(0),[recovering,setRecovering]=useState(false);
 const submitting=useRef(false),onDoneRef=useRef(onDone),notified=useRef<string|undefined>(undefined);onDoneRef.current=onDone;
 const lastStreamProgress=useRef<ImportProgress|undefined>(undefined);
 const active=kind==='suppliers'&&activeStatus(live?.status),locked=posting||active||recovering;
 const pendingCategories=actualNumber(live?.pending_category_mappings),pendingMaterials=actualNumber(live?.pending_material_mappings);
 const pending=pendingCategories!==undefined&&pendingMaterials!==undefined?pendingCategories+pendingMaterials:pendingCategories??pendingMaterials;
 const failures=kind==='suppliers'?(actualNumber(live?.supplier_failures)??actualNumber(live?.failed)):actualNumber(live?.failed);
 const needsReview=live?.status==='completed_with_errors'||(pending??0)>0||(actualNumber(live?.capabilities_skipped)??0)>0||(failures??0)>0||(actualNumber(live?.failed_rows)??0)>0||!!live?.issues?.length;
 const complete=live?.status==='completed'||live?.status==='completed_with_errors';
 const number=(value?:number)=>value===undefined?'—':new Intl.NumberFormat(arabic?'ar-SA':'en-US',{maximumFractionDigits:2}).format(value);
 function showJob(value:ImportProgress){setLive(value);setRecovering(false);if(value.status==='failed')setError(value.message||w('The import paused. Saved progress is preserved. Retry to resume.','توقف الاستيراد. تم الاحتفاظ بالتقدم المحفوظ. أعد المحاولة للمتابعة.'));}
 function remember(id:string){setJobId(id);try{localStorage.setItem(storageKey,id);}catch{}}
 useEffect(()=>{if(!open||kind!=='suppliers')return;try{const stored=localStorage.getItem(storageKey);if(stored){setRecovering(true);setJobId(stored);setPollVersion(value=>value+1);}}catch{}},[open,kind]);
 useEffect(()=>{
  if(!open||!jobId||kind!=='suppliers')return;
  let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;const controller=new AbortController();
  async function poll(){
   try{
    const value=await api<ImportProgress>(`imports/bulk/suppliers/jobs/${jobId}`,{signal:controller.signal});if(stopped)return;
    setError('');showJob(value);
    if(terminalStatus(value.status)){if(value.status!=='failed'&&notified.current!==jobId){notified.current=jobId;onDoneRef.current();}return;}
   }catch(cause){if(stopped)return;setError(`${cause instanceof Error?cause.message:w('Could not read import progress.','تعذر قراءة تقدم الاستيراد.')} ${w('Saved progress is preserved. Refresh to reconnect.','تم الاحتفاظ بالتقدم المحفوظ. حدّث لإعادة الاتصال.')}`);}
   if(!stopped)timer=setTimeout(()=>void poll(),2500);
  }
  void poll();return()=>{stopped=true;if(timer)clearTimeout(timer);controller.abort();};
  // Each request completes before scheduling the next; closing aborts the read.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 },[open,jobId,kind,pollVersion]);
 function select(next:File){if(locked)return;setFile(next);setLive(undefined);setJobId(undefined);setError('');setUploadPercent(undefined);notified.current=undefined;try{if(kind==='suppliers')localStorage.removeItem(storageKey);}catch{}}
 async function submit(){
  if(!file||locked||submitting.current)return;submitting.current=true;setPosting(true);setPhase('uploading');setUploadPercent(undefined);setError('');setLive(undefined);
  try{const form=new FormData();form.set('file',file);
   if(kind==='suppliers'){
    const value=await upload<ImportProgress>('imports/bulk/suppliers/jobs',form,setUploadPercent,()=>setPhase('validating'));
    showJob(value);if(value.batch_id){remember(value.batch_id);setPollVersion(v=>v+1);}
   }else{
    lastStreamProgress.current=undefined;
    const value=await uploadStream<ImportProgress>('imports/bulk/materials',form,setUploadPercent,event=>{if(event.type==='started'||event.type==='progress'){setPhase('validating');lastStreamProgress.current=event as ImportProgress;setLive({...event,status:'processing'} as ImportProgress);}});
    const previous:ImportProgress|undefined=lastStreamProgress.current as ImportProgress|undefined;
    setLive({...previous,...value,status:(actualNumber(value.failed)??0)>0?'completed_with_errors':'completed'});onDoneRef.current();
   }
  }catch(cause){setError(cause instanceof Error?cause.message:w('The import could not be completed. Saved progress is preserved.','تعذر إكمال الاستيراد. تم الاحتفاظ بالتقدم المحفوظ.'));if(kind==='materials')setLive(previous=>previous?{...previous,status:'failed'}:previous);}
  finally{submitting.current=false;setPosting(false);setPhase('idle');}
 }
 async function retry(){
  if(!jobId||submitting.current||active)return;submitting.current=true;setPosting(true);setPhase('retrying');setError('');
  try{const value=await api<ImportProgress>(`imports/bulk/suppliers/jobs/${jobId}/retry`,{method:'POST'});showJob(value);if(value.batch_id)remember(value.batch_id);notified.current=undefined;setPollVersion(v=>v+1);}
  catch(cause){setError(cause instanceof Error?cause.message:w('Retry could not be started.','تعذر بدء إعادة المحاولة.'));}
  finally{submitting.current=false;setPosting(false);setPhase('idle');}
 }
 const title=w(kind==='suppliers'?'Bulk Upload Suppliers':'Bulk Upload Materials',kind==='suppliers'?'استيراد الموردين بالجملة':'استيراد المواد بالجملة');
 const stage=phase==='retrying'?w('Resuming the saved import…','جارٍ استئناف الاستيراد المحفوظ…'):live?.status==='queued'?w('Waiting for the import worker. Your upload is saved.','في انتظار معالجة الاستيراد. تم حفظ الملف المرفوع.'):live?.status==='processing'?w('Saving records and relationships in small batches.','جارٍ حفظ السجلات والعلاقات على دفعات صغيرة.'):complete?w('All source batches have been processed. Review the saved and pending counts below.','تمت معالجة جميع دفعات المصدر. راجع أعداد السجلات المحفوظة والمعلّقة أدناه.'):live?.status==='failed'?w('Processing paused. Previously committed records are preserved.','توقفت المعالجة. تم الاحتفاظ بالسجلات المحفوظة سابقاً.'):w('Reading saved import progress…','جارٍ قراءة تقدم الاستيراد المحفوظ…');
 const stats:[string,string,number|undefined][] = kind==='suppliers'?[
  ['Supplier records identified','سجلات الموردين المحددة',actualNumber(live?.total_supplier_records_identified)],
  ['New suppliers saved','الموردون الجدد المحفوظون',actualNumber(live?.new_suppliers_saved)],['Existing suppliers reused','الموردون المعاد استخدامهم',actualNumber(live?.existing_suppliers_reused)],
  ['Contacts created','جهات الاتصال المنشأة',actualNumber(live?.contacts_created)],['Contacts updated','جهات الاتصال المحدّثة',actualNumber(live?.contacts_updated)],
  ['Contact-category assignments','تعيينات جهات الاتصال والفئات',actualNumber(live?.category_assignments_created)],['Capabilities saved','قدرات الموردين المحفوظة',actualNumber(live?.capabilities_saved)],
  ['Pending mappings','التعيينات المعلّقة',pending],['Supplier failures','الموردون المتعذر استيرادهم',failures],['Failed rows','الصفوف المتعذرة',actualNumber(live?.failed_rows)],
 ]:[['Source rows','صفوف المصدر',actualNumber(live?.total_rows)],['Saved','تم الحفظ',actualNumber(live?.imported)],['Skipped','تم التخطي',actualNumber(live?.skipped)],['Failed rows','الصفوف المتعذرة',actualNumber(live?.failed)]];
 const extra:[string,string,number|undefined][]=[['Categories created','الفئات المنشأة',actualNumber(live?.categories_created)],['Materials created','المواد المنشأة',actualNumber(live?.materials_created)],['Capabilities reused','القدرات المعاد استخدامها',actualNumber(live?.capabilities_reused)],['Employee responsibilities saved','مسؤوليات الموظفين المحفوظة',actualNumber(live?.responsibilities_created)],['Pending categories','الفئات المعلّقة',pendingCategories],['Pending materials','المواد المعلّقة',pendingMaterials]];
 const canRetry=kind==='suppliers'&&!!jobId&&(live?.status==='failed'||(live?.status==='completed_with_errors'&&(failures??0)>0));
 const reportId=live?.batch_id||jobId;
 function issuesPanel(issues:Issue[],warning:boolean){return <details className={`admin-issues${warning?'':' is-error'}`}><summary><span><IconAlertTriangle size={17}/>{warning?w('Reported warnings','التحذيرات المبلّغ عنها'):w('Failed rows','الصفوف المتعذرة')} <b>{number(issues.length)}</b></span><IconChevronDown size={17}/></summary><div>
  <p>{w('Showing the row messages returned by the import service.','تُعرض رسائل الصفوف التي أعادتها خدمة الاستيراد.')}</p>
  {reportId&&<DownloadAction path={warning?`imports/bulk/suppliers/${reportId}/warnings`:`imports/bulk/${reportId}/errors`} filename={warning?'supplier-warnings.xlsx':'import-errors.xlsx'} onError={setError}>{warning?w('Download warnings','تنزيل التحذيرات'):w('Download error report','تنزيل تقرير الأخطاء')}</DownloadAction>}
  <ul>{issues.slice(0,20).map((issue,index)=><li key={`${issue.row}-${index}`}><span className="admin-issue-row">{w('Row','الصف')} {number(actualNumber(issue.row))}</span><span>{issue.reason}{issue.sheet&&<small>{issue.sheet}</small>}</span><span className="admin-issue-severity">{warning?w('Warning','تحذير'):w('Error','خطأ')}</span></li>)}</ul>
  {issues.length>20&&<details className="admin-more-issues"><summary>{w(`Show the remaining ${issues.length-20} reported messages`,`عرض الرسائل المتبقية (${issues.length-20})`)}</summary><ul>{issues.slice(20).map((issue,index)=><li key={`${issue.row}-${index}`}><span className="admin-issue-row">{w('Row','الصف')} {number(actualNumber(issue.row))}</span><span>{issue.reason}</span><span className="admin-issue-severity">{warning?w('Warning','تحذير'):w('Error','خطأ')}</span></li>)}</ul></details>}
 </div></details>;}
 return <AdminModal open={open} normalize={false} busy={posting} preventDismiss={locked} onRequestClose={onClose} onClose={onClose} className="bulk-upload-dialog" aria-labelledby={`bulk-${kind}-title`} aria-describedby={`bulk-${kind}-description`}>
  <ModalHeader onClose={onClose} disabled={posting}><h2 id={`bulk-${kind}-title`}>{title}</h2><p id={`bulk-${kind}-description`}>{w('Import your Excel workbook. Companies, contacts and responsibilities stay connected.','استورد ملف Excel مع الحفاظ على ارتباط الشركات وجهات الاتصال والمسؤوليات.')}</p></ModalHeader>
  <ModalBody>
   {!live&&!recovering&&<><section className="admin-download-section"><h3>{w('Prepare your workbook','إعداد الملف')}</h3><div className="admin-download-actions"><DownloadAction path={`imports/bulk/${kind}/template`} filename={kind==='suppliers'?'supplier-template.xlsx':'material-template.xlsx'} onError={setError} disabled={posting}>{w(kind==='suppliers'?'Download Supplier Excel Template':'Download Excel Template',kind==='suppliers'?'تنزيل نموذج الموردين':'تنزيل نموذج Excel')}</DownloadAction>{kind==='suppliers'&&<DownloadAction path="imports/bulk/suppliers/export" filename="suppliers-with-contacts.xlsx" onError={setError} disabled={posting}>{w('Export suppliers with contacts and categories','تصدير الموردين وجهات الاتصال والفئات')}</DownloadAction>}</div></section><UploadDropzone file={file} disabled={locked} onSelect={select} onError={setError}/></>}
   {(live||recovering)&&<div className="admin-import-file"><span><IconUpload size={18}/><strong>{file?.name||w('Saved import','الاستيراد المحفوظ')}</strong></span>{!locked&&<Button type="button" variant="ghost" size="sm" onClick={()=>{setFile(undefined);setLive(undefined);setJobId(undefined);setError('');try{if(kind==='suppliers')localStorage.removeItem(storageKey);}catch{}}}>{w('Choose another file','اختيار ملف آخر')}</Button>}</div>}
   {posting&&!live&&<ProgressPanel status={phase==='uploading'?'uploading':phase==='retrying'?'processing':'validating'} stage={phase==='uploading'?w('Transferring your workbook securely.','جارٍ نقل الملف بأمان.'):phase==='retrying'?stage:w('Reading the workbook and preparing the import.','جارٍ قراءة الملف وإعداد الاستيراد.')} percent={phase==='uploading'?uploadPercent:undefined}/>}
   {(live||recovering)&&<>
    {complete&&<div className={`admin-completion${needsReview?' needs-review':''}`} role="status">{needsReview?<IconAlertTriangle size={22}/>:<IconCheck size={22}/>}<div><strong>{needsReview?w('Import finished — review needed','اكتمل الاستيراد — يلزم المراجعة'):w('Import complete','اكتمل الاستيراد')}</strong><p>{needsReview?w('Saved records are preserved. Some mappings or rows still need attention.','تم الاحتفاظ بالسجلات المحفوظة. بعض التعيينات أو الصفوف لا تزال بحاجة إلى مراجعة.'):w('Your saved records are reflected in the summary below.','تظهر السجلات المحفوظة في الملخص أدناه.')}</p></div></div>}
    <ProgressPanel status={complete&&needsReview?'completed_with_errors':live?.status} stage={stage} percent={actualNumber(live?.progress_percent)} batches={actualNumber(live?.completed_batches)} totalBatches={actualNumber(live?.batches_total)}>
     {live&&<dl className="admin-stat-grid">{stats.map(([en,ar,value])=><div key={en}><dt>{w(en,ar)}</dt><dd>{number(value)}</dd></div>)}</dl>}
    </ProgressPanel>
    {live&&<details className="admin-extra-details"><summary>{w('More import details','تفاصيل إضافية للاستيراد')}<IconChevronDown size={16}/></summary><dl className="admin-stat-grid">{extra.filter(([, ,value])=>value!==undefined).map(([en,ar,value])=><div key={en}><dt>{w(en,ar)}</dt><dd>{number(value)}</dd></div>)}</dl>{live.batch_id&&<p className="admin-import-reference">{w('Import reference','مرجع الاستيراد')}: <bdi>{live.batch_id}</bdi></p>}</details>}
    {kind==='suppliers'&&!!live?.warnings?.length&&issuesPanel(live.warnings,true)}
    {!!live?.issues?.length&&issuesPanel(live.issues,false)}
    {!live?.issues?.length&&(failures??0)>0&&reportId&&<DownloadAction path={`imports/bulk/${reportId}/errors`} filename="import-errors.xlsx" onError={setError}>{w('Download error report','تنزيل تقرير الأخطاء')}</DownloadAction>}
   </>}
   {error&&<div className="admin-import-error" role="alert"><IconAlertTriangle size={19}/><p>{error}</p></div>}
   {active&&<p className="admin-import-hint"><IconInfoCircle size={16}/>{w('The import continues safely in the background when you close this dialog.','يستمر الاستيراد بأمان في الخلفية عند إغلاق هذه النافذة.')}</p>}
  </ModalBody>
  <ModalFooter><Button type="button" variant="outline" disabled={posting} onClick={onClose}>{w('Close','إغلاق')}</Button>{jobId&&<Button type="button" variant="outline" disabled={posting} onClick={()=>setPollVersion(v=>v+1)}><IconRefresh size={16}/>{w('Refresh progress','تحديث التقدم')}</Button>}{canRetry?<Button type="button" disabled={posting||active} onClick={()=>void retry()}>{posting?<IconLoader2 size={17} className="admin-spin"/>:<IconRefresh size={17}/>} {w('Retry failed rows','إعادة محاولة الصفوف المتعذرة')}</Button>:!live&&<Button type="button" disabled={!file||locked} onClick={()=>void submit()}>{posting?<IconLoader2 size={17} className="admin-spin"/>:<IconUpload size={17}/>} {posting?w('Starting import…','جارٍ بدء الاستيراد…'):w('Start Import','بدء الاستيراد')}</Button>}</ModalFooter>
 </AdminModal>;
}
