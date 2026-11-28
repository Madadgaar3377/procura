"use client";
import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ApiError } from '@/lib/api/errors';
import { FieldError } from '@/features/shared/field-error';
import { api, json, upload } from '@/lib/api/client';
import {useServerList} from '@/features/shared/server-list';
import { useApi } from '@/lib/api/use-api';
import type { Schemas } from '@/lib/api/generated/schema';
import { useSession } from '@/features/auth/session';
import { Action, DataTable, Heading, State } from '@/features/shared/ui';
import {RFQMaterialImport} from './material-import';
import {GroupedMaterialEntry} from '@/features/shared/grouped-material-entry';
import { GroupedRfqMaterials, RfqExportActions } from './grouped-rfq-materials';
import { MaterialRow, type MaterialLine } from './material-row';
import { useLocale } from '@/lib/locale';
import { Button } from '@/components/ui/button';
import { FileUploadStatus } from '@/features/shared/file-upload-status';
import { Matching } from './matched-suppliers';
export { Matching };
type Line=Schemas['RFQLineIn'];
// Send only the API contract. Restored session drafts and import rows can carry
// extra UI fields, "" for an unset select, or null specifications.
const ref=(value:string|null|undefined)=>value&&value.trim()?value.trim():null;
function lineFieldErrors(errors:Record<string,string>){
 return Object.fromEntries(Object.entries(errors).map(([key,message])=>[key.replace(/^(items\.\d+)\.material_id$/,'$1.description'),message]));
}
export function toRfqLine(line:MaterialLine):Line{
 return {id:line.id,material_id:ref(line.material_id),main_category_id:ref(line.main_category_id),subcategory_id:ref(line.subcategory_id),description:(line.description||'').trim(),specifications:(line.specifications??'').trim(),unit:line.unit,quantity:String(line.quantity??'').trim()};
}
export function RFQList({role}:{role:string}){
 const {t}=useLocale();
 const {org}=useSession();const [status,setStatus]=useState('');const q=useServerList<Schemas['RFQOut']>(role==='admin'?`rfqs?status=${status}`:org?`rfqs?org_id=${org.id}&status=${status}`:null,'Search RFQs');const rows=q.rows;
 return <><Heading title={t("Requests for quotation")} description="Manage drafts and published procurement requests."/>{role==='buyer'&&<Link href="/app/buyer/rfqs/new" className="page-link">{t("Create RFQ \u2192")}</Link>}<label className="field live-selector">{t("RFQ status")}<select value={status} onChange={event=>setStatus(event.target.value)}>{["","draft","published","collecting_quotes","closed","cancelled"].map(value=><option key={value} value={value}>{t(value?value.replaceAll("_"," "):"All statuses")}</option>)}</select></label>{q.controls}<State {...q} empty={!q.loading&&!rows.length} retry={q.reload}/>{rows.length>0&&<DataTable paginate={false} rows={rows} columns={['reference','title','city','response_deadline','status']} action={r=><Link className="page-link" href={`/app/${role}/rfqs/${r.id}`}>{t("Open")}</Link>}/>}</>;
}
export function RFQWizard({id}:{id?:string}) {
 const {org}=useSession();
 return <RFQWizardForm key={`${org?.id||'none'}:${id||'new'}`} id={id}/>;
}
function RFQWizardForm({id}:{id?:string}) {
 const {org,user}=useSession();const {t,arabic}=useLocale();
 const [step,setStep]=useState(0),[title,setTitle]=useState(''),[city,setCity]=useState(''),[deadline,setDeadline]=useState('');
 const [projectName,setProjectName]=useState(''),[deliveryDate,setDeliveryDate]=useState(''),[instructions,setInstructions]=useState('');
 const [files,setFiles]=useState<File[]>([]);
 const [uploadProgress,setUploadProgress]=useState<{name:string;percent:number}|null>(null);
 const [retryPublish,setRetryPublish]=useState(true);
 const [groupDirty,setGroupDirty]=useState(false);
 const [showBatchByCategory,setShowBatchByCategory]=useState(false);
 const [importOpen,setImportOpen]=useState(false);
 const [fieldErrors,setFieldErrors]=useState<Record<string,string>>({});
 const [savedId,setSavedId]=useState(id);const [requestId,setRequestId]=useState('');const [pendingPublication,setPendingPublication]=useState(false);const initialized=useRef<string|undefined>(undefined);const submitting=useRef(false);
 const [items,setItems]=useState<MaterialLine[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const taxonomy=useApi<Schemas['CategoryOut'][]>('taxonomy/categories');
 const units=useApi<{code:string;name_en:string}[]>('taxonomy/units');
 const draft=useApi<Schemas['RFQDetail']>(id?`rfqs/${id}`:null);
 const localDraftKey=`procura:rfq-draft:v1:${user.id}:${org?.id||'none'}`;
 const [localRestored,setLocalRestored]=useState(false);
 useEffect(()=>{
  if(id||!org)return;
  let active=true;
  async function restore(){
   let checked=false;
   try{const saved=JSON.parse(sessionStorage.getItem(localDraftKey)||'null');
    if(saved&&Array.isArray(saved.items)&&saved.items.length<=500){
     let editableId:string|undefined;
     if(typeof saved.savedId==='string'&&saved.savedId){
      try{const existing=await api<Schemas['RFQDetail']>(`rfqs/${encodeURIComponent(saved.savedId)}`);
       if(existing.rfq.buyer_org_id===org!.id&&existing.rfq.status==='draft')editableId=existing.rfq.id;
       else if(existing.rfq.buyer_org_id===org!.id&&['published','collecting_quotes'].includes(existing.rfq.status)){
        sessionStorage.removeItem(localDraftKey);window.location.assign(`/app/buyer/matches?rfq=${encodeURIComponent(existing.rfq.id)}`);return;
       }
      }catch(cause){if(!(cause instanceof ApiError)||cause.status!==404)throw cause;}
     }
     if(!active)return;
     setItems(saved.items);setTitle(saved.title||'');setCity(saved.city||'');setDeadline(saved.deadline||'');setProjectName(saved.projectName||'');setDeliveryDate(saved.deliveryDate||'');setInstructions(saved.instructions||'');setSavedId(editableId);setRequestId(saved.requestId||crypto.randomUUID());setPendingPublication(false);
    }
    else setRequestId(crypto.randomUUID());
    checked=true;
   }catch{if(active)setError('Could not check the saved draft. Please retry before publishing.');}
   finally{if(active&&checked)setLocalRestored(true);}
  }
  void restore();return()=>{active=false};
 },[id,localDraftKey,org?.id]);
 useEffect(()=>{if(id||!localRestored)return;try{sessionStorage.setItem(localDraftKey,JSON.stringify({items,title,city,deadline,projectName,deliveryDate,instructions,savedId,requestId,pendingPublication}));}catch{}},[id,localRestored,localDraftKey,items,title,city,deadline,projectName,deliveryDate,instructions,savedId,requestId,pendingPublication]);
  useEffect(()=>{if(draft.data&&initialized.current!==id){initialized.current=id;const d=draft.data;setTitle(d.rfq.title);setProjectName(d.rfq.project_name||'');setDeliveryDate(d.rfq.required_delivery_date||'');setInstructions(d.rfq.additional_instructions||'');setCity(d.rfq.city);setDeadline(new Date(new Date(d.rfq.response_deadline).getTime()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16));setItems(d.items.map(i=>({key:i.id,id:i.id,material_id:i.material_id,main_category_id:i.main_category_id,subcategory_id:i.subcategory_id,description:i.description,specifications:i.specifications,unit:i.unit,quantity:i.quantity})));}},[draft.data]);
 async function save(publish:boolean){if(submitting.current||!org||(!id&&!localRestored)||draft.loading)return;setRetryPublish(publish);submitting.current=true;setBusy(true);setError('');try{
  const body:Schemas['RFQIn']={buyer_org_id:org.id,client_request_id:id?undefined:requestId,title,city,project_name:projectName,required_delivery_date:deliveryDate||null,additional_instructions:instructions,response_deadline:new Date(deadline).toISOString(),required_qualifications:draft.data?.rfq.required_qualifications||[],items:items.map(toRfqLine)};
  let editableId=savedId;
  if(editableId){
   try{const existing=await api<Schemas['RFQDetail']>(`rfqs/${encodeURIComponent(editableId)}`);
    if(existing.rfq.buyer_org_id!==org.id)throw new Error('Select the buyer organization that owns this RFQ.');
    if(existing.rfq.status!=='draft'){
     if(['published','collecting_quotes'].includes(existing.rfq.status)){sessionStorage.removeItem(localDraftKey);window.location.assign(`/app/buyer/matches?rfq=${encodeURIComponent(editableId)}`);return;}
     if(id)throw new Error('This RFQ is already published. Open its matching results.');
     editableId=undefined;setSavedId(undefined);
    }
   }catch(cause){if(cause instanceof ApiError&&cause.status===404&&!id){editableId=undefined;setSavedId(undefined);}else throw cause;}
  }
  let result=await api<Schemas['RFQDetail']>(editableId?`rfqs/${editableId}`:'rfqs',{method:editableId?'PUT':'POST',body:json(body)});
  if(['published','collecting_quotes'].includes(result.rfq.status)){sessionStorage.removeItem(localDraftKey);window.location.assign(`/app/buyer/matches?rfq=${encodeURIComponent(result.rfq.id)}`);return;}
  // A replayed create can return the server's earlier snapshot after the buyer
  // edits the form. Save the current form against that same draft ID.
  if(!editableId){result=await api<Schemas['RFQDetail']>(`rfqs/${result.rfq.id}`,{method:'PUT',body:json(body)});}
   const knownIds=new Set(items.map(item=>item.id).filter(Boolean));
   const created=result.items.filter(item=>!knownIds.has(item.id));
   let createdIndex=0;
   const persistedItems=items.map(item=>item.id?item:{...item,id:created[createdIndex++]?.id});
   setItems(persistedItems);
  setSavedId(result.rfq.id);
   try{sessionStorage.setItem(localDraftKey,JSON.stringify({items:persistedItems,title,city,deadline,projectName,deliveryDate,instructions,savedId:result.rfq.id,requestId,pendingPublication:false}));}catch{}
  for(const file of files){const body=new FormData();body.set('file',file);await upload(`rfqs/${result.rfq.id}/attachments`,body,percent=>setUploadProgress({name:file.name,percent}));setFiles(previous=>previous.filter(entry=>entry!==file));}
   if(publish){setPendingPublication(true);try{sessionStorage.setItem(localDraftKey,JSON.stringify({items:persistedItems,title,city,deadline,projectName,deliveryDate,instructions,savedId:result.rfq.id,requestId,pendingPublication:true}));}catch{}
   try{await api(`rfqs/${result.rfq.id}/publish`,{method:'POST'});}catch(cause){
    const existing=await api<Schemas['RFQDetail']>(`rfqs/${encodeURIComponent(result.rfq.id)}`);
    if(!['published','collecting_quotes'].includes(existing.rfq.status))throw cause;
   }
  }
  try{sessionStorage.removeItem(localDraftKey);}catch{}
  window.location.assign(publish?`/app/buyer/matches?rfq=${encodeURIComponent(result.rfq.id)}`:`/app/buyer/rfqs/${result.rfq.id}`);
 }catch(e){
  if(e instanceof ApiError&&e.status===409&&savedId){
   try{const current=await api<Schemas['RFQDetail']>(`rfqs/${encodeURIComponent(savedId)}`);
    if(['published','collecting_quotes'].includes(current.rfq.status)){sessionStorage.removeItem(localDraftKey);window.location.assign(`/app/buyer/matches?rfq=${encodeURIComponent(savedId)}`);return;}
   }catch{}
  }
  setError(e instanceof ApiError&&e.status===409?'This RFQ changed. Refresh its status before trying again.':e instanceof Error?e.message:'Could not save RFQ');if(e instanceof ApiError){setFieldErrors(lineFieldErrors(e.fieldErrors));if(Object.keys(e.fieldErrors).length)setStep(Object.keys(e.fieldErrors).some(key=>key.startsWith('items.'))?1:0);}
 }finally{submitting.current=false;setBusy(false);setUploadProgress(null);}}
 if(!org)return <State error={new Error('Create a buyer company before creating an RFQ.')}/>;
 if(id&&draft.data&&draft.data.rfq.buyer_org_id!==org.id)return <State error={new Error('Select the buyer organization that owns this RFQ.')}/>;
 const names=new Map((taxonomy.data||[]).map(c=>[c.id,c.name_en]));
 return <div className="procurement-workflow"><Heading title={id?t('Edit RFQ draft'):t('Create a request for quotation')} description="Choose materials and customize your request."/><State loading={draft.loading} error={draft.error}/><ol className="live-steps">{['Request details','Materials','Review'].map((s,i)=><li key={s} aria-current={step===i?'step':undefined}>{i+1}. {t(s)}</li>)}</ol><form className="panel live-form" onInvalidCapture={event=>{const input=event.target;if(input instanceof HTMLInputElement||input instanceof HTMLSelectElement||input instanceof HTMLTextAreaElement)setFieldErrors(previous=>({...previous,[input.name]:input.validity.rangeUnderflow?"Quantity must be greater than zero.":input.validity.valueMissing?"This field is required.":input.validationMessage}));}} onSubmit={e=>{e.preventDefault();if(step===1&&groupDirty){setError('Save the material group before continuing.');return;}if(!items.length&&step===1){setError('Add at least one material.');return;}const issues:Record<string,string>={};
 if(step===0){if(deliveryDate&&deadline&&deliveryDate<deadline.slice(0,10))issues.required_delivery_date='Required delivery date must not precede the quotation deadline';if(title.trim().length<3)issues.title='Enter an RFQ title with at least 3 characters.';if(!deadline||new Date(deadline).getTime()<=Date.now())issues.response_deadline='Deadline must be in the future.';}
 if(step===1)items.forEach((item,index)=>{const key=`items.${index}.`;if(!item.main_category_id)issues[key+'main_category_id']='Please select a main category.';if((taxonomy.data||[]).some(category=>category.parent_id===item.main_category_id)&&!item.subcategory_id)issues[key+'subcategory_id']='Please select a subcategory.';if(item.description.trim().length<3)issues[key+'description']='Enter a description with at least 3 characters.';if(!(units.data||[]).some(unit=>unit.code===item.unit))issues[key+'unit']='Please select an approved unit.';if(!Number.isFinite(Number(item.quantity))||Number(item.quantity)<=0)issues[key+'quantity']='Quantity must be greater than zero.';});
 setFieldErrors(issues);if(Object.keys(issues).length){setError('Check the highlighted fields.');return;}setError('');if(step<2)setStep(step+1);else void save(true);}}>
 {step===0&&<div className="form-grid"><label className="field">{t('RFQ title')}<input name="title" aria-invalid={Boolean(fieldErrors.title)} required minLength={3} maxLength={250} value={title} onChange={e=>setTitle(e.target.value)}/><FieldError id="rfq-title-error" message={fieldErrors.title}/></label><label className="field">{t('Delivery city (optional)')}<input name="city" maxLength={120} value={city} onChange={e=>setCity(e.target.value)}/><small>{t('Leave blank if the delivery location is not decided. Matching will then use approved categories without filtering by city.')}</small></label><label className="field">{t('Response deadline')}<input name="response_deadline" aria-invalid={Boolean(fieldErrors.response_deadline)} type="datetime-local" required value={deadline} onChange={e=>setDeadline(e.target.value)} min={new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16)}/><FieldError id="rfq-deadline-error" message={fieldErrors.response_deadline}/></label><label className="field">{t('Project name / reference')}<input name="project_name" maxLength={250} value={projectName} onChange={event=>setProjectName(event.target.value)}/></label><label className="field">{t('Required delivery date')}<input name="required_delivery_date" type="date" min={deadline.slice(0,10)} value={deliveryDate} onChange={event=>setDeliveryDate(event.target.value)}/><FieldError id="rfq-delivery-error" message={fieldErrors.required_delivery_date}/></label><label className="field">{t('Additional instructions')}<textarea name="additional_instructions" maxLength={10000} value={instructions} onChange={event=>setInstructions(event.target.value)}/></label><label className="field">{t('Optional attachments')}<input type="file" multiple accept=".pdf,.xlsx,.docx,.png,.jpg,.jpeg,.webp,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,image/webp" disabled={busy} onChange={event=>{const allowed=['application/pdf','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.openxmlformats-officedocument.wordprocessingml.document','image/png','image/jpeg','image/webp'];const selected=Array.from(event.target.files||[]);const invalid=selected.find(file=>file.size>5_000_000||!allowed.includes(file.type));if(invalid){setError(invalid.size>5_000_000?'Each attachment must be 5 MB or smaller.':'Attach a PDF, XLSX, DOCX, PNG, JPEG or WebP file.');}else if(files.length+selected.length>10){setError('Choose no more than 10 attachments.');}else{setError('');setFiles(previous=>[...previous,...selected]);}event.target.value='';}}/><small>{t('PDF, XLSX, DOCX, PNG, JPEG or WebP. Up to 10 files. Each file up to 5 MB.')}</small></label>{files.length>0&&<div className="file-upload-list" aria-label={t('Selected files')}>{files.map((file,index)=><div className="file-upload-item" key={`${file.name}:${file.lastModified}:${index}`}><span><strong>{file.name}</strong><small>{(file.size/1024/1024).toFixed(2)} MB</small></span><Button type="button" variant="ghost" disabled={busy} onClick={()=>setFiles(previous=>previous.filter((_,position)=>position!==index))}>{t('Remove')}</Button></div>)}</div>}</div>}
 {busy&&<FileUploadStatus title={uploadProgress?t('Uploading')+' '+uploadProgress.name:'Saving RFQ'} detail={uploadProgress?undefined:t('Please wait while the RFQ and attachments are saved.')} percent={uploadProgress?.percent??null}/>}
 {step===1&&<><State {...taxonomy} retry={taxonomy.reload}/><State {...units} retry={units.reload}/>
 <div className="rfq-material-list">{items.map((line,index)=><MaterialRow errors={fieldErrors} key={line.key} line={line} index={index} categories={taxonomy.data||[]} units={units.data||[]} onChange={next=>setItems(current=>current.map(item=>item.key===next.key?next:item))} onRemove={()=>setItems(current=>current.filter(item=>item.key!==line.key))}/>)}</div>
 <div className="rfq-material-actions"><Button type="button" disabled={items.length>=500} onClick={()=>setItems(current=>[...current,{key:crypto.randomUUID(),material_id:null,main_category_id:null,subcategory_id:null,description:'',specifications:'',unit:'',quantity:''}])}>{arabic?'إضافة مادة':'Add Material'}</Button><Button type="button" variant="outline" onClick={()=>setImportOpen(true)}>{arabic?'استيراد من Excel / CSV':'Import from Excel / CSV'}</Button><Button type="button" variant="ghost" onClick={()=>setShowBatchByCategory(v=>!v)}>{showBatchByCategory?t('Hide bulk entry by category'):t('Bulk paste multiple rows by category')}</Button></div>
 {showBatchByCategory&&<GroupedMaterialEntry mode="rfq" onImport={()=>setImportOpen(true)} onDirtyChange={setGroupDirty} draftId={id||'new'} categories={taxonomy.data||[]} units={units.data||[]} onSave={async group=>{setItems(current=>[...current,...group.rows.map(row=>({key:row.key,description:row.description,unit:row.unit,quantity:row.quantity,specifications:row.specifications,material_id:row.material_id,main_category_id:group.main_category_id,subcategory_id:group.subcategory_id}))]);}}/>}</>} {step===2&&<><h2>{title}</h2><p>{city} · {deadline}</p>{projectName&&<p>{t('Project name / reference')}: {projectName}</p>}{deliveryDate&&<p>{t('Required delivery date')}: {deliveryDate}</p>}{instructions&&<p style={{whiteSpace:'pre-wrap'}}>{instructions}</p>}{files.map((file,index)=><p key={index}>{file.name}</p>)}<GroupedRfqMaterials items={items} categories={taxonomy.data||[]}/><p className="workflow-help">{t('Suppliers are matched by category, subcategory, material catalog and verified capabilities. Technical compliance is reviewed separately.')}</p></>}
 {error&&<p className="notice" role="alert">{t(error)}</p>}{error&&files.length>0&&<Button type="button" variant="outline" disabled={busy} onClick={()=>void save(retryPublish)}>{t('Retry upload')}</Button>}<div className="form-actions"><Button type="button" variant="outline" disabled={busy||step===0} onClick={()=>setStep(step-1)}>{t('Back')}</Button>{step===2&&<Button type="button" variant="outline" disabled={busy} onClick={()=>void save(false)}>{t('Save draft')}</Button>}<Button type="submit" disabled={busy||draft.loading||(step===1&&(taxonomy.loading||units.loading))}>{t(busy?'Saving...':step===2?'Confirm and publish':'Continue')}</Button></div></form>{importOpen&&<RFQMaterialImport open orgId={org.id} draftKey={id||localDraftKey} rfqId={savedId} categories={taxonomy.data||[]} units={units.data||[]} onClose={()=>setImportOpen(false)} onAdd={lines=>{const keys=new Set(items.map(item=>item.key));const added=lines.filter(line=>!keys.has(line.key));if(items.length+added.length>500)throw new Error('An RFQ supports up to 500 materials. Remove some existing rows first.');setItems(current=>{const existing=new Set(current.map(item=>item.key));return [...current,...lines.filter(line=>!existing.has(line.key))];});}}/>}</div>;
}
export function RFQDetail({id,role}:{id:string;role:string}){
 const {t}=useLocale();
 const cats=useApi<Schemas['CategoryOut'][]>('taxonomy/categories');const names=new Map((cats.data||[]).map(c=>[c.id,c.name_en]));
 const q=useApi<Schemas['RFQDetail']>(`rfqs/${id}`);const [editing,setEditing]=useState(false);
 if(editing)return <RFQWizard id={id}/>;
 const r=q.data?.rfq;
 return <div className="procurement-workflow"><Heading title={r?.reference||t('RFQ details')}/><State {...q} retry={q.reload}/>{r&&<><section className="panel"><h2>{r.title}</h2>{r.project_name&&<p>{t('Project name / reference')}: {r.project_name}</p>}{r.required_delivery_date&&<p>{t('Required delivery date')}: {r.required_delivery_date}</p>}{r.additional_instructions&&<p style={{whiteSpace:'pre-wrap'}}>{r.additional_instructions}</p>}<RFQAttachments id={id} attachments={q.data?.attachments||[]} editable={role==='buyer'&&r.status==='draft'} reload={q.reload}/><p>{r.city} · {t(String(r.status??'').replaceAll('_',' '))} · {t("Deadline")} {r.response_deadline}</p><div className="live-actions"><RfqExportActions rfqId={id}/>{(role==='buyer'||role==='admin')&&<>{role==='buyer'&&r.status==='draft'&&<><Button onClick={()=>setEditing(true)}>{t("Edit draft")}</Button><Action path={`rfqs/${id}/publish`} onDone={q.reload} confirm="Publish this RFQ and queue supplier matching?">{t("Publish")}</Action></>}{role==='admin'&&r.status==='draft'&&<Action path={`rfqs/${id}/publish`} onDone={q.reload} confirm="Publish this RFQ and queue the existing automatic matching worker?">{t("Publish RFQ")}</Action>}{!['closed','cancelled'].includes(r.status)&&<Action path={`rfqs/${id}/cancel`} onDone={q.reload} confirm="Cancel this RFQ? Suppliers will no longer be able to submit.">{t("Cancel RFQ")}</Action>}{['published','collecting_quotes','review'].includes(r.status)&&<Action path={`rfqs/${id}/close`} onDone={q.reload} confirm="Close this RFQ?">{t("Close RFQ")}</Action>}{role==='buyer'&&<><Link href={`/app/buyer/matches?rfq=${id}`}>{t("Matching results")}</Link><Link href={`/app/buyer/compare?rfq=${id}`}>{t("Compare quotations")}</Link></>}</>}</div></section><GroupedRfqMaterials items={q.data?.items||[]} categories={cats.data||[]}/>{role==='buyer'&&r.status!=='draft'&&<Matching id={id} embedded/>}</>}</div>;}

