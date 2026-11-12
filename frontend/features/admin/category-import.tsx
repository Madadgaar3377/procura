"use client";
import {AdminModal} from "@/features/shared/admin-modal";
import {adminConfirm} from "@/features/shared/admin-confirm";
import {useLocale} from '@/lib/locale';

import { useEffect, useRef, useState } from 'react';
import { IconCloudUpload, IconFileSpreadsheet } from '@tabler/icons-react';
import { Button } from '@/components/ui/button';
import { DataTable } from '@/features/shared/ui';
import { useToast } from '@/features/shared/toast';
import { api, upload } from '@/lib/api/client';

type Sheet = { name:string; eligible:boolean; row_count:number; headers:string[]; reason?:string|null };
type CategoryPreview = {
  batch_id?:string; status:string; sheets?:Sheet[]; selected_sheet?:string; headers?:string[];
  mapping?:Record<string,string>; rows?:number; valid?:number; invalid?:number; create?:number;
  unchanged?:number; duplicate_rows?:number; categories_to_create?:number;
  arabic_labels_to_add?:number; arabic_labels_added?:number;
  sample?:Record<string,unknown>[]; issues?:Record<string,unknown>[];
  categories_created?:number; errors?:number;
};
type Field = 'main_category'|'main_category_ar'|'subcategory'|'subcategory_ar'|'discipline';

