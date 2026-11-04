"use client";
import {useEffect,useId,useRef,useState,type FormEvent} from 'react';
import Link from 'next/link';
import {api,json} from '@/lib/api/client';
import {useApi} from '@/lib/api/use-api';
import type {Schemas} from '@/lib/api/generated/schema';
import {useLocale} from '@/lib/locale';
import {Button} from '@/components/ui/button';
import {State} from '@/features/shared/ui';
import {SearchableSelect} from '@/features/shared/searchable-select';
import {FieldError} from '@/features/shared/field-error';
import {ApiError} from '@/lib/api/errors';
import {useToast} from '@/features/shared/toast';
import {useApprovalRefresh} from './price-book-form';

type Rate=Schemas['PriceOut'];type Material=Schemas['MaterialOut'];
export function RateForm({orgId,rate,close,done}:{orgId:string;rate:Rate|null;close:()=>void;done:()=>void}){
 const {t,arabic}=useLocale();const w=(en:string,ar:string)=>arabic?ar:en;const toast=useToast();
 const dialog=useRef<HTMLDialogElement>(null),submitting=useRef(false);const id=useId();
 const [main,setMain]=useState(rate?.main_category_id||''),[sub,setSub]=useState(rate?.subcategory_id||'');
 const [description,setDescription]=useState(rate?.supplier_description||rate?.material_description||''),[unit,setUnit]=useState(rate?.unit||'');
 const [materialId,setMaterialId]=useState<string|null>(rate?.material_id||null),[masterUnit,setMasterUnit]=useState(rate?.material_id?rate.unit:'');
 const [query,setQuery]=useState(''),[open,setOpen]=useState(false),[cursor,setCursor]=useState(0);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[errors,setErrors]=useState<Record<string,string>>({});
 const cats=useApi<Schemas['CategoryOut'][]>('taxonomy/categories');
 const caps=useApi<Schemas['CapabilityOut'][]>(`capabilities/${orgId}`);
 const units=useApi<Schemas['UnitOptionOut'][]>('taxonomy/units');
 const policy=useApi<Schemas['PricePolicyOut']>('pricebooks/policy');
 useApprovalRefresh(caps.reload);
 useEffect(()=>{dialog.current?.showModal();},[]);
 useEffect(()=>{const timer=setTimeout(()=>setQuery(description.trim().slice(0,100)),250);return()=>clearTimeout(timer);},[description]);
 const selected=new Set((caps.data||[]).filter(c=>c.eligibility_status==='enabled').map(c=>c.category_id));
 const approved=new Set((caps.data||[]).filter(c=>c.confirmed&&c.eligibility_status==='enabled').map(c=>c.category_id));
 const options=(cats.data||[]).filter(c=>c.parent_id===main&&selected.has(c.id));
 const ready=!!main&&(!!sub||selected.has(main)&&options.length===0);
 const materials=useApi<Material[]>(ready&&open?`taxonomy/materials?${new URLSearchParams({main_category_id:main,...(sub?{subcategory_id:sub}:{}),q:query,autocomplete:'true',limit:'20'})}`:null);
 const suggestions=(materials.data||[]).filter(m=>selected.has(m.subcategory_id||m.category_id));
 const searching=materials.loading||description.trim().slice(0,100)!==query;
 const catName=(c:Schemas['CategoryOut'])=>arabic&&c.name_ar?c.name_ar:c.name_en;
 function reset(){setDescription('');setMaterialId(null);setMasterUnit('');setOpen(false);setCursor(0);}
 function choose(m:Material){setMaterialId(m.id);setDescription(m.name_en);setUnit(m.unit);setMasterUnit(m.unit);setOpen(false);setErrors({});}
 function custom(){setMaterialId(null);setMasterUnit('');setOpen(false);}
 async function save(e:FormEvent<HTMLFormElement>){
  e.preventDefault();if(submitting.current)return;
  const issues:Record<string,string>={};if(description.trim().length<3)issues.supplier_description=w('Enter at least 3 characters.','أدخل ٣ أحرف على الأقل.');if(!unit)issues.unit=w('Select a unit.','اختر الوحدة.');
  if(!ready||!selected.has(sub||main))issues.main_category_id=w('Select a saved supply category.','اختر فئة توريد محفوظة.');
  setErrors(issues);if(Object.keys(issues).length)return;
  submitting.current=true;setBusy(true);setError('');const data=new FormData(e.currentTarget);
  const body={org_id:orgId,main_category_id:main,subcategory_id:sub||null,material_id:materialId,supplier_description:description.trim(),unit,unit_price:String(data.get('unit_price')),active:data.get('active')==='on',price_basis:String(data.get('price_basis')),brand:String(data.get('brand')||''),model:String(data.get('model')||''),offered_description:String(data.get('offered_description')||''),offered_specs:String(data.get('offered_specs')||''),remarks:String(data.get('remarks')||''),lead_days:Number(data.get('lead_days')||7),valid_until:data.get('valid_until')?new Date(String(data.get('valid_until'))).toISOString():null,technical_confirmed:data.get('technical_confirmed')==='on',expected_version:rate?.version};
  try{await api(rate?`pricebooks/${rate.id}`:'pricebooks',{method:rate?'PUT':'POST',body:json(body)});toast.success(t('Changes saved.'));done();}
  catch(e){if(e instanceof ApiError)setErrors(e.fieldErrors);setError(e instanceof Error?e.message:t('Request failed'));}
  finally{submitting.current=false;setBusy(false);}
 }
 const field=(name:string,en:string,ar:string,value:string|number='',type='text')=><label className="field">{w(en,ar)}<input name={name} type={type} defaultValue={value} aria-invalid={!!errors[name]} aria-describedby={errors[name]?`${id}-${name}`:undefined} {...(name==='unit_price'?{required:true,min:'0.01',step:'0.01',inputMode:'decimal' as const}:name==='lead_days'?{min:0,max:3650}:{})}/><FieldError id={`${id}-${name}`} message={errors[name]}/></label>;
 return <dialog ref={dialog} className="live-dialog supplier-price-dialog" aria-labelledby={`${id}-title`} onCancel={close}>
 <div className="price-dialog-heading"><h2 id={`${id}-title`}>{rate?w('Edit material & price','تعديل المادة والسعر'):w('Add Material & Price','إضافة مادة وسعر')}</h2><Button variant="outline" onClick={close}>{w('Close','إغلاق')}</Button></div>
 <div className="price-category-tools"><Link href="/app/supplier/capabilities">{w('Manage supply categories','إدارة فئات التوريد')}</Link><Button variant="outline" disabled={caps.fetching} onClick={caps.reload}>{w('Refresh categories','تحديث الفئات')}</Button></div>
 <State {...cats} retry={cats.reload}/><State {...caps} retry={caps.reload}/><State {...units} retry={units.reload}/><State {...policy} retry={policy.reload}/>
 {!caps.loading&&!caps.error&&!selected.size&&<p className="notice">{w('Select your supply categories in your company profile first.','حدد فئات التوريد في ملف شركتك أولاً.')}</p>}
 <form onSubmit={save}><fieldset disabled={busy} className="form-grid">
 <SearchableSelect name="main_category_id" label={w('Main Category','الفئة الرئيسية')} value={main} required disabled={!!rate||cats.loading||caps.loading} onChange={v=>{setMain(v);setSub('');reset();}} error={errors.main_category_id} emptyMessage={w('No saved supply categories.','لا توجد فئات توريد محفوظة.')} options={(cats.data||[]).filter(c=>!c.parent_id&&(selected.has(c.id)||(cats.data||[]).some(child=>child.parent_id===c.id&&selected.has(child.id)))).map(c=>({value:c.id,label:catName(c)}))}/>
 <SearchableSelect name="subcategory_id" label={w('Subcategory','الفئة الفرعية')} value={sub} required={options.length>0} disabled={!!rate||!main||options.length===0} onChange={v=>{setSub(v);reset();}} options={options.map(c=>({value:c.id,label:catName(c),badge:approved.has(c.id)?w('Approved','معتمد'):policy.data?.require_capability_approval?w('Pending approval','بانتظار الاعتماد'):w('Self-declared','اختيار المورد'),status:approved.has(c.id)?'approved':'declared'}))}/>
 <label className="field full material-autocomplete price-material-search">{w('Material Description','وصف المادة')}
 <input name="supplier_description" role="combobox" aria-autocomplete="list" aria-expanded={open&&ready} aria-controls={`${id}-materials`} aria-activedescendant={open&&!searching&&suggestions[cursor]?`${id}-material-${cursor}`:undefined} aria-invalid={!!errors.supplier_description} aria-describedby={`${id}-description-help`} disabled={!ready} required minLength={3} maxLength={4000} value={description} placeholder={w('Search materials or enter your own description','ابحث عن مادة أو أدخل وصفك الخاص')} onFocus={()=>setOpen(true)} onClick={()=>setOpen(true)} onBlur={()=>setOpen(false)} onChange={e=>{setDescription(e.target.value);setMaterialId(null);setMasterUnit('');setOpen(true);setCursor(0);}} onKeyDown={e=>{
  if(e.key==='ArrowDown'){e.preventDefault();setOpen(true);setCursor(i=>Math.min(i+1,suggestions.length));}
  if(e.key==='ArrowUp'){e.preventDefault();setCursor(i=>Math.max(0,i-1));}
  if(e.key==='Enter'&&open){e.preventDefault();if(!searching&&suggestions[cursor])choose(suggestions[cursor]);else custom();}
  if(e.key==='Escape'&&open){e.preventDefault();e.stopPropagation();setOpen(false);}
 }}/><FieldError id={`${id}-description-error`} message={errors.supplier_description||errors.material_id}/>
 {open&&ready&&<span className="select-suggestions" id={`${id}-materials`} role="listbox">
 {searching?<span className="suggestion-message" role="status">{w('Searching materials…','جارٍ البحث عن المواد…')}</span>:materials.error?<span className="suggestion-message">{w('Suggestions unavailable. You can still enter a private description.','الاقتراحات غير متاحة. يمكنك إدخال وصف خاص.')}</span>:suggestions.length?suggestions.map((m,i)=><button type="button" role="option" id={`${id}-material-${i}`} aria-selected={m.id===materialId} className={cursor===i?'is-active':''} key={m.id} onMouseDown={e=>e.preventDefault()} onClick={()=>choose(m)}><span>{arabic&&m.name_ar?m.name_ar:m.name_en}<small>{m.code}</small></span><span>{m.unit}</span></button>):<span className="suggestion-message">{w('No matching master materials. Save your own private description.','لا توجد مواد مطابقة في الدليل. يمكنك حفظ وصفك الخاص.')}</span>}
 {description.trim().length>=3&&<button type="button" role="option" aria-selected={!materialId} className={cursor===suggestions.length?'is-active':''} onMouseDown={e=>e.preventDefault()} onClick={custom}>{w('Use custom description','استخدام وصف خاص')}</button>}</span>}
 <small id={`${id}-description-help`}>{materialId?w('Linked to Material Master. Editing the description creates a private custom offer.','مرتبط بدليل المواد. تعديل الوصف ينشئ عرضاً خاصاً.'):w('Private description. No global material will be created.','وصف خاص. لن يتم إنشاء مادة في الدليل العام.')}</small>
 </label>
 <div className="price-unit-field"><SearchableSelect name="unit" label={w('Unit','الوحدة')} value={unit} onChange={setUnit} required error={errors.unit} disabled={units.loading||!!units.error} options={(units.data||[]).map(u=>({value:u.code,label:u.code+' - '+u.name_en}))}/></div>
 {field('unit_price','Unit Price (SAR)','سعر الوحدة (ر.س)',rate?.unit_price||'','number')}
 {materialId&&unit!==masterUnit&&<div className="full notice">{w('No approved conversion is configured for this material. Use a custom private item for a different unit.','لا يوجد تحويل وحدة معتمد لهذه المادة. استخدم مادة خاصة لوحدة مختلفة.')}<Button type="button" variant="outline" onClick={custom}>{w('Use custom description','استخدام وصف خاص')}</Button></div>}
 <label className="field full"><span><input type="checkbox" name="active" defaultChecked={rate?.active??true}/>{w('Active private rate (uncheck to keep an inactive draft)','سعر خاص نشط (ألغِ التحديد للاحتفاظ بمسودة غير نشطة)')}</span></label>
 <details className="full"><summary>{w('Additional details & technical confirmation','تفاصيل إضافية والتأكيد الفني')}</summary><div className="form-grid">
 {field('brand','Offered brand','العلامة المعروضة',rate?.brand)}{field('model','Model','الطراز',rate?.model)}{field('offered_description','Offered description','الوصف المعروض',rate?.offered_description)}{field('offered_specs','Offered specifications','المواصفات المعروضة',rate?.offered_specs)}
 <label className="field">{w('Price basis','أساس السعر')}<select name="price_basis" defaultValue={rate?.price_basis||'supply'}><option value="supply">{w('Supply only','توريد فقط')}</option><option value="supply_install">{w('Supply and installation','توريد وتركيب')}</option></select></label>
 {field('lead_days','Lead time (days)','مدة التوريد (أيام)',rate?.lead_days??7,'number')}{field('valid_until','Price valid until','السعر صالح حتى',rate?.valid_until?new Date(new Date(rate.valid_until).getTime()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16):'','datetime-local')}{field('remarks','Remarks','ملاحظات',rate?.remarks)}
 <label className="field full"><span><input type="checkbox" name="technical_confirmed" defaultChecked={rate?.technical_confirmed}/>{w('I confirm the offered specifications. Each RFQ still requires its applicable technical checks.','أؤكد المواصفات المعروضة. يظل كل طلب عرض سعر خاضعاً للفحوص الفنية اللازمة.')}</span></label></div></details></fieldset>
 {error&&<p className="notice" role="alert">{t(error)}</p>}<Button disabled={busy||!ready||description.trim().length<3||!unit||caps.loading||!!caps.error||!!units.error} type="submit">{busy?t('Saving...'):w('Save private price','حفظ السعر الخاص')}</Button></form></dialog>;
}
