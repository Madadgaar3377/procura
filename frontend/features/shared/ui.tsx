"use client";
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { IconSearch, IconX, IconArrowsSort, IconArrowDown, IconArrowUp } from '@tabler/icons-react';
import { ApiError } from '@/lib/api/errors';
import { FieldError } from './field-error';
import { api, json } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { useLocale } from '@/lib/locale';
import { PageLoadingState, EmptyState, ErrorState } from './loading';
import { useToast } from '@/features/shared/toast';
import {adminConfirm} from './admin-confirm';
export function Heading({title,description,action}:{title:string;description?:string;action?:React.ReactNode}){const {t}=useLocale();return <div className="page-head"><div><span className="section-label">{t('WORKSPACE')}</span><h1>{t(title)}</h1>{description&&<p>{t(description)}</p>}</div>{action&&<div className="page-head-action">{action}</div>}</div>;}
export function State({loading,error,empty,retry,reload}:{loading?:boolean;error?:Error;empty?:boolean;retry?:()=>void;reload?:()=>void}){
 if(error)return <ErrorState error={error} retry={retry||reload}/>;
 if(loading)return <PageLoadingState/>;
 if(empty)return <EmptyState/>;
 return null;
}
export function Action({path,method='POST',body,children,onDone,confirm}:{path:string;method?:string;body?:unknown;children:React.ReactNode;onDone?:()=>void;confirm?:string}){
 const {t}=useLocale();
 const toast=useToast();
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [done,setDone]=useState(false);
 return <span className="live-action"><Button disabled={busy} variant="outline" onClick={async()=>{if(busy||confirm&&!(await (window.location.pathname.startsWith('/app/admin')?adminConfirm(t(confirm)):Promise.resolve(window.confirm(t(confirm))))))return;setBusy(true);setError('');setDone(false);try{await api(path,{method,body:body===undefined?undefined:json(body)});setDone(true);toast.success(t('Changes saved.'));onDone?.();}catch(e){const detail=e instanceof Error?t(e.message):t('Action failed');setError(detail);toast.error(t('Action failed'),detail);}finally{setBusy(false);}}}>{busy?t('Saving...'):typeof children==='string'?t(children):children}</Button>{error&&<span role="alert">{error}</span>}{done&&<span role="status">{t('Saved')}</span>}</span>;
}
export function DataTable({rows,columns,action,searchable=false,paginate=true,sortable=paginate,labels={},render={}}:{searchable?:boolean;paginate?:boolean;sortable?:boolean;labels?:Record<string,string>;render?:Record<string,(row:Record<string,unknown>)=>React.ReactNode>;rows:Record<string,unknown>[];columns:string[];action?:(row:Record<string,unknown>)=>React.ReactNode}){
 const {t,arabic}=useLocale();
 const [search,setSearch]=useState('');const [sort,setSort]=useState('');const [descending,setDescending]=useState(false);const [page,setPage]=useState(0);
 const cell=(key:string,value:unknown):string=>{
  if(value==null)return '\u2014';
  if(typeof value==='boolean')return t(value?(key==='confirmed'?'Approved':key.includes('verified')?'Verified':'Active'):(key==='confirmed'?'Pending approval':key.includes('verified')?'Unverified':'Inactive'));
  if(typeof value==='string'&&/(_at|_until|_deadline)$/.test(key)){const date=new Date(value);if(!Number.isNaN(date.getTime()))return new Intl.DateTimeFormat(arabic?'ar-SA-u-ca-gregory':'en-GB',{dateStyle:'medium',timeStyle:'short'}).format(date);}
  if(Array.isArray(value))return value.map(v=>typeof v==='object'?JSON.stringify(v):String(v)).join(', ')||'\u2014';
  if(typeof value==='object')return JSON.stringify(value);
  const text=String(value).replaceAll('_',' ');return /status$|^(price_basis|compliance|role|channel|topic)$/.test(key)?t(text.charAt(0).toUpperCase()+text.slice(1)):String(value);
 };
 const filtered=rows.filter(r=>!search||columns.some(k=>cell(k,r[k]).toLocaleLowerCase().includes(search.toLocaleLowerCase()))).sort((a,b)=>sort?String(a[sort]??'').localeCompare(String(b[sort]??''),undefined,{numeric:true})*(descending?-1:1):0);
 const currentPage=Math.min(page,Math.max(0,Math.ceil(filtered.length/20)-1));
 return <div className="panel data-table-panel">
  {searchable&&(rows.length>10||search)&&<label className="server-search table-search"><IconSearch size={19} aria-hidden="true"/><span className="sr-only">{t('Search this table')}</span><input type="search" placeholder={t('Search this table')} value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}}/>{search&&<button type="button" aria-label={t('Clear search')} onClick={()=>{setSearch('');setPage(0);}}><IconX size={18}/></button>}</label>}
  <div className="table-wrap" tabIndex={0} aria-label={t('Records table')}><table className="data-table"><thead><tr>{columns.map(k=><th key={k} aria-sort={sortable?(sort===k?(descending?'descending':'ascending'):'none'):undefined}>{sortable?<button className="table-sort" type="button" onClick={()=>{setSort(k);setDescending(sort===k?!descending:false);setPage(0);}}>{t(labels[k]||k.replaceAll('_',' '))}{sort===k?(descending?<IconArrowDown size={15}/>:<IconArrowUp size={15}/>):<IconArrowsSort size={15}/>}</button>:t(labels[k]||k.replaceAll('_',' '))}</th>)}{action&&<th>{t('Actions')}</th>}</tr></thead><tbody>{(paginate?filtered.slice(currentPage*20,currentPage*20+20):filtered).map((r,i)=><tr key={String(r.id??i)}>{columns.map(k=><td key={k} data-label={t(labels[k]||k.replaceAll('_',' '))}>{render[k]?render[k](r):/status$/.test(k)?<span className={`dashboard-badge ${String(r[k]||'')}`}>{cell(k,r[k])}</span>:cell(k,r[k])}</td>)}{action&&<td data-label={t('Actions')}>{action(r)}</td>}</tr>)}</tbody></table></div>
  {!filtered.length&&<div className="table-empty" role="status"><p>{t(search?'No matching records.':'No records yet')}</p>{search&&<Button type="button" variant="outline" onClick={()=>setSearch('')}>{t('Clear search')}</Button>}</div>}
  {paginate&&filtered.length>20&&<div className="table-footer"><span>{filtered.length} {t('records')} &middot; {t('page')} {currentPage+1} / {Math.ceil(filtered.length/20)}</span><Button type="button" variant="outline" disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}>{t('Previous')}</Button><Button type="button" variant="outline" disabled={(currentPage+1)*20>=filtered.length} onClick={()=>setPage(currentPage+1)}>{t('Next')}</Button></div>}
 </div>;
}
export type Field={name:string;label:string;type?:string;required?:boolean;readOnly?:boolean;min?:number;max?:number;step?:string;inputMode?:React.HTMLAttributes<HTMLInputElement>['inputMode'];maxLength?:number;hint?:string;normalize?:(value:string)=>string;validate?:(value:string)=>string;options?:{value:string;label:string}[];value?:string};
export function ApiForm({path,method='POST',fields,extra,onDone,label='Save',draftKey,disabled=false}:{path:string;method?:string;fields:Field[];extra?:Record<string,unknown>;onDone?:(data:unknown)=>void;label?:string;disabled?:boolean;draftKey?:string}) {
 const {t}=useLocale();const formId=useId();
 const toast=useToast();
 const formRef=useRef<HTMLFormElement>(null);const submitting=useRef(false);
 useEffect(()=>{if(!draftKey)return;try{const raw=sessionStorage.getItem(draftKey);if(raw){const values=JSON.parse(raw) as Record<string,string>;for(const [name,value] of Object.entries(values)){const input=formRef.current?.elements.namedItem(name);if(input instanceof HTMLInputElement||input instanceof HTMLTextAreaElement||input instanceof HTMLSelectElement)input.value=value;}}}catch{/* Draft persistence is optional. */}},[draftKey]);
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [success,setSuccess]=useState(false);const [fieldErrors,setFieldErrors]=useState<Record<string,string>>({});
 async function submit(e:FormEvent<HTMLFormElement>){
  e.preventDefault();if(submitting.current||disabled)return;
  const form=e.currentTarget;const values=new FormData(form);const issues:Record<string,string>={};
  for(const field of fields){const raw=String(values.get(field.name)||'');const value=field.normalize?field.normalize(raw):raw;values.set(field.name,value);const issue=field.validate?.(value);if(issue)issues[field.name]=issue;}
  setSuccess(false);setFieldErrors(issues);
  if(Object.keys(issues).length){setError('Check the highlighted fields.');const input=form.elements.namedItem(Object.keys(issues)[0]);if(input instanceof HTMLElement)input.focus();return;}
  submitting.current=true;setBusy(true);setError('');const payload:Record<string,unknown>={...extra};
  for(const f of fields){if(f.readOnly)continue;const v=String(values.get(f.name)||'');if(v!==''||f.required||(method==='PATCH'&&path.startsWith('organizations/')))payload[f.name]=v===''&&!f.required&&method==='PATCH'&&path.startsWith('organizations/')?null:f.type==='number'?Number(v):f.type==='datetime-local'?new Date(v).toISOString():v;}
  try{const data=await api(path,{method,body:json(payload)});setSuccess(true);if(draftKey){try{sessionStorage.removeItem(draftKey);}catch{}}toast.success(t('Changes saved.'));onDone?.(data);}
  catch(error){const detail=error instanceof ApiError&&Object.keys(error.fieldErrors).length?'Check the highlighted fields.':error instanceof Error?error.message:'Request failed';if(error instanceof ApiError)setFieldErrors(error.fieldErrors);setError(detail);toast.error(t('Request failed'),t(detail));}
  finally{submitting.current=false;setBusy(false);}
 }

 return <form ref={formRef} className="panel live-form" onSubmit={submit} onInvalidCapture={event=>{const input=event.target;if(!(input instanceof HTMLInputElement||input instanceof HTMLSelectElement||input instanceof HTMLTextAreaElement))return;if(input.name)setFieldErrors(previous=>({...previous,[input.name]:input.validity.valueMissing?"This field is required.":input.validationMessage}));}} onChange={event=>{const target=event.target;const name=target instanceof HTMLInputElement||target instanceof HTMLSelectElement||target instanceof HTMLTextAreaElement?target.name:"";if(name){if(draftKey&&formRef.current){try{sessionStorage.setItem(draftKey,JSON.stringify(Object.fromEntries(new FormData(formRef.current).entries())));}catch{}}setFieldErrors(previous=>({...previous,[name]:""}));setError('');setSuccess(false);}}}><fieldset disabled={busy||disabled} className="form-grid">{fields.map(f=><label key={f.name} className={`field ${f.type==='textarea'?'full':''}`}>{t(f.label)}{f.options?<select name={f.name} aria-invalid={Boolean(fieldErrors[f.name])} aria-describedby={[f.hint?`field-hint-${formId}-${f.name}`:'',fieldErrors[f.name]?`field-error-${formId}-${f.name}`:''].filter(Boolean).join(' ')||undefined} required={f.required} defaultValue={f.value||''}><option value="">{t('Select...')}</option>{f.options.map(o=><option key={o.value} value={o.value}>{t(o.label)}</option>)}</select>:f.type==='textarea'?<textarea name={f.name} aria-invalid={Boolean(fieldErrors[f.name])} aria-describedby={[f.hint?`field-hint-${formId}-${f.name}`:'',fieldErrors[f.name]?`field-error-${formId}-${f.name}`:''].filter(Boolean).join(' ')||undefined} required={f.required} defaultValue={f.value}/>:<input name={f.name} aria-invalid={Boolean(fieldErrors[f.name])} aria-describedby={[f.hint?`field-hint-${formId}-${f.name}`:'',fieldErrors[f.name]?`field-error-${formId}-${f.name}`:''].filter(Boolean).join(' ')||undefined} readOnly={f.readOnly} type={f.type||'text'} required={f.required} min={f.min} max={f.max} step={f.step} inputMode={f.inputMode} maxLength={f.maxLength} onBlur={event=>{if(!f.validate&&!f.normalize)return;const value=f.normalize?f.normalize(event.currentTarget.value):event.currentTarget.value;event.currentTarget.value=value;setFieldErrors(previous=>({...previous,[f.name]:f.validate?.(value)||''}));}} defaultValue={f.value}/>} {f.hint&&<small id={`field-hint-${formId}-${f.name}`}>{t(f.hint)}</small>}<FieldError id={`field-error-${formId}-${f.name}`} message={fieldErrors[f.name]}/></label>)}</fieldset>{error&&<p role="alert" className="notice">{t(error)}</p>}{success&&<p role="status" className="notice success">{t('Changes saved.')}</p>}<Button disabled={busy||disabled} type="submit">{busy?t('Saving...'):t(label)}</Button></form>;
}