export function CategoryImport({open,onClose,onDone}:{open:boolean;onClose:()=>void;onDone?:()=>void}) {
 const {t}=useLocale();
  const toast=useToast();
  const dialog=useRef<HTMLDialogElement>(null);
  const [file,setFile]=useState<File>();
  const [discipline,setDiscipline]=useState('');
  const [sheetName,setSheetName]=useState('');
  const [mapping,setMapping]=useState<Partial<Record<Field,string>>>({});
  const [result,setResult]=useState<CategoryPreview>();
  const [busy,setBusy]=useState(false);
  const [uploadProgress,setUploadProgress]=useState<number|null>(null);
  const [validating,setValidating]=useState(false);
  const [dragging,setDragging]=useState(false);
  const [error,setError]=useState('');
  const [confirmed,setConfirmed]=useState(false);

  useEffect(()=>{
    if(open&&!dialog.current?.open)dialog.current?.showModal();
    else if(!open&&dialog.current?.open)dialog.current.close();
  },[open]);

  function choose(next?:File) {
    setFile(undefined);setResult(undefined);setMapping({});setSheetName('');setConfirmed(false);setError('');setUploadProgress(null);setValidating(false);
    if(!next)return;
    if(next.size>8_000_000){setError('Maximum file size is 8 MB');toast.error('File is too large','Choose an XLSX or UTF-8 CSV up to 8 MB.');return;}
    if(!/\.(xlsx|csv)$/i.test(next.name)){setError('Choose an XLSX or CSV file');toast.error('Unsupported file','Choose an XLSX or UTF-8 CSV workbook.');return;}
    setFile(next);
  }

  async function preview() {
    if(!file||busy)return;
    setBusy(true);setError('');setConfirmed(false);
    setUploadProgress(0);setValidating(false);
    const body=new FormData();body.set('file',file);
    if(discipline)body.set('discipline',discipline);
    if(sheetName)body.set('sheet_name',sheetName);
    const selected=Object.fromEntries(Object.entries(mapping).filter(([,value])=>value));
    if(Object.keys(selected).length)body.set('column_mapping',JSON.stringify(selected));
    try {
      const next=await upload<CategoryPreview>('taxonomy/categories/import/preview',body,setUploadProgress,()=>setValidating(true));
      setResult(next);
      if(next.status==='needs_sheet_selection')toast.info('Choose a worksheet','Select the worksheet containing category hierarchy rows.');
      else if(next.status==='preview')toast.success('Category preview ready',`${next.valid??0} rows checked; ${next.categories_to_create??0} hierarchy records proposed.`);
      else toast.info('More information needed','Choose a discipline or update the detected column mapping, then validate again.');
    } catch(e) {
      const message=e instanceof Error?e.message:'Spreadsheet validation failed';setError(message);toast.error('Could not validate category sheet',message);
    } finally {setBusy(false);setUploadProgress(null);setValidating(false);}
  }

  async function confirm() {
    if(!result?.batch_id||result.status!=='preview'||!result.valid||busy||confirmed)return;
    if(!(await adminConfirm(`Create ${result.categories_to_create??0} missing category records and add ${result.arabic_labels_to_add??0} supplied Arabic labels?`)))return;
    setBusy(true);setError('');
    try {
      const outcome=await api<CategoryPreview>(`taxonomy/categories/import/${result.batch_id}/confirm`,{method:'POST'});
      setResult({...result,...outcome});setConfirmed(true);onDone?.();
      toast.success('Category hierarchy imported',`${outcome.categories_created??0} categories created; ${outcome.arabic_labels_added??0} supplied Arabic labels added; ${outcome.errors??0} rows need review.`);
    } catch(e) {
      const message=e instanceof Error?e.message:'Category import could not be confirmed';setError(message);toast.error('Category import not completed',message);
    } finally {setBusy(false);}
  }

  return <AdminModal ref={dialog} className="upload-dialog category-upload-dialog" aria-labelledby="category-upload-title" onCancel={event=>{event.preventDefault();onClose();}} onClick={event=>{if(event.target===event.currentTarget)onClose();}} open={open} busy={busy} onRequestClose={onClose}>
    <div className="upload-dialog-body live-form import-panel category-import-panel">
    <div className="panel-title"><div><h2 id="category-upload-title">{t("Upload a category hierarchy")}</h2><p>{t("Import discipline, main category, and subcategory rows without creating material records.")}</p></div><Button type="button" variant="outline" onClick={onClose}>{t("Close")}</Button></div>
    <div className="import-note"><strong>{t("Preview before saving")}</strong><span>{t("The importer detects worksheets and columns, skips reference sheets, reuses exact category paths, and writes only after confirmation.")}</span></div>
    <div className="form-grid">
      <label className="field">{t("Fallback discipline")}<select value={discipline} disabled={busy} onChange={event=>{setDiscipline(event.target.value);if(result?.status==='preview')setResult(undefined);setConfirmed(false);}}><option value="">{t("Detect from sheet")}</option><option value="Civil">{t("Civil")}</option><option value="Electrical">{t("Electrical")}</option><option value="Mechanical">{t("Mechanical")}</option></select></label>
      {result?.status==='needs_sheet_selection'&&<label className="field">{t("Category worksheet")}<select value={sheetName} disabled={busy} onChange={event=>setSheetName(event.target.value)}><option value="">{t("Choose a worksheet")}</option>{result.sheets?.filter(sheet=>sheet.eligible).map(sheet=><option key={sheet.name} value={sheet.name}>{sheet.name} · {sheet.row_count} {t("rows")}</option>)}</select></label>}
    </div>
    <label className={`import-drop${dragging?' is-dragging':''}`} onDragOver={event=>{event.preventDefault();if(!busy)setDragging(true);}} onDragLeave={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node|null))setDragging(false);}} onDrop={event=>{event.preventDefault();setDragging(false);if(!busy)choose(event.dataTransfer.files?.[0]);}}>
      <input type="file" accept=".xlsx,.csv" disabled={busy} onChange={event=>choose(event.target.files?.[0])}/>
      <span className="import-drop-icon"><IconCloudUpload size={22}/></span><strong>{file?.name||t('Drop a category workbook here or browse files')}</strong>
      <small>{file?`${(file.size/1024).toFixed(0)} KB · Ready to validate`:t('XLSX or UTF-8 CSV · Maximum 8 MB')}</small><span className="import-browse">{t("Browse files")}</span>
    </label>
    {!!result?.headers?.length&&<fieldset className="import-mapping"><legend>{t("Detected column mapping")}</legend><div className="form-grid">{(['main_category','main_category_ar','subcategory','subcategory_ar','discipline'] as Field[]).map(field=><label className="field" key={field}>{field.replaceAll('_',' ')}{field!=='main_category'?t(' (optional)'):''}<select value={mapping[field]??result.mapping?.[field]??''} disabled={busy} onChange={event=>{setMapping({...mapping,[field]:event.target.value});setConfirmed(false);}}><option value="">{field==='main_category'?t('Choose a column'):t('Not provided')}</option>{result.headers?.map(header=><option key={header} value={header}>{header}</option>)}</select></label>)}</div></fieldset>}
    <div className="live-actions"><Button type="button" disabled={!file||busy} onClick={()=>void preview()}>{busy?t('Processing…'):t('Validate and preview')}</Button><Button type="button" variant="outline" disabled={!result?.batch_id||result.status!=='preview'||!result.valid||busy||confirmed} onClick={()=>void confirm()}>{busy?t('Saving…'):t('Confirm category import')}</Button></div>
    {busy&&<div className={`category-upload-status${validating?' is-validating':''}`} role="status" aria-live="polite">
      <div className="category-upload-copy"><span className="category-upload-orb"><i/></span><div><strong>{validating?t('Workbook received'):t('Uploading workbook')}</strong><small>{validating?t('Checking worksheets, category paths, and existing records…'):t('Keep this window open while your workbook uploads.')}</small></div><b>{validating?t('Reviewing…'):`${uploadProgress??0}%`}</b></div>
      <div className={`category-upload-track${validating?' is-indeterminate':''}`}><i style={validating?undefined:{width:`${uploadProgress??0}%`}}/></div>
    </div>}
    {error&&<p className="import-error" role="alert">{error}</p>}
    {result&&<>
      <div className="import-summary" role="status"><IconFileSpreadsheet size={19}/><div><strong>{confirmed?t('Import complete'):result.status.replaceAll('_',' ')}</strong><small>{result.rows??0} {t("rows ·")} {result.valid??0} {t("valid ·")} {result.invalid??0} {t("invalid ·")} {result.duplicate_rows??0} {t("duplicate paths")}</small></div><span>{result.categories_created??result.categories_to_create??0} {t("categories")} {confirmed?t('created'):t('proposed')} · {result.arabic_labels_added??result.arabic_labels_to_add??0} {t("Arabic labels")}</span></div>
      {result.status==='needs_sheet_selection'&&<p className="notice">{t("Choose the active category worksheet, then validate again. Reference and lookup worksheets are not imported.")}</p>}
      {!!result.sample?.length&&<><h3>{t("Category hierarchy preview")}</h3><DataTable rows={result.sample} columns={['row','discipline','main_category','subcategory','status','issue']}/></>}
      {!!result.issues?.length&&<><h3>{t("Rows needing review")}</h3><DataTable rows={result.issues} columns={['row','reason']}/></>}
    </>}
    </div>
  </AdminModal>;
}
