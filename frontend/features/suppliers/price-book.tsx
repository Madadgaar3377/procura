"use client";
import {useEffect,useRef,useState,type FormEvent} from 'react';
import Link from 'next/link';
import {api,json} from '@/lib/api/client';
import {useApi} from '@/lib/api/use-api';
import type {Schemas} from '@/lib/api/generated/schema';
import {useSession} from '@/features/auth/session';
import {useLocale} from '@/lib/locale';
import {Button} from '@/components/ui/button';
import {Heading,State,DataTable} from '@/features/shared/ui';
import {RateStatus,useApprovalRefresh} from './price-book-form';
import {RateForm} from './price-book-entry';
import {useToast} from '@/features/shared/toast';
import './price-book.css';

type Rate=Schemas['PriceOut'];
type Material=Schemas['MaterialOut'];
function useDebounce(value:string){const [result,setResult]=useState(value);useEffect(()=>{const timer=setTimeout(()=>setResult(value),300);return()=>clearTimeout(timer);},[value]);return result;}
function useWords(){const {arabic}=useLocale();return (en:string,ar:string)=>arabic?ar:en;}

export function PriceBook(){
 const {org}=useSession();
 // Unmount state when the active organization changes, including open private forms.
 return org?<PriceBookWorkspace key={org.id} orgId={org.id}/>:null;
}
function PriceBookWorkspace({orgId}:{orgId:string}){
 const w=useWords();const {arabic,t}=useLocale();const toast=useToast();
 const [search,setSearch]=useState(''),[main,setMain]=useState(''),[sub,setSub]=useState(''),[status,setStatus]=useState('all'),[offset,setOffset]=useState(0);
 const query=useDebounce(search);const [editing,setEditing]=useState<Rate|null|undefined>(),[importOpen,setImportOpen]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const categories=useApi<Schemas['CategoryOut'][]>('taxonomy/categories');
 const params=new URLSearchParams({q:query,main_category_id:main,subcategory_id:sub,status,limit:'25',offset:String(offset)});
 const rates=useApi<Schemas['PricePage']>(`pricebooks/${orgId}/page?${params}`);
 useApprovalRefresh(rates.reload);
 useEffect(()=>{setOffset(0);},[query,main,sub,status]);
 const categoryName=(c:Schemas['CategoryOut'])=>arabic&&c.name_ar?c.name_ar:c.name_en;
 const filter=(name:string,value:string,change:(v:string)=>void,options:{value:string;label:string}[])=><label className="field">{name}<select value={value} onChange={e=>change(e.target.value)}><option value="">{w('All','الكل')}</option>{options.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></label>;
 async function toggle(rate:Rate){if(busy)return;setBusy(true);setError('');try{await api(`pricebooks/${rate.id}/status`,{method:'PATCH',body:json({active:!rate.active,expected_version:rate.version})});rates.reload();toast.success(t('Changes saved.'));}catch(e){setError(e instanceof Error?e.message:t('Request failed'));}finally{setBusy(false);}}
 return <div className="supplier-prices"><Heading title={w('My Materials & Prices','موادي وأسعاري')} description={w('Your private supplier price book. Saved rates help prepare quotations; buyers see only submitted offers.','دفتر أسعار المورد الخاص. تساعد الأسعار المحفوظة في إعداد العروض، ولا يرى المشتري إلا العروض المقدمة.')} action={<div className="live-actions"><Button onClick={()=>setEditing(null)}>{w('Add Material & Price','إضافة مادة وسعر')}</Button><Button variant="outline" onClick={()=>setImportOpen(true)}>{w('Import Excel / CSV','استيراد Excel / CSV')}</Button></div>}/>
 <section className="panel price-filters" aria-label={w('Filter materials','تصفية المواد')}>
 <label className="field">{w('Search materials','البحث عن المواد')}<input type="search" maxLength={200} value={search} onChange={e=>setSearch(e.target.value)} placeholder={w('Description, code, brand or model','الوصف أو الرمز أو العلامة أو الطراز')}/></label>
 {filter(w('Main Category','الفئة الرئيسية'),main,v=>{setMain(v);setSub('');},(categories.data||[]).filter(c=>!c.parent_id).map(c=>({value:c.id,label:categoryName(c)})))}
 {filter(w('Subcategory','الفئة الفرعية'),sub,setSub,(categories.data||[]).filter(c=>c.parent_id&&(!main||c.parent_id===main)).map(c=>({value:c.id,label:categoryName(c)})))}
 <label className="field">{w('Status','الحالة')}<select value={status} onChange={e=>setStatus(e.target.value)}>{['all','active','inactive','expired','review'].map(s=><option key={s} value={s}>{w(({all:'All',active:'Active',inactive:'Inactive',expired:'Expired',review:'Needs review'})[s]!,({all:'الكل',active:'نشط',inactive:'غير نشط',expired:'منتهي',review:'يحتاج مراجعة'})[s]!)}</option>)}</select></label></section>
 <State {...categories} retry={categories.reload}/><State {...rates} retry={rates.reload}/>{error&&<p className="notice" role="alert">{t(error)}</p>}
 {!rates.loading&&!rates.error&&<><DataTable paginate={false} rows={rates.data?.items||[]} columns={['main_category_name','subcategory_name','material_description','unit','unit_price','status']} labels={{main_category_name:w('Main Category','الفئة الرئيسية'),subcategory_name:w('Subcategory','الفئة الفرعية'),material_description:w('Description','الوصف'),unit:w('Unit','الوحدة'),unit_price:w('Unit Price (SAR)','سعر الوحدة (ر.س)'),status:w('Status','الحالة')}} render={{status:r=><RateStatus status={String(r.status)} capabilityStatus={String(r.capability_status||'approved')}/>,unit_price:r=>new Intl.NumberFormat(arabic?'ar-SA':'en-SA',{style:'currency',currency:'SAR'}).format(Number(r.unit_price)),material_description:r=><><strong>{String(r.material_description)}</strong>{!!(r.brand||r.model)&&<small>{String(r.brand||'')} {String(r.model||'')}</small>}</>}} action={row=><div className="live-actions"><Button variant="outline" onClick={()=>setEditing(row as Rate)}>{w('Edit price','تعديل السعر')}</Button><Button variant="outline" disabled={busy||!row.active&&row.capability_status==='not_selected'} onClick={()=>toggle(row as Rate)}>{row.active?w('Deactivate','إيقاف'):w('Reactivate','إعادة التفعيل')}</Button></div>}/>
 {!rates.data?.total&&<p className="notice">{w('Add your first material rate or private draft, or adjust the filters. No Material Master records will be created.','أضف أول سعر لمادة أو مسودة خاصة أو عدّل عوامل التصفية. لن يتم إنشاء مواد في الدليل الرئيسي.')}</p>}
 <div className="table-footer"><span>{rates.data?.total||0} {w('private rates','أسعار خاصة')}</span><div className="live-actions"><Button variant="outline" disabled={!offset||rates.fetching} onClick={()=>setOffset(Math.max(0,offset-25))}>{t('Previous')}</Button><Button variant="outline" disabled={rates.fetching||offset+25>=(rates.data?.total||0)} onClick={()=>setOffset(offset+25)}>{t('Next')}</Button></div></div></>}
 {editing!==undefined&&<RateForm orgId={orgId} rate={editing} close={()=>setEditing(undefined)} done={()=>{setEditing(undefined);rates.reload();}}/>}
 {importOpen&&<RateImport orgId={orgId} close={()=>{setImportOpen(false);rates.reload();}}/>}
 <AutomationSettings orgId={orgId}/>
 </div>;
}

function Dialog({title,close,children}:{title:string;close:()=>void;children:React.ReactNode}){
 const ref=useRef<HTMLDialogElement>(null);const w=useWords();
 useEffect(()=>{ref.current?.showModal();},[]);
 return <dialog ref={ref} className="live-dialog supplier-price-dialog" aria-label={title} onCancel={close}><div className="price-dialog-heading"><h2>{title}</h2><Button variant="outline" onClick={close}>{w('Close','إغلاق')}</Button></div>{children}</dialog>;
}
function RateImport({orgId,close}:{orgId:string;close:()=>void}){
 const w=useWords();const {t}=useLocale();const [batch,setBatch]=useState(''),[offset,setOffset]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[mapping,setMapping]=useState<number|null>(null),[search,setSearch]=useState('');const query=useDebounce(search);
 const progress=useApi<Schemas['RateImportOut']>(batch?`pricebooks/${orgId}/imports/${batch}?limit=25&offset=${offset}`:null);
 const materials=useApi<Material[]>(mapping!==null?`taxonomy/materials?autocomplete=true&limit=20&q=${encodeURIComponent(query)}`:null);
 const state=progress.data?.status;
 useEffect(()=>{if(!batch||['preview','completed','completed_with_errors','failed'].includes(state||''))return;const timer=setTimeout(progress.reload,1500);return()=>clearTimeout(timer);},[batch,state,progress.data,progress.reload]);
 async function run(action:()=>Promise<unknown>){if(busy)return;setBusy(true);setError('');try{await action();progress.reload();}catch(e){setError(e instanceof Error?e.message:t('Request failed'));}finally{setBusy(false);}}
 return <Dialog title={w('Import private rates','استيراد الأسعار الخاصة')} close={close}><p>{w('Columns: Main Category, Subcategory, Description, Unit, Unit Price. Optional: Brand, Model, Lead Time, Price Valid Until. Review and resolve every row before confirming.','الأعمدة: Main Category, Subcategory, Description, Unit, Unit Price. اختياري: Brand, Model, Lead Time, Price Valid Until. راجع جميع الصفوف قبل التأكيد.')}</p>
 {!batch&&<label className="field">{w('Excel or CSV file','ملف Excel أو CSV')}<input type="file" accept=".csv,.xlsx" disabled={busy} onChange={e=>{const file=e.target.files?.[0];if(file)void run(async()=>{const body=new FormData();body.set('file',file);const result=await api<Schemas['RateImportOut']>(`pricebooks/${orgId}/bulk-import`,{method:'POST',body});setBatch(result.batch_id);});}}/></label>}
 <State {...progress} retry={progress.reload}/>{progress.data&&<><p role="status">{t(progress.data.status)} · {progress.data.saved}/{progress.data.rows} {w('saved','محفوظ')} · {progress.data.errors} {w('issues','مشكلات')}</p>{progress.data.failure_reason&&<p role="alert">{progress.data.failure_reason}</p>}<DataTable paginate={false} rows={progress.data.items||[]} columns={['row','description','unit','unit_price','action','status','issue']} action={r=>state==='preview'?<Button variant="outline" onClick={()=>setMapping(Number(r.row))}>{w('Map material','ربط المادة')}</Button>:null}/>
 <div className="live-actions"><Button variant="outline" disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-25))}>{t('Previous')}</Button><Button variant="outline" disabled={offset+25>=progress.data.rows} onClick={()=>setOffset(offset+25)}>{t('Next')}</Button>{state==='preview'&&<Button disabled={busy||progress.data.errors>0||!progress.data.rows} onClick={()=>run(()=>api(`pricebooks/${orgId}/imports/${batch}/confirm`,{method:'POST'}))}>{w('Confirm and save rates','تأكيد وحفظ الأسعار')}</Button>}</div></>}
 {mapping!==null&&<section className="panel"><h3>{w('Map row','ربط الصف')} {mapping}</h3><label className="field">{w('Search Material Master','البحث في دليل المواد')}<input value={search} maxLength={100} onChange={e=>setSearch(e.target.value)}/></label><State {...materials} retry={materials.reload}/><select aria-label={w('Select material','اختر المادة')} value="" onChange={e=>{if(e.target.value)void run(async()=>{await api(`pricebooks/${orgId}/imports/${batch}/rows/${mapping}`,{method:'PUT',body:json({material_id:e.target.value})});setMapping(null);});}}><option value="">{w('Select material','اختر المادة')}</option>{materials.data?.map(m=><option key={m.id} value={m.id}>{m.code} · {m.name_en} · {m.unit}</option>)}</select></section>}
 {error&&<p role="alert" className="notice">{t(error)}</p>}</Dialog>;
}

