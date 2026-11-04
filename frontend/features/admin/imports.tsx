"use client";
import {useLocale} from '@/lib/locale';

import { DragEvent, useEffect, useRef, useState } from 'react';
import { IconCloudUpload, IconDownload, IconFileSpreadsheet } from '@tabler/icons-react';
import { api, upload } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { DataTable } from '@/features/shared/ui';
import { useToast } from '@/features/shared/toast';

type Preview = {
  batch_id?: string; status: string; discipline?: string | null; sheets?: {name:string;eligible:boolean;row_count:number;reason?:string|null}[];
  selected_sheet?: string; headers?: string[]; mapping?: Record<string,string>; rows?: number; valid?: number; invalid?: number;
  create?: number; update?: number; unchanged?: number; duplicate_rows?: number;
  created?: number; updated_count?: number; unchanged_count?: number; categories_created?: number;
  errors?: number;
  completed_rows?: number; progress_percent?: number;
  stage?: string; failure_reason?: string | null;
  new_categories?: {path:string;depth:number}[];
  sample?: Record<string,unknown>[]; preview?: Record<string,unknown>[]; issues?: Record<string,unknown>[]; warnings?: Record<string,unknown>[];
  dry_run?: boolean;
};
type MappingProfile = { name: string; mapping: Record<string,string> };