function RFQAttachments({id,attachments,editable,reload}:{id:string;attachments:Schemas['RFQAttachmentOut'][];editable:boolean;reload:()=>void}){
 const {t}=useLocale();const [busy,setBusy]=useState(false),[error,setError]=useState('');
 return <section><h3>{t('Attachments')}</h3>{busy&&<FileUploadStatus title="Downloading attachment" detail={t('Preparing your authorized private download.')} percent={null}/>}{attachments.map(file=><div className="file-upload-item" key={file.id}><span><strong>{file.filename}</strong></span><div className="live-actions"><Button type="button" variant="outline" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{const {apiUrl}=await import('@/lib/api/client');const response=await fetch(apiUrl(`rfqs/${id}/attachments/${file.id}`),{credentials:'include'});if(!response.ok)throw new Error(t('Could not download attachment'));const url=URL.createObjectURL(await response.blob());const anchor=document.createElement('a');anchor.href=url;anchor.download=file.filename;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){setError(error instanceof Error?error.message:'Download failed');}finally{setBusy(false);}}}>{t('Download')}</Button> {editable&&<Action path={`rfqs/${id}/attachments/${file.id}`} method="DELETE" onDone={reload}>{t('Remove')}</Action>}</div></div>)}{!attachments.length&&<p>{t('No attachments')}</p>}{error&&<p role="alert">{error}</p>}</section>;
}