function AutomationSettings({orgId}:{orgId:string}){
 const w=useWords();const {t}=useLocale();const toast=useToast();const policy=useApi<Schemas['QuoteAutomationOut']>(`pricebooks/${orgId}/automation`);const caps=useApi<Schemas['CapabilityOut'][]>(`capabilities/${orgId}`);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[search,setSearch]=useState(''),[selected,setSelected]=useState<{id:string;description:string;unit:string}[]|null>(null);const query=useDebounce(search);
 const materials=useApi<Material[]>(query?`taxonomy/materials?autocomplete=true&limit=20&q=${encodeURIComponent(query)}`:null);const covered=selected??policy.data?.materials??[];
 async function save(e:FormEvent<HTMLFormElement>){e.preventDefault();if(busy)return;const data=new FormData(e.currentTarget);setBusy(true);setError('');try{await api(`pricebooks/${orgId}/automation`,{method:'PUT',body:json({enabled:data.get('enabled')==='on',material_ids:covered.map(m=>m.id),service_cities:data.getAll('cities'),price_basis:data.get('price_basis'),vat_rate:data.get('vat_rate'),max_quantity:data.get('max_quantity')||null,max_total:data.get('max_total')||null,valid_until:data.get('valid_until')?new Date(String(data.get('valid_until'))).toISOString():null,confirm_terms:data.get('confirm_terms')==='on'})});policy.reload();toast.success(t('Changes saved.'));}catch(e){setError(e instanceof Error?e.message:t('Request failed'));}finally{setBusy(false);}}
 return <details className="panel price-automation"><summary>{w('Automatic quotation submission settings','إعدادات التقديم التلقائي للعروض')}</summary><p>{w('Manual review is the default. Automatic submission requires explicit authorization, valid technically confirmed rates and complete coverage. Changed buyer specifications always need review.','المراجعة اليدوية هي الوضع الافتراضي. يتطلب التقديم التلقائي تفويضاً صريحاً وأسعاراً صالحة ومؤكدة فنياً وتغطية كاملة. تتطلب المواصفات المعدلة مراجعة يدوية.')}</p><State {...policy} retry={policy.reload}/>{policy.data&&<form key={policy.data.revision} onSubmit={save}><fieldset disabled={busy} className="form-grid"><label className="field full"><span><input type="checkbox" name="enabled" defaultChecked={policy.data.enabled}/>{w('Enable automatic submission','تفعيل التقديم التلقائي')}</span></label>
 <label className="field full">{w('Authorized material coverage','تغطية المواد المفوضة')}<input type="search" value={search} maxLength={100} onChange={e=>setSearch(e.target.value)} placeholder={w('Search Material Master','البحث في دليل المواد')}/></label><State {...materials} retry={materials.reload}/>
 {materials.data?.filter(m=>!covered.some(c=>c.id===m.id)).map(m=><Button key={m.id} variant="outline" type="button" onClick={()=>setSelected([...covered,{id:m.id,description:m.name_en,unit:m.unit}])}>{m.name_en}</Button>)}
 <div className="full">{covered.map(m=><p key={m.id}>{m.description} · {m.unit} <Button type="button" variant="outline" onClick={()=>setSelected(covered.filter(c=>c.id!==m.id))}>{w('Remove','إزالة')}</Button></p>)}</div>
 <div className="field full">{w('Approved service cities','مدن الخدمة المعتمدة')}{[...new Set(caps.data?.filter(c=>c.confirmed&&c.accepts_rfqs).map(c=>c.city))].map(city=><label key={city}><input type="checkbox" name="cities" value={city} defaultChecked={policy.data?.service_cities?.includes(city)}/>{city}</label>)}</div>
 <label className="field">{w('Price basis','أساس السعر')}<select name="price_basis" defaultValue={policy.data.price_basis}><option value="supply">{w('Supply only','توريد فقط')}</option><option value="supply_install">{w('Supply and installation','توريد وتركيب')}</option></select></label>
 {([['max_quantity','Maximum quantity per item','الحد الأقصى لكمية المادة','0.001'],['max_total','Maximum total (SAR)','الحد الأقصى للإجمالي (ر.س)','0.01'],['vat_rate','VAT rate (0 to 1)','نسبة الضريبة (٠ إلى ١)','0.0001']] as const).map(([name,en,ar,step])=><label className="field" key={name}>{w(en,ar)}<input type="number" name={name} min={name==='vat_rate'?'0':step} max={name==='vat_rate'?'1':undefined} step={step} defaultValue={policy.data?.[name]||''}/></label>)}
 <label className="field">{w('Authorization valid until','التفويض صالح حتى')}<input type="datetime-local" name="valid_until" defaultValue={policy.data.valid_until?new Date(new Date(policy.data.valid_until).getTime()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16):''}/></label>
 <label className="field full"><span><input type="checkbox" name="confirm_terms" defaultChecked={policy.data.confirm_terms}/>{w('I authorize submission within this coverage, pricing basis and limits, subject to technical and commercial checks.','أفوض التقديم ضمن هذه التغطية وأساس السعر والحدود، مع استيفاء الفحوص الفنية والتجارية.')}</span></label></fieldset>{error&&<p role="alert" className="notice">{t(error)}</p>}<Button disabled={busy} type="submit">{busy?t('Saving...'):w('Save authorization settings','حفظ إعدادات التفويض')}</Button></form>}</details>;
}