export function ImportUpload({path,open,onClose,onDone}:{path:string;open:boolean;onClose:()=>void;onDone?:()=>void}) {
 const {t}=useLocale();
  const materialImport = path === 'imports/materials';
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file,setFile] = useState<File>();
  const [result,setResult] = useState<Preview>();
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [uploadProgress,setUploadProgress] = useState<number|null>(null);
  const [validating,setValidating] = useState(false);
  const [confirmed,setConfirmed] = useState(false);
  const [previewed,setPreviewed] = useState(false);
  const [discipline,setDiscipline] = useState('');
  const [sheetName,setSheetName] = useState('');
  const [mapping,setMapping] = useState<Record<string,string>>({});
  const [profiles,setProfiles] = useState<MappingProfile[]>([]);
  const [dragging,setDragging] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(()=>{
    if(open&&!dialog.current?.open)dialog.current?.showModal();
    else if(!open&&dialog.current?.open)dialog.current.close();
  },[open]);

  useEffect(()=>{
    try {
      const stored=localStorage.getItem(`procura.import-mappings.v1:${path}`);
      const parsed=stored?JSON.parse(stored):[];
      setProfiles(Array.isArray(parsed)?parsed.filter((item):item is MappingProfile=>
        typeof item?.name==='string' && item.mapping && typeof item.mapping==='object' && !Array.isArray(item.mapping)
      ):[]);
    } catch { setProfiles([]); }
  },[path]);

  useEffect(()=>{
    if(!materialImport||!result?.batch_id||!['uploaded','processing'].includes(result.status))return;
    let cancelled=false;let timer:number;
    const poll=async()=>{
      try{
        const next=await api<Preview>(`imports/materials/${result.batch_id}`);
        if(cancelled)return;
        setResult(next);
        if(next.status==='preview')setPreviewed(true);
        else if(!['uploaded','processing'].includes(next.status))setPreviewed(false);
        else timer=window.setTimeout(poll,1000);
      }catch(e){
        if(cancelled)return;
        const message=e instanceof Error?e.message:'Could not read import progress';
        setError(message);timer=window.setTimeout(poll,2500);
      }
    };
    timer=window.setTimeout(poll,600);
    return()=>{cancelled=true;window.clearTimeout(timer);};
  },[materialImport,result?.batch_id,result?.status]);

  function storeProfiles(next:MappingProfile[]) {
    setProfiles(next);
    try { localStorage.setItem(`procura.import-mappings.v1:${path}`,JSON.stringify(next)); }
    catch { toast.error('Could not save mapping profile','Browser storage is unavailable or full.'); }
  }

  function saveProfile() {
    const name=window.prompt('Name this reusable column mapping');
    if(!name?.trim())return;
    const selected=Object.fromEntries(Object.entries({...result?.mapping,...mapping}).filter(([,value])=>typeof value==='string'&&value.trim()));
    if(!Object.keys(selected).length){toast.info('No mapping to save','Validate a workbook and select its column mapping first.');return;}
    storeProfiles([...profiles.filter(profile=>profile.name!==name.trim()),{name:name.trim().slice(0,60),mapping:selected}]);
    toast.success('Mapping profile saved','Only field-to-column names are stored in this browser; workbook rows are not saved.');
  }

  function applyProfile(name:string) {
    const profile=profiles.find(item=>item.name===name);
    if(!profile)return;
    const headers=new Set(result?.headers||[]);
    const applicable=Object.fromEntries(Object.entries(profile.mapping).filter(([,header])=>headers.has(header)));
    if(!Object.keys(applicable).length){toast.error('Mapping profile does not match','Choose a profile created for this workbook structure.');return;}
    setMapping(applicable);setPreviewed(false);setConfirmed(false);
    toast.info('Mapping profile applied','Validate the workbook again to review the result.');
  }

  function choose(next?:File) {
    setFile(undefined); setResult(undefined); setConfirmed(false); setPreviewed(false); setMapping({}); setError(''); setSheetName(''); setDiscipline('');
    if (!next) return;
    if (next.size > 8_000_000) { setError('Maximum file size is 8 MB'); toast.error('File is too large','Choose an XLSX or CSV up to 8 MB.'); return; }
    if (!/\.(xlsx|csv)$/i.test(next.name)) { setError('Choose an .xlsx or .csv file'); toast.error('Unsupported file','Choose an XLSX or CSV workbook.'); return; }
    setFile(next);
  }

  function payload() {
    const body = new FormData();
    if (file) body.set('file',file);
    if (discipline) body.set('discipline',discipline);
    if (sheetName) body.set('sheet_name',sheetName);
    const selected = Object.fromEntries(Object.entries(mapping).filter(([,v])=>v));
    if (Object.keys(selected).length) body.set('column_mapping',JSON.stringify(selected));
    return body;
  }

  async function preview() {
    if ((!file&&!result?.batch_id) || busy) return;
    setBusy(true); setError(''); setResult(undefined); setConfirmed(false);
    setUploadProgress(0); setValidating(false);
    try {
      const needsOptions=materialImport&&!!result?.batch_id&&['needs_review','needs_discipline','needs_sheet_selection','failed'].includes(result.status);
      let next:Preview;
      if(needsOptions){
        const body=new FormData();if(discipline)body.set('discipline',discipline);if(sheetName)body.set('sheet_name',sheetName);
        const selected=Object.fromEntries(Object.entries(mapping).filter(([,v])=>v));if(Object.keys(selected).length)body.set('column_mapping',JSON.stringify(selected));
        next=await api<Preview>(`imports/materials/${result.batch_id}/preview-options`,{method:'POST',body});
      }else{
        next = await upload<Preview>(materialImport?'imports/materials/preview':`${path}?dry_run=true&column_mapping=${encodeURIComponent(JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([,v])=>v))))}`,materialImport?payload():supplierPayload(),setUploadProgress,()=>setValidating(true));
      }
      setResult(next); setPreviewed(true);
      if (materialImport && next.status === 'preview') toast.success('Import preview ready',`${next.valid ?? 0} valid rows are ready for review.`);
      else if (materialImport) toast.info('Workbook staged','The server is processing your private upload. Progress will update here automatically.');
      else if(next.status==='needs_sheet_selection')toast.info('Choose a worksheet','Select the supplier data worksheet, then validate again.');
      else toast.success('Validation complete',`${next.rows ?? 0} spreadsheet rows were checked.`);
    } catch(e) {
      const message=e instanceof Error?e.message:'Import preview failed'; setError(message); toast.error('Could not validate spreadsheet',message);
    } finally { setBusy(false); setUploadProgress(null); setValidating(false); }
  }

  function supplierPayload() { const body=new FormData(); if(file)body.set('file',file); if(sheetName)body.set('sheet_name',sheetName); return body; }

  async function confirm() {
    if (busy || confirmed) return;
    if (!materialImport) {
      if (!result?.batch_id || result.status!=='preview' || !previewed || !result.valid) return;
      if (!window.confirm('Import the validated rows? Imported supplier contacts will not be messaged.')) return;
      setBusy(true); setError('');
      try {
        const outcome=await api<Preview>(`imports/suppliers/${result.batch_id}/confirm`,{method:'POST'});
        setResult(outcome); setConfirmed(true); onDone?.();
        toast.success('Spreadsheet import complete',`${outcome.created??0} companies created, ${outcome.unchanged??0} rows unchanged, ${outcome.errors??0} rows need review. No messages were sent.`);
      } catch(e) { const message=e instanceof Error?e.message:'Import could not be completed';setError(message);toast.error('Import was not completed',message); }
      finally { setBusy(false); }
      return;
    }
    if (!result?.batch_id) return;
    if (!window.confirm(`Create ${result.create ?? 0} new materials and leave ${result.unchanged ?? 0} exact matches unchanged? Invalid rows will be retained in the error report.`)) return;
    setBusy(true); setError('');
    try {
      const batchId=result.batch_id;
      const outcome=await api<{status:string;created:number;updated:number;unchanged:number;errors:number;categories_created:number}>(`imports/materials/${batchId}/confirm`,{method:'POST'});
      if(outcome.status==='queued'||outcome.status==='processing'){
        setResult({...result,status:outcome.status});
        toast.info('Import queued','Large workbooks are processed in the background. You can keep using the platform while progress updates.');
        onDone?.();
        for(let attempt=0;attempt<240;attempt++){
          await new Promise(resolve=>window.setTimeout(resolve,1500));
          const progress=await api<Preview>(`imports/materials/${batchId}`);
          setResult(progress);
          if(['completed','completed_with_errors','failed'].includes(progress.status)){
            setConfirmed(progress.status!=='failed');
            if(progress.status==='failed'){
              const message='The import stopped safely. Download its error report and review the affected rows before retrying.';
              setError(message);toast.error('Import needs attention',message);
            }else toast.success('Material import complete',`${progress.created??0} created, ${progress.updated_count??0} updated, ${progress.unchanged_count??0} unchanged, ${progress.invalid??0} invalid.`);
            onDone?.();
            return;
          }
        }
        toast.info('Import is still running','Progress remains saved. You can safely leave this page and check the import history later.');
        return;
      }
      setConfirmed(true);
      toast.success('Material import complete',`${outcome.created} created, ${outcome.updated} updated, ${outcome.unchanged} unchanged, ${outcome.errors} needs review.`);
      onDone?.();
      const status=await api<Preview>(`imports/materials/${batchId}`);
      setResult(status);
    } catch(e) {
      const message=e instanceof Error?e.message:'Import could not be confirmed'; setError(message); toast.error('Import was not completed',message);
    } finally { setBusy(false); }
  }

  function drop(event:DragEvent<HTMLLabelElement>) { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files[0]); }

  function downloadErrors() {
    const rows=result?.issues||[];
    if(!rows.length)return;
    const quote=(v:unknown)=>`"${String(v??'').replaceAll('"','""')}"`;
    const csv=['row,reason',...rows.map(r=>`${quote(r.row)},${quote(r.reason)}`)].join('\r\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
    const anchor=document.createElement('a'); anchor.href=url; anchor.download='material-import-errors.csv'; anchor.click(); URL.revokeObjectURL(url);
    toast.info('Error report downloaded',`${rows.length} row issues included.`);
  }

  return <dialog ref={dialog} className="upload-dialog import-upload-dialog" aria-labelledby="spreadsheet-upload-title" onCancel={event=>{event.preventDefault();onClose();}} onClick={event=>{if(event.target===event.currentTarget)onClose();}}>
    <div className="upload-dialog-body live-form import-panel procurement-workflow">
    <div className="panel-title"><div><h2 id="spreadsheet-upload-title">{materialImport?t('Import materials'):t('Spreadsheet import')}</h2><p>{materialImport?t('Upload Civil, Electrical or Mechanical materials. Review the four fields, then confirm.'):t('CSV or XLSX, maximum 8 MB. Preview before importing.')}</p></div><Button type="button" variant="outline" onClick={onClose}>{t("Close")}</Button></div>
    {materialImport&&<div className="import-note"><strong>{t("Source safe import")}</strong><span>{t("Reference and suggested worksheets are excluded. The import does not execute workbook formulas or send notifications. Categories and materials are created only after you confirm.")}</span></div>}
    {materialImport&&<label className="field import-discipline">{t("Workbook type (optional)")}<select value={discipline} disabled={busy} onChange={e=>{setDiscipline(e.target.value);setPreviewed(false);setConfirmed(false);}}><option value="">{t("Detect from workbook or choose…")}</option><option value="Civil">{t("Civil")}</option><option value="Electrical">{t("Electrical")}</option><option value="Mechanical">{t("Mechanical")}</option></select></label>}
    <label className={`import-drop${dragging?' is-dragging':''}`} onDragOver={e=>{e.preventDefault();setDragging(true);}} onDragLeave={()=>setDragging(false)} onDrop={drop}>
      <input ref={input} type="file" accept=".csv,.xlsx" disabled={busy} onChange={e=>choose(e.target.files?.[0])}/>
      <span className="import-drop-icon"><IconCloudUpload size={23}/></span><strong>{file?.name||t('Drop a workbook here or browse files')}</strong>
      <small>{file?`${(file.size/1024).toFixed(0)} KB · Ready to validate`:t('XLSX or UTF-8 CSV · Maximum 8 MB')}</small>
      <span className="import-browse">{t("Browse files")}</span>
    </label>
    {materialImport&&result?.sheets?.some(s=>s.eligible)&&result?.status!=='preview'&&<label className="field">{t("Material worksheet")}<select value={sheetName||result?.selected_sheet||''} onChange={e=>{setSheetName(e.target.value);setPreviewed(false);}}><option value="">{t("Choose active material worksheet…")}</option>{result?.sheets?.filter(s=>s.eligible).map(s=><option key={s.name} value={s.name}>{s.name} · {s.row_count} {t("rows")}</option>)}</select><small>{t("Reference and suggested classification worksheets are not selectable.")}</small></label>}
    {materialImport&&!!result?.headers?.length&&<details className="import-mapping"><summary>{t("Adjust detected columns (optional)")}</summary><div className="form-grid">{['main_category','subcategory','material_name','unit'].map(key=><label className="field" key={key}>{key.replaceAll('_',' ')}<select value={mapping[key]||result?.mapping?.[key]||''} onChange={e=>{setMapping({...mapping,[key]:e.target.value});setPreviewed(false);setConfirmed(false);}}><option value="">{t("Not provided")}</option>{result?.headers?.map(h=><option value={h} key={h}>{h}</option>)}</select></label>)}</div></details>}
    {!materialImport&&result?.status==='needs_sheet_selection'&&<label className="field">{t("Supplier worksheet")}<select value={sheetName} onChange={e=>{setSheetName(e.target.value);setResult(undefined);setPreviewed(false);}}><option value="">{t("Choose supplier data worksheet…")}</option>{result.sheets?.filter(sheet=>sheet.eligible).map(sheet=><option value={sheet.name} key={sheet.name}>{sheet.name} · {sheet.row_count} {t("rows")}</option>)}</select><small>{t("Reference and expenditure worksheets are excluded.")}</small></label>}
    {!materialImport&&!!result?.headers?.length&&!path.startsWith('pricebooks')&&<fieldset className="import-mapping"><legend>{t("Column mapping")}</legend><div className="form-grid">{(path.endsWith('materials')?['material_code','material_name','category_code','unit']:['supplier_name','trade','contact_name','phone','email','city','brands','website']).map(key=><label className="field" key={key}>{key.replaceAll('_',' ')}<select value={mapping[key]||result?.mapping?.[key]||''} onChange={e=>{setMapping({...mapping,[key]:e.target.value});setPreviewed(false);setConfirmed(false);}}><option value="">{t("Use detected column")}</option>{result?.headers?.map(h=><option value={h} key={h}>{h}</option>)}</select></label>)}</div></fieldset>}
    {!!result?.headers?.length&&<div className="live-actions"><label className="field">{t("Saved mapping profiles")}<select value="" onChange={e=>applyProfile(e.target.value)}><option value="">{t("Choose a saved profile")}</option>{profiles.map(profile=><option key={profile.name} value={profile.name}>{profile.name}</option>)}</select></label><Button type="button" variant="outline" onClick={saveProfile}>{t("Save mapping profile")}</Button>{profiles.length>0&&<Button type="button" variant="outline" onClick={()=>{const name=window.prompt('Enter the saved profile name to remove');if(name)storeProfiles(profiles.filter(profile=>profile.name!==name));}}>{t("Remove profile")}</Button>}</div>}
    <div className="live-actions"><Button type="button" disabled={busy||(!file&&!result?.batch_id)||(!!result?.batch_id&&!['needs_review','needs_discipline','needs_sheet_selection','failed'].includes(result.status))} onClick={()=>void preview()}>{busy?t('Uploading…'):result?.batch_id?t('Recheck preview'):t('Upload and analyze')}</Button>{materialImport&&<Button type="button" variant="outline" disabled={!result?.batch_id||result.status!=='preview'||!previewed||busy||confirmed||!result.valid} onClick={()=>void confirm()}>{busy?t('Importing…'):t('Confirm material import')}</Button>}</div>
    {busy&&uploadProgress!==null&&<div className={`category-upload-status${validating?' is-validating':''}`} role="status" aria-live="polite"><div className="category-upload-copy"><span className="category-upload-orb"><i/></span><div><strong>{validating?t('Workbook received'):t('Uploading workbook')}</strong><small>{validating?t('Staging a private copy and starting background worksheet analysis…'):t('Keep this window open while your workbook uploads.')}</small></div><b>{validating?t('Starting…'):`${uploadProgress}%`}</b></div><div className={`category-upload-track${validating?' is-indeterminate':''}`}><i style={validating?undefined:{width:`${uploadProgress}%`}}/></div></div>}
    {(busy&&uploadProgress===null||materialImport&&!!result&&['uploaded','processing'].includes(result.status))&&<div className="import-progress" role="status" aria-live="polite"><i/><span>{result?.stage==='apply'?`Saving ${result.completed_rows??0} of ${result.rows??0} material rows (${result.progress_percent??0}%).`:result?.rows?`Analyzing ${result.completed_rows??0} of ${result.rows} worksheet rows…`:t('Workbook staged privately. Detecting worksheet structure and columns…')}</span></div>}
    {error&&<p className="import-error" role="alert">{error}</p>}
    {materialImport&&result&&<>
      <div className="import-summary" role="status"><IconFileSpreadsheet size={19}/><div><strong>{result.status==='preview'?t('Ready for confirmation'):result.status.replaceAll('_',' ')}</strong><small>{result.rows??0} {t("source rows ·")} {result.valid??0} {t("valid ·")} {result.invalid??0} {t("invalid ·")} {result.duplicate_rows??0} {t("duplicate")}</small></div><span>{result.discipline||t('Material Master')}{result.selected_sheet?` · ${result.selected_sheet}`:''}</span></div>
      {result.status==='needs_discipline'&&<p className="notice">{t("The workbook does not identify a single discipline. Select Civil, Electrical or Mechanical above, then validate again.")}</p>}
      {result.status==='needs_sheet_selection'&&<p className="notice">{t("More than one material worksheet was detected. Choose the active material data worksheet above; suggested/reference sheets remain excluded.")}</p>}
      {result.status==='needs_review'&&<p className="notice warn">{t("No eligible material worksheet was detected. Confirm the workbook contains a description column and a main category or subcategory column.")}</p>}
      {result.status==='failed'&&<p className="notice warn" role="alert">{result.failure_reason||t('The import worker could not process this workbook. Correct the source or preview settings, then retry.')}</p>}
      {!!result.sample?.length&&<><h3>{t("Material row and category preview")}</h3><DataTable rows={result.sample} columns={['category','subcategory','name','unit']} searchable={false} labels={{category:'Main category',subcategory:'Subcategory',name:'Description',unit:'Unit'}}/></>}
      {!!result.new_categories?.length&&<><h3>{t("Categories that will be added after confirmation")}</h3><DataTable rows={result.new_categories} columns={['path']} searchable={false} labels={{path:'Category / Subcategory'}}/></>}
      {!!result.warnings?.length&&<><h3>{t("Duplicate and unchanged rows")}</h3><DataTable rows={result.warnings} columns={['row','reason']}/></>}
      {!!result.issues?.length&&<div className="import-issues-head"><h3>{t("Rows needing review")}</h3><Button type="button" variant="outline" onClick={downloadErrors}><IconDownload size={15}/>{t("Download CSV")}</Button></div>}
      {!!result.issues?.length&&<DataTable rows={result.issues.slice(0,100)} columns={['row','reason']}/ >}
    </>}
    {!materialImport&&<div className="live-actions"><Button type="button" variant="outline" disabled={!result?.batch_id||result.status!=='preview'||!previewed||!result.valid||busy||confirmed} onClick={()=>void confirm()}>{busy?t('Importing…'):t('Confirm import')}</Button></div>}
    {!materialImport&&result&&<><p role="status">{confirmed?t('Import complete'):t('Preview complete')} {t("· Rows:")} {String(result.rows??0)} {t("· Created:")} {confirmed?String(result.created??0):'—'}</p>{Array.isArray(result.preview)&&<DataTable rows={result.preview} columns={['row','name','status','issue']}/>}</>}
    </div>
  </dialog>;
}
