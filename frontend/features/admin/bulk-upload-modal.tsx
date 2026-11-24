"use client";
import {useEffect,useRef,useState} from 'react';
import {IconAlertTriangle,IconCheck,IconChevronDown,IconHistory,IconInfoCircle,IconLoader2,IconPlayerStop,IconRefresh,IconUpload} from '@tabler/icons-react';
import {api,upload,uploadStream} from '@/lib/api/client';
import {Button} from '@/components/ui/button';
import {useLocale} from '@/lib/locale';
import {AdminModal,ModalBody,ModalFooter,ModalHeader} from '@/features/shared/admin-modal';
import {actualNumber,DownloadAction,ProgressPanel,UploadDropzone} from '@/features/shared/admin-upload';

type Issue={row:number;reason:string;sheet?:string;severity?:string;company_name?:string;field?:string;code?:string;status?:string};
export type ImportProgress={
 batch_id?:string;
 filename?:string;
 file_checksum?:string;
 status?:string;
 stage?:string;
 message?:string;
 started_at?:string;
 completed_at?:string;
 created_at?:string;
 total_rows?:number;
 processed_rows?:number;
 batch_size?:number;
 completed_batches?:number;
 batches_total?:number;
 imported?:number;
 skipped?:number;
 incomplete?:number;
 failed?:number;
 progress_percent?:number;
 issues?:Issue[];
 warnings?:Issue[];
 total_supplier_records_identified?:number;
 new_suppliers_estimated?:number;
 existing_suppliers_estimated?:number;
 contacts_detected?:number;
 disciplines_detected?:number;
 categories_detected?:number;
 subcategories_detected?:number;
 material_values_detected?:number;
 existing_materials_matched?:number;
 unresolved_material_mappings?:number;
 brands_detected?:number;
 duplicate_source_rows?:number;
 valid_rows?:number;
 warning_rows?:number;
 review_required_rows?:number;
 invalid_rows?:number;
 new_suppliers_saved?:number;
 existing_suppliers_reused?:number;
 suppliers_updated?:number;
 suppliers_incomplete?:number;
 contacts_created?:number;
 contacts_updated?:number;
 category_assignments_created?:number;
 capabilities_saved?:number;
 capabilities_skipped?:number;
 material_mappings_created?:number;
 categories_created?:number;
 materials_created?:number;
 pending_category_mappings?:number;
 pending_material_mappings?:number;
 supplier_failures?:number;
 failed_rows?:number;
 responsibilities_created?:number;
 responsibilities_reused?:number;
 capabilities_reused?:number;
};

const storageKey='procura:supplier-import';
const activeStatus=(status?:string)=>status==='queued'||status==='processing';
const terminalStatus=(status?:string)=>status==='preview_ready'||status==='completed'||status==='completed_with_errors'||status==='failed'||status==='cancelled';

export function BulkUpload({kind,open,onClose,onDone}:{kind:'materials'|'suppliers';open:boolean;onClose:()=>void;onDone:()=>void}){
 const {arabic}=useLocale(),w=(en:string,ar:string)=>arabic?ar:en;
 const [file,setFile]=useState<File>(),[live,setLive]=useState<ImportProgress>(),[error,setError]=useState('');
 const [fallbackDiscipline,setFallbackDiscipline]=useState('');
 const [posting,setPosting]=useState(false),[phase,setPhase]=useState<'idle'|'uploading'|'validating'|'retrying'>('idle'),[uploadPercent,setUploadPercent]=useState<number>();
 const [jobId,setJobId]=useState<string>(),[pollVersion,setPollVersion]=useState(0),[recovering,setRecovering]=useState(false);
 const [history,setHistory]=useState<ImportProgress[]>([]),[historyOpen,setHistoryOpen]=useState(false);
 const submitting=useRef(false),onDoneRef=useRef(onDone),notified=useRef<string|undefined>(undefined),sessionFileRef=useRef<File|undefined>(undefined);
 onDoneRef.current=onDone;
 sessionFileRef.current=file;
 const lastStreamProgress=useRef<ImportProgress|undefined>(undefined);

 const active=kind==='suppliers'&&activeStatus(live?.status),locked=posting||recovering;
 const pendingCategories=actualNumber(live?.pending_category_mappings),pendingMaterials=actualNumber(live?.pending_material_mappings);
 const pending=pendingCategories!==undefined&&pendingMaterials!==undefined?pendingCategories+pendingMaterials:pendingCategories??pendingMaterials;
 const failures=kind==='suppliers'?(actualNumber(live?.supplier_failures)??actualNumber(live?.failed)):actualNumber(live?.failed);
 const needsReview=live?.status==='completed_with_errors'||(pending??0)>0||(actualNumber(live?.capabilities_skipped)??0)>0||(failures??0)>0||(actualNumber(live?.failed_rows)??0)>0||!!live?.issues?.length;
 const complete=live?.status==='completed'||live?.status==='completed_with_errors';
 const cancelled=live?.status==='cancelled';
 const number=(value?:number)=>value===undefined?'—':new Intl.NumberFormat(arabic?'ar-SA':'en-US',{maximumFractionDigits:2}).format(value);

 function showJob(value:ImportProgress){
  setLive(value);
  setRecovering(false);
  if(value.status==='failed'){
   setError(value.message||w('The import paused. Saved progress is preserved. Retry to resume.','توقف الاستيراد. تم الاحتفاظ بالتقدم المحفوظ. أعد المحاولة للمتابعة.'));
  }
 }
 function remember(id:string){setJobId(id);try{localStorage.setItem(storageKey,id);}catch{}}
 function clearRemembered(){try{localStorage.removeItem(storageKey);}catch{}}

 function resetToNewUpload(){
  setFile(undefined);
  setLive(undefined);
  setJobId(undefined);
  setError('');
  setUploadPercent(undefined);
  setRecovering(false);
  notified.current=undefined;
  if(kind==='suppliers')clearRemembered();
 }

 async function loadHistory(){
  if(kind!=='suppliers')return;
  try{
   const items=await api<ImportProgress[]>('imports/bulk/suppliers/jobs?limit=8');
   setHistory(Array.isArray(items)?items:[]);
  }catch{}
 }

 useEffect(()=>{
  if(!open||kind!=='suppliers')return;
  void loadHistory();
  try{
   const stored=localStorage.getItem(storageKey);
   if(stored){
    setRecovering(true);
    setJobId(stored);
    setPollVersion(value=>value+1);
   }else{
    setLive(undefined);
    setJobId(undefined);
    setFile(undefined);
   }
  }catch{}
 },[open,kind]);

 useEffect(()=>{
  if(!open||!jobId||kind!=='suppliers')return;
  let stopped=false,timer:ReturnType<typeof setTimeout>|undefined;
  const controller=new AbortController();
  const currentJobId=jobId;
  async function poll(){
   try{
    const value=await api<ImportProgress>(`imports/bulk/suppliers/jobs/${currentJobId}`,{signal:controller.signal});
    if(stopped)return;
    // Never restore a stale completed/cancelled/old preview job as if it were a fresh upload.
    if(!sessionFileRef.current&&recovering&&!activeStatus(value.status)){
     clearRemembered();
     setLive(undefined);
     setJobId(undefined);
     setRecovering(false);
     return;
    }
    setError('');
    showJob(value);
    if(terminalStatus(value.status)){
     if(value.status!=='failed'&&value.status!=='preview_ready'&&value.status!=='cancelled'&&notified.current!==currentJobId){
      notified.current=currentJobId;
      onDoneRef.current();
      void loadHistory();
     }
     if(value.status!=='preview_ready')clearRemembered();
     return;
    }
   }catch(cause){
    if(stopped)return;
    setRecovering(false);
    setError(`${cause instanceof Error?cause.message:w('Could not read import progress.','تعذر قراءة تقدم الاستيراد.')} ${w('Saved progress is preserved. Refresh to reconnect.','تم الاحتفاظ بالتقدم المحفوظ. حدّث لإعادة الاتصال.')}`);
   }
   if(!stopped)timer=setTimeout(()=>void poll(),2500);
  }
  void poll();
  return()=>{stopped=true;if(timer)clearTimeout(timer);controller.abort();};
  // Each request completes before scheduling the next; closing aborts the read.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 },[open,jobId,kind,pollVersion]);

 function select(next:File){
  if(locked)return;
  setFile(next);
  setLive(undefined);
  setJobId(undefined);
  setError('');
  setUploadPercent(undefined);
  notified.current=undefined;
  if(kind==='suppliers')clearRemembered();
 }

 async function submit(){
  if(!file||locked||submitting.current)return;
  submitting.current=true;setPosting(true);setPhase('uploading');setUploadPercent(undefined);setError('');setLive(undefined);
  try{
   const form=new FormData();
   form.set('file',file);
   if(kind==='suppliers'){
    if(fallbackDiscipline)form.set('fallback_discipline',fallbackDiscipline);
    const value=await upload<ImportProgress>('imports/bulk/suppliers/jobs/preview',form,setUploadPercent,()=>setPhase('validating'));
    showJob(value);
    if(value.batch_id){setJobId(value.batch_id);setPollVersion(v=>v+1);}
   }else{
    lastStreamProgress.current=undefined;
    const value=await uploadStream<ImportProgress>('imports/bulk/materials',form,setUploadPercent,event=>{
     if(event.type==='started'||event.type==='progress'){
      setPhase('validating');
      lastStreamProgress.current=event as ImportProgress;
      setLive({...event,status:'processing'} as ImportProgress);
     }
    });
    const previous:ImportProgress|undefined=lastStreamProgress.current as ImportProgress|undefined;
    setLive({...previous,...value,status:(actualNumber(value.failed)??0)>0?'completed_with_errors':'completed'});
    onDoneRef.current();
   }
  }catch(cause){
   setError(cause instanceof Error?cause.message:w('The import could not be completed. Saved progress is preserved.','تعذر إكمال الاستيراد. تم الاحتفاظ بالتقدم المحفوظ.'));
   if(kind==='materials')setLive(previous=>previous?{...previous,status:'failed'}:previous);
  }finally{submitting.current=false;setPosting(false);setPhase('idle');}
 }

 async function startPreview(){
  if(!jobId||live?.status!=='preview_ready'||submitting.current)return;
  submitting.current=true;setPosting(true);setError('');
  try{
   const value=await api<ImportProgress>(`imports/bulk/suppliers/jobs/${jobId}/start`,{method:'POST'});
   showJob(value);
   if(value.batch_id)remember(value.batch_id);
   setPollVersion(v=>v+1);
  }catch(cause){
   setError(cause instanceof Error?cause.message:w('Could not start the import.','تعذر بدء الاستيراد.'));
  }finally{submitting.current=false;setPosting(false);}
 }

 async function cancelJob(){
  if(!jobId||submitting.current)return;
  submitting.current=true;setPosting(true);setError('');
  try{
   const value=await api<ImportProgress>(`imports/bulk/suppliers/jobs/${jobId}/cancel`,{method:'POST'});
   showJob(value);
   clearRemembered();
   void loadHistory();
  }catch(cause){
   setError(cause instanceof Error?cause.message:w('Could not cancel the import.','تعذر إلغاء الاستيراد.'));
  }finally{submitting.current=false;setPosting(false);}
 }

 async function retry(){
  if(!jobId||submitting.current||active)return;
  submitting.current=true;setPosting(true);setPhase('retrying');setError('');
  try{
   const value=await api<ImportProgress>(`imports/bulk/suppliers/jobs/${jobId}/retry`,{method:'POST'});
   showJob(value);
   if(value.batch_id)remember(value.batch_id);
   notified.current=undefined;
   setPollVersion(v=>v+1);
  }catch(cause){
   setError(cause instanceof Error?cause.message:w('Retry could not be started.','تعذر بدء إعادة المحاولة.'));
  }finally{submitting.current=false;setPosting(false);setPhase('idle');}
 }

 const title=w(kind==='suppliers'?'Bulk Upload Suppliers':'Bulk Upload Materials',kind==='suppliers'?'استيراد الموردين بالجملة':'استيراد المواد بالجملة');
 const subtitle=kind==='suppliers'
  ?w('Import suppliers, contacts and material capabilities from Excel.','استورد الموردين وجهات الاتصال وقدرات المواد من ملف Excel.')
  :w('Import your Excel workbook into the Material Master.','استورد ملف Excel إلى دليل المواد الرئيسي.');

 const stage=live?.status==='preview_ready'
  ?w('Preview ready. Review the workbook summary before starting.','المعاينة جاهزة. راجع ملخص الملف قبل البدء.')
  :cancelled
   ?w('Import cancelled. Previously committed batches are preserved.','تم إلغاء الاستيراد. تم الاحتفاظ بالدفعات المحفوظة سابقاً.')
   :phase==='retrying'
    ?w('Resuming the saved import…','جارٍ استئناف الاستيراد المحفوظ…')
    :live?.status==='queued'
     ?w('Waiting for the import worker. Your upload is saved.','في انتظار معالجة الاستيراد. تم حفظ الملف المرفوع.')
     :live?.status==='processing'
      ?w('Saving records and relationships in small batches.','جارٍ حفظ السجلات والعلاقات على دفعات صغيرة.')
      :complete
       ?w('All source batches have been processed. Review the saved and pending counts below.','تمت معالجة جميع دفعات المصدر. راجع أعداد السجلات المحفوظة والمعلّقة أدناه.')
       :live?.status==='failed'
        ?w('Processing paused. Previously committed records are preserved.','توقفت المعالجة. تم الاحتفاظ بالسجلات المحفوظة سابقاً.')
        :w('Reading saved import progress…','جارٍ قراءة تقدم الاستيراد المحفوظ…');

 const previewStats:[string,string,number|undefined][]=[
  ['Total source rows','إجمالي صفوف المصدر',actualNumber(live?.total_rows)],
  ['Unique suppliers','الموردون الفريدون',actualNumber(live?.total_supplier_records_identified)],
  ['New suppliers (est.)','الموردون الجدد (تقديري)',actualNumber(live?.new_suppliers_estimated)],
  ['Existing suppliers (est.)','الموردون الحاليون (تقديري)',actualNumber(live?.existing_suppliers_estimated)],
  ['Contacts detected','جهات الاتصال المكتشفة',actualNumber(live?.contacts_detected)],
  ['Disciplines detected','التخصصات المكتشفة',actualNumber(live?.disciplines_detected)],
  ['Main categories','الفئات الرئيسية',actualNumber(live?.categories_detected)],
  ['Subcategories','الفئات الفرعية',actualNumber(live?.subcategories_detected)],
  ['Material values','قيم المواد',actualNumber(live?.material_values_detected)],
  ['Existing materials matched','المواد المطابقة',actualNumber(live?.existing_materials_matched)],
  ['Unresolved materials','المواد غير المطابقة',actualNumber(live?.unresolved_material_mappings)],
  ['Brands detected','العلامات التجارية',actualNumber(live?.brands_detected)],
  ['Duplicate source rows','الصفوف المكررة',actualNumber(live?.duplicate_source_rows)],
  ['Valid rows','الصفوف الصالحة',actualNumber(live?.valid_rows)],
  ['Review-required rows','صفوف تتطلب مراجعة',actualNumber(live?.review_required_rows)],
  ['Invalid rows','الصفوف غير الصالحة',actualNumber(live?.invalid_rows)],
 ];

 const stats:[string,string,number|undefined][] = kind==='suppliers'?[
  ['Source rows processed','صفوف المصدر المعالجة',actualNumber(live?.processed_rows)],
  ['Unique suppliers','الموردون الفريدون',actualNumber(live?.total_supplier_records_identified)],
  ['New suppliers saved','الموردون الجدد المحفوظون',actualNumber(live?.new_suppliers_saved)],
  ['Existing suppliers reused','الموردون المعاد استخدامهم',actualNumber(live?.existing_suppliers_reused)],
  ['Contacts created','جهات الاتصال المنشأة',actualNumber(live?.contacts_created)],
  ['Contacts updated','جهات الاتصال المحدّثة',actualNumber(live?.contacts_updated)],
  ['Capabilities created','القدرات المنشأة',actualNumber(live?.capabilities_saved)],
  ['Material mappings created','تعيينات المواد المنشأة',actualNumber(live?.material_mappings_created)],
  ['Pending mappings','التعيينات المعلّقة',pending],
  ['Failed rows','الصفوف المتعذرة',actualNumber(live?.failed_rows)],
 ]:[
  ['Source rows','صفوف المصدر',actualNumber(live?.total_rows)],
  ['Saved','تم الحفظ',actualNumber(live?.imported)],
  ['Skipped','تم التخطي',actualNumber(live?.skipped)],
  ['Failed rows','الصفوف المتعذرة',actualNumber(live?.failed)],
 ];

 const extra:[string,string,number|undefined][]=[
  ['Contact-category assignments','تعيينات جهات الاتصال والفئات',actualNumber(live?.category_assignments_created)],
  ['Capabilities reused','القدرات المعاد استخدامها',actualNumber(live?.capabilities_reused)],
  ['Categories created','الفئات المنشأة',actualNumber(live?.categories_created)],
  ['Materials created','المواد المنشأة',actualNumber(live?.materials_created)],
  ['Employee responsibilities saved','مسؤوليات الموظفين المحفوظة',actualNumber(live?.responsibilities_created)],
  ['Pending categories','الفئات المعلّقة',pendingCategories],
  ['Pending materials','المواد المعلّقة',pendingMaterials],
  ['Supplier failures','الموردون المتعذر استيرادهم',failures],
 ];

 const canRetry=kind==='suppliers'&&!!jobId&&(live?.status==='failed'||(live?.status==='completed_with_errors'&&(failures??0)>0));
 const canCancel=kind==='suppliers'&&!!jobId&&(active||live?.status==='preview_ready');
 const reportId=live?.batch_id||jobId;

 function issuesPanel(issues:Issue[],warning:boolean){
  return <details className={`admin-issues${warning?'':' is-error'}`}>
   <summary><span><IconAlertTriangle size={17}/>{warning?w('Warnings & review items','التحذيرات وعناصر المراجعة'):w('Failed rows','الصفوف المتعذرة')} <b>{number(issues.length)}</b></span><IconChevronDown size={17}/></summary>
   <div>
    <p>{w('Showing the row messages returned by the import service.','تُعرض رسائل الصفوف التي أعادتها خدمة الاستيراد.')}</p>
    {reportId&&<div className="admin-download-actions">
     <DownloadAction path={`imports/bulk/suppliers/jobs/${reportId}/review-report`} filename={`supplier-review-report-${reportId}.xlsx`} onError={setError}>{w('Download Review Rows','تنزيل صفوف المراجعة')}</DownloadAction>
     <DownloadAction path={warning?`imports/bulk/suppliers/${reportId}/warnings`:`imports/bulk/${reportId}/errors`} filename={warning?'supplier-warnings.xlsx':'import-errors.xlsx'} onError={setError}>{warning?w('Download warnings','تنزيل التحذيرات'):w('Download Failed Rows','تنزيل الصفوف المتعذرة')}</DownloadAction>
    </div>}
    <ul>{issues.slice(0,20).map((issue,index)=><li key={`${issue.row}-${index}`}><span className="admin-issue-row">{w('Row','الصف')} {number(actualNumber(issue.row))}</span><span>{issue.company_name&&<strong>{issue.company_name}: </strong>}{issue.reason}{issue.field&&<small> ({issue.field})</small>}{issue.sheet&&<small> · {issue.sheet}</small>}</span><span className="admin-issue-severity">{warning?w('Review','مراجعة'):w('Error','خطأ')}</span></li>)}</ul>
    {issues.length>20&&<details className="admin-more-issues"><summary>{w(`Show the remaining ${issues.length-20} reported messages`,`عرض الرسائل المتبقية (${issues.length-20})`)}</summary><ul>{issues.slice(20).map((issue,index)=><li key={`${issue.row}-${index}`}><span className="admin-issue-row">{w('Row','الصف')} {number(actualNumber(issue.row))}</span><span>{issue.reason}</span><span className="admin-issue-severity">{warning?w('Review','مراجعة'):w('Error','خطأ')}</span></li>)}</ul></details>}
   </div>
  </details>;
 }

 return <AdminModal open={open} normalize={false} busy={posting} preventDismiss={locked} onRequestClose={onClose} onClose={onClose} className="bulk-upload-dialog" aria-labelledby={`bulk-${kind}-title`} aria-describedby={`bulk-${kind}-description`}>
  <ModalHeader onClose={onClose} disabled={posting}>
   <h2 id={`bulk-${kind}-title`}>{title}</h2>
   <p id={`bulk-${kind}-description`}>{subtitle}</p>
  </ModalHeader>
  <ModalBody>
   {kind==='suppliers'&&<section className="admin-download-section">
    <div className="admin-download-actions">
     <DownloadAction path="imports/bulk/suppliers/template" filename="suppliers-template.xlsx" onError={setError} disabled={posting}>{w('Download Excel Template','تنزيل نموذج Excel')}</DownloadAction>
     {!live&&!recovering&&<DownloadAction path="imports/bulk/suppliers/export" filename="suppliers-with-contacts.xlsx" onError={setError} disabled={posting}>{w('Export suppliers','تصدير الموردين')}</DownloadAction>}
     {history.length>0&&<Button type="button" variant="ghost" size="sm" onClick={()=>setHistoryOpen(v=>!v)}><IconHistory size={16}/>{w(`Import History (${history.length})`,`سجل الاستيراد (${history.length})`)}</Button>}
    </div>
   </section>}

   {kind==='suppliers'&&historyOpen&&history.length>0&&<section className="admin-extra-details" aria-label={w('Import History','سجل الاستيراد')}>
    <ul style={{listStyle:'none',padding:0,margin:'0.5rem 0',display:'grid',gap:'0.4rem'}}>
     {history.map(item=><li key={item.batch_id} style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:'0.5rem',fontSize:'0.85rem',padding:'0.4rem 0.6rem',border:'1px solid var(--border,#e5e7eb)',borderRadius:'0.5rem'}}>
      <span><strong>{item.filename||w('Workbook','ملف')}</strong> · {item.status} ({number(actualNumber(item.total_rows))} {w('rows','صف')})</span>
      <span style={{display:'flex',gap:'0.4rem'}}>
       {item.batch_id&&<Button type="button" variant="ghost" size="sm" onClick={()=>{setJobId(item.batch_id);setLive(item);setHistoryOpen(false);}}>{w('View','عرض')}</Button>}
      </span>
     </li>)}
    </ul>
   </section>}

   {!live&&!recovering&&<>
    {kind==='materials'&&<section className="admin-download-section"><h3>{w('Prepare your workbook','إعداد الملف')}</h3><div className="admin-download-actions"><DownloadAction path="imports/bulk/materials/template" filename="material-template.xlsx" onError={setError} disabled={posting}>{w('Download Excel Template','تنزيل نموذج Excel')}</DownloadAction></div></section>}
    {kind==='suppliers'&&<label className="field" style={{marginBottom:'0.75rem'}}>
     <span>{w('Fallback Discipline (optional — used only when a row leaves Discipline blank)','التخصص الاحتياطي (اختياري — يُستخدم فقط عند ترك عمود التخصص فارغاً)')}</span>
     <select value={fallbackDiscipline} disabled={locked} onChange={e=>setFallbackDiscipline(e.target.value)}>
      <option value="">{w('None (detect from workbook rows)','بدون (استخدام قيم الملف)')}</option>
      <option value="Civil">{w('Civil','مدني')}</option>
      <option value="Electrical">{w('Electrical','كهربائي')}</option>
      <option value="Mechanical">{w('Mechanical','ميكانيكي')}</option>
     </select>
    </label>}
    <UploadDropzone file={file} disabled={locked} onSelect={select} onError={setError}/>
   </>}

   {(live||recovering)&&<div className="admin-import-file">
    <span><IconUpload size={18}/><strong>{file?.name||live?.filename||w('Active import','الاستيراد النشط')}</strong></span>
    {!locked&&<Button type="button" variant="ghost" size="sm" onClick={resetToNewUpload}>{w('Choose another file','اختيار ملف آخر')}</Button>}
   </div>}

   {posting&&!live&&<ProgressPanel status={phase==='uploading'?'uploading':phase==='retrying'?'processing':'validating'} stage={phase==='uploading'?w('Transferring your workbook securely.','جارٍ نقل الملف بأمان.'):phase==='retrying'?stage:w('Analyzing the workbook and preparing the preview.','جارٍ تحليل الملف وإعداد المعاينة.')} percent={phase==='uploading'?uploadPercent:undefined}/>}

   {(live||recovering)&&<>
    {live?.status==='preview_ready'&&<div className="admin-completion" role="status">
     <IconInfoCircle size={22}/>
     <div style={{width:'100%'}}>
      <strong>{w('Import preview ready','معاينة الاستيراد جاهزة')}</strong>
      <p>{w('No supplier records have been saved yet. Confirm to start the background import.','لم تُحفظ سجلات الموردين بعد. أكّد لبدء الاستيراد في الخلفية.')}</p>
      {live.batch_id&&<p className="admin-import-reference">{w('Import Job ID','معرّف المهمة')}: <bdi>{live.batch_id}</bdi></p>}
      <dl className="admin-stat-grid">{previewStats.map(([en,ar,value])=><div key={en}><dt>{w(en,ar)}</dt><dd>{number(value)}</dd></div>)}</dl>
     </div>
    </div>}

    {complete&&<div className={`admin-completion${needsReview?' needs-review':''}`} role="status">
     {needsReview?<IconAlertTriangle size={22}/>:<IconCheck size={22}/>}
     <div>
      <strong>{needsReview?w('Import completed with review items','اكتمل الاستيراد — يلزم المراجعة'):w('Import completed','اكتمل الاستيراد')}</strong>
      <p>{needsReview?w('Saved records are preserved. Download the review report to inspect unresolved mappings or failed rows.','تم الاحتفاظ بالسجلات المحفوظة. نزّل تقرير المراجعة لفحص التعيينات المعلّقة أو الصفوف المتعذرة.'):w('Your saved records are reflected in the summary below.','تظهر السجلات المحفوظة في الملخص أدناه.')}</p>
      {kind==='suppliers'&&reportId&&<div className="admin-download-actions" style={{marginTop:'0.5rem'}}>
       <DownloadAction path={`imports/bulk/suppliers/jobs/${reportId}/review-report`} filename={`supplier-review-report-${reportId}.xlsx`} onError={setError}>{w('Download Review Rows','تنزيل صفوف المراجعة')}</DownloadAction>
       {(failures??0)>0&&<DownloadAction path={`imports/bulk/${reportId}/errors`} filename="supplier-failed-rows.xlsx" onError={setError}>{w('Download Failed Rows','تنزيل الصفوف المتعذرة')}</DownloadAction>}
      </div>}
     </div>
    </div>}

    {cancelled&&<div className="admin-completion needs-review" role="status">
     <IconAlertTriangle size={22}/>
     <div>
      <strong>{w('Import cancelled','تم إلغاء الاستيراد')}</strong>
      <p>{live?.message||w('Future batches were stopped safely. Committed records are preserved.','تم إيقاف الدفعات القادمة بأمان مع الاحتفاظ بالسجلات المحفوظة.')}</p>
     </div>
    </div>}

    {live?.status!=='preview_ready'&&<ProgressPanel status={complete&&needsReview?'completed_with_errors':live?.status} stage={stage} percent={actualNumber(live?.progress_percent)} batches={actualNumber(live?.completed_batches)} totalBatches={actualNumber(live?.batches_total)}>
     {live&&<dl className="admin-stat-grid">{stats.map(([en,ar,value])=><div key={en}><dt>{w(en,ar)}</dt><dd>{number(value)}</dd></div>)}</dl>}
    </ProgressPanel>}

    {live&&live.status!=='preview_ready'&&<details className="admin-extra-details">
     <summary>{w('More import details','تفاصيل إضافية للاستيراد')}<IconChevronDown size={16}/></summary>
     <dl className="admin-stat-grid">{extra.filter(([, ,value])=>value!==undefined).map(([en,ar,value])=><div key={en}><dt>{w(en,ar)}</dt><dd>{number(value)}</dd></div>)}</dl>
     {live.batch_id&&<p className="admin-import-reference">{w('Import Job ID','معرّف المهمة')}: <bdi>{live.batch_id}</bdi></p>}
    </details>}

    {kind==='suppliers'&&!!live?.warnings?.length&&issuesPanel(live.warnings,true)}
    {!!live?.issues?.length&&issuesPanel(live.issues,false)}
    {!live?.issues?.length&&(failures??0)>0&&reportId&&<DownloadAction path={`imports/bulk/${reportId}/errors`} filename="import-errors.xlsx" onError={setError}>{w('Download Failed Rows','تنزيل الصفوف المتعذرة')}</DownloadAction>}
   </>}

   {error&&<div className="admin-import-error" role="alert"><IconAlertTriangle size={19}/><p>{error}</p></div>}
   {active&&<p className="admin-import-hint"><IconInfoCircle size={16}/>{w('The import continues safely in the background when you close this dialog.','يستمر الاستيراد بأمان في الخلفية عند إغلاق هذه النافذة.')}</p>}
  </ModalBody>
  <ModalFooter>
   <Button type="button" variant="outline" disabled={posting} onClick={onClose}>{w('Close','إغلاق')}</Button>
   {jobId&&kind==='suppliers'&&active&&<Button type="button" variant="outline" disabled={posting} onClick={()=>setPollVersion(v=>v+1)}><IconRefresh size={16}/>{w('Refresh progress','تحديث التقدم')}</Button>}
   {canCancel&&<Button type="button" variant="outline" disabled={posting} onClick={()=>void cancelJob()}><IconPlayerStop size={16}/>{w('Cancel Import','إلغاء الاستيراد')}</Button>}
   {(complete||cancelled)&&kind==='suppliers'&&<Button type="button" variant="outline" disabled={posting} onClick={resetToNewUpload}>{w('Start New Import','بدء استيراد جديد')}</Button>}
   {live?.status==='preview_ready'&&<Button type="button" disabled={posting} onClick={()=>void startPreview()}>{posting?<IconLoader2 size={17} className="admin-spin"/>:<IconUpload size={17}/>} {w('Start Import','بدء الاستيراد')}</Button>}
   {canRetry
    ?<Button type="button" disabled={posting||active} onClick={()=>void retry()}>{posting?<IconLoader2 size={17} className="admin-spin"/>:<IconRefresh size={17}/>} {w('Retry Failed Batches','إعادة محاولة الدفعات المتعذرة')}</Button>
    :!live&&<Button type="button" disabled={!file||locked} onClick={()=>void submit()}>{posting?<IconLoader2 size={17} className="admin-spin"/>:<IconUpload size={17}/>} {kind==='suppliers'?(posting?w('Analyzing…','جارٍ التحليل…'):w('Preview Import','معاينة الاستيراد')):(posting?w('Starting import…','جارٍ بدء الاستيراد…'):w('Start Import','بدء الاستيراد'))}</Button>}
  </ModalFooter>
 </AdminModal>;
}
