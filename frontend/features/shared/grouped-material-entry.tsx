"use client";
import {IconFileSpreadsheet} from '@tabler/icons-react';
import {useContext,useEffect,useRef,useState} from 'react';
import {ApiScope,useApi} from '@/lib/api/use-api';
import {api,json} from '@/lib/api/client';
import type {Schemas} from '@/lib/api/generated/schema';
import {useLocale} from '@/lib/locale';
import {Button} from '@/components/ui/button';
import {DownloadAction} from './admin-upload';
import './grouped-material-entry.css';

export type GroupRow={key:string;description:string;unit:string;quantity:string;price:string;material_id:string|null;specifications:string};
export type EntryGroup={main_category_id:string;subcategory_id:string|null;discipline:string|null;rows:GroupRow[]};
const blank=():GroupRow=>({key:crypto.randomUUID(),description:'',unit:'',quantity:'1',price:'',material_id:null,specifications:''});
export function GroupedMaterialEntry({mode='master',categories,units,onSave,onImport,onDirtyChange,draftId='new'}:{
 mode?:'master'|'rfq'|'supplier';categories:Schemas['CategoryOut'][];units:{code:string;name_en:string}[];
 onSave:(group:EntryGroup)=>Promise<void>;onImport?:()=>void;onDirtyChange?:(dirty:boolean)=>void;draftId?:string;
}){
 const {arabic}=useLocale();const w=(en:string,ar:string)=>arabic?ar:en;const scope=useContext(ApiScope);
 const storageKey=`procura:group:v1:${scope}:${mode}:${draftId}`;
 const [main,setMain]=useState(''),[sub,setSub]=useState(''),[discipline,setDiscipline]=useState('');
 const [rows,setRows]=useState<GroupRow[]>([blank()]),[paste,setPaste]=useState(''),[pasteOpen,setPasteOpen]=useState(false);
 const [search,setSearch]=useState(''),[term,setTerm]=useState(''),[offset,setOffset]=useState(0);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[restored,setRestored]=useState(false);
 const submitting=useRef(false);const root=useRef<HTMLElement>(null);
 useEffect(()=>{onDirtyChange?.(rows.some(row=>!!row.description.trim()));},[rows,onDirtyChange]);
 useEffect(()=>{try{const value=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(value&&Array.isArray(value.rows)&&value.rows.length<=300){setMain(value.main||'');setSub(value.sub||'');setDiscipline(value.discipline||'');setRows(value.rows);}}catch{}setRestored(true);},[storageKey]);
 useEffect(()=>{if(!restored)return;try{sessionStorage.setItem(storageKey,JSON.stringify({main,sub,discipline,rows}));}catch{}},[main,sub,discipline,rows,restored,storageKey]);
 useEffect(()=>{const timer=setTimeout(()=>{setTerm(search.trim());setOffset(0);},250);return()=>clearTimeout(timer);},[search]);
 const children=categories.filter(c=>c.parent_id===main);
 const ready=!!main&&(!children.length||!!sub);
 const results=useApi<Schemas['MaterialOut'][]>(ready&&term?`taxonomy/materials?${new URLSearchParams({main_category_id:main,...(sub?{subcategory_id:sub}:{}),q:term,limit:'20',offset:String(offset),autocomplete:'true'})}`:null);
 const label=(c:Schemas['CategoryOut'])=>arabic&&c.name_ar?c.name_ar:c.name_en;
 const hierarchy=[discipline,...[main,sub].map(id=>categories.find(c=>c.id===id)).filter(Boolean).map(c=>label(c!))].filter(Boolean).join(' / ');
 function update(key:string,patch:Partial<GroupRow>){setRows(current=>current.map(row=>row.key===key?{...row,...patch}:row));setError('');}
 function add(){if(rows.length>=300)return;setRows(current=>[...current,blank()]);setTimeout(()=>root.current?.querySelectorAll<HTMLInputElement>('[data-description]')[rows.length]?.focus(),0);}
 function choose(material:Schemas['MaterialOut']){const row={...blank(),description:material.name_en,unit:material.unit,material_id:material.id};setRows(current=>[...current.filter(r=>r.description.trim()),row]);setSearch('');}
 function pasteRows(){
  const parsed=paste.split(/\r?\n/).filter(line=>line.trim()).map(line=>{const [description,unit,amount]=line.split('\t');return {...blank(),description:description.trim(),unit:(unit||'').trim().toUpperCase(),...(mode==='rfq'?{quantity:amount?.trim()??'1'}:mode==='supplier'?{price:amount?.trim()||''}:{})};});
  if(rows.filter(r=>r.description.trim()).length+parsed.length>300){setError(w('Use Excel import for more than 300 rows.','استخدم استيراد Excel لأكثر من ٣٠٠ صف.'));return;}
  setRows(current=>[...current.filter(r=>r.description.trim()),...parsed]);setPaste('');setPasteOpen(false);
 }
 async function save(next:'stay'|'sub'|'main'){
  if(submitting.current)return;setError('');setMessage('');
  if(!ready){setError(w('Select a main category and subcategory.','اختر الفئة الرئيسية والفرعية.'));return;}
  if(next!=='stay'&&!rows.some(row=>row.description.trim())){setSub('');if(next==='main')setMain('');setSearch('');setRows([blank()]);return;}
  if(!rows.length){setError(w('Add at least one material.','أضف مادة واحدة على الأقل.'));return;}
  const seen=new Set<string>();
  for(let i=0;i<rows.length;i++){
   const row=rows[i];const identity=row.description.normalize('NFKC').trim().replace(/\s+/g,' ').toLowerCase()+'|'+row.unit;
   let issue='';
   if(row.description.trim().length<3||row.description.length>250)issue=w('Description must contain 3–250 characters.','يجب أن يحتوي الوصف على ٣ إلى ٢٥٠ حرفاً.');
   else if(!units.some(u=>u.code===row.unit))issue=w('Choose an approved unit.','اختر وحدة معتمدة.');
   else if(mode==='rfq'&&(!row.quantity.trim()||!Number.isFinite(Number(row.quantity))||Number(row.quantity)<=0))issue=w('Quantity must be greater than zero; remove zero-quantity rows.','يجب أن تكون الكمية أكبر من صفر؛ احذف الصفوف ذات الكمية صفر.');
   else if(mode==='supplier'&&(!row.material_id||Number(row.price)<=0||!Number.isFinite(Number(row.price))))issue=w('Select an existing material and enter a positive price.','اختر مادة موجودة وأدخل سعراً موجباً.');
   else if(seen.has(identity))issue=w('Duplicate description and unit in this group.','وصف ووحدة مكرران في هذه المجموعة.');
   if(issue){setError(`${i+1}: ${issue}`);return;}seen.add(identity);
  }
  submitting.current=true;setBusy(true);
  try{await onSave({main_category_id:main,subcategory_id:sub||null,discipline:discipline||null,rows});setRows([blank()]);setMessage(mode==='rfq'?w('Group added. Save the RFQ to keep these materials.','تمت إضافة المجموعة. احفظ طلب الأسعار للاحتفاظ بهذه المواد.'):w('Group saved.','تم حفظ المجموعة.'));if(next==='sub'){setSub('');setSearch('');root.current?.querySelector<HTMLSelectElement>('[data-subcategory]')?.focus();}if(next==='main'){setMain('');setSub('');setSearch('');}}
  catch(cause){setError(cause instanceof Error?cause.message:w('Save failed. Your draft is preserved.','فشل الحفظ. تم الاحتفاظ بالمسودة.'));}
  finally{submitting.current=false;setBusy(false);}
 }
 return <section ref={root} className="grouped-entry" dir={arabic?'rtl':'ltr'} aria-label={w('Grouped material entry','إدخال المواد حسب المجموعة')}>
  <h2>{w('Add materials by category','إضافة المواد حسب الفئة')}</h2><p>{w('Choose the category once, enter its materials, then save and continue to the next group.','اختر الفئة مرة واحدة وأدخل موادها ثم احفظ وانتقل إلى المجموعة التالية.')}</p>
  <fieldset disabled={busy} className="grouped-entry-fields"><div className="form-grid">
   {mode==='master'&&<label className="field">{w('Discipline (optional)','التخصص (اختياري)')}<select value={discipline} onChange={e=>setDiscipline(e.target.value)}><option value="">{w('Unspecified','غير محدد')}</option>{['Civil','Mechanical','Electrical'].map(d=><option key={d}>{d}</option>)}</select></label>}
   <label className="field">{w('Main Category','الفئة الرئيسية')}<select data-maincategory value={main} disabled={rows.some(r=>r.description.trim())} onChange={e=>{setMain(e.target.value);setSub('');setSearch('');}}><option value="">{w('Choose main category','اختر الفئة الرئيسية')}</option>{categories.filter(c=>!c.parent_id).map(c=><option key={c.id} value={c.id}>{label(c)}</option>)}</select></label>
   <label className="field">{w('Subcategory','الفئة الفرعية')}<select data-subcategory value={sub} disabled={!main||rows.some(r=>r.description.trim())} onChange={e=>{setSub(e.target.value);setSearch('');}}><option value="">{w(children.length?'Choose subcategory':'No subcategory','اختر الفئة الفرعية')}</option>{children.map(c=><option key={c.id} value={c.id}>{label(c)}</option>)}</select></label>
  </div><p className="grouped-hierarchy" aria-live="polite">{hierarchy||w('Select a category to begin','اختر فئة للبدء')}</p>
  <label className="field">{w('Search existing materials','البحث في المواد الموجودة')}<input type="search" disabled={!ready} value={search} maxLength={100} onChange={e=>setSearch(e.target.value)}/></label>
  {term&&ready&&<div className="grouped-search"><span role="status">{results.loading?w('Searching…','جارٍ البحث…'):results.error?results.error.message:''}</span>{results.data?.map(m=><Button type="button" variant="outline" key={m.id} onClick={()=>choose(m)}>{arabic&&m.name_ar?m.name_ar:m.name_en} · {m.unit}</Button>)}<div className="live-actions"><Button type="button" variant="ghost" disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-20))}>{w('Previous','السابق')}</Button><Button type="button" variant="ghost" disabled={results.data?.length!==20} onClick={()=>setOffset(offset+20)}>{w('Next','التالي')}</Button></div></div>}
  {rows.map((row,index)=><div className="grouped-material-row" key={row.key}>
   <label className="field">{w('Description','الوصف')} {index+1}<input data-description dir="auto" disabled={!ready||mode==='supplier'} maxLength={250} value={row.description} onChange={e=>update(row.key,{description:e.target.value,material_id:null})}/></label>
   <label className="field">{w('Unit','الوحدة')}<select disabled={!ready||mode==='supplier'} value={row.unit} onChange={e=>update(row.key,{unit:e.target.value,material_id:null})}><option value="">{w('Choose unit','اختر الوحدة')}</option>{units.map(u=><option key={u.code} value={u.code}>{u.code}</option>)}</select></label>
   {mode==='rfq'&&<><label className="field">{w('Quantity','الكمية')}<input type="number" min="0.001" step="0.001" value={row.quantity} onChange={e=>update(row.key,{quantity:e.target.value})}/></label><label className="field">{w('RFQ specifications','مواصفات طلب الأسعار')}<input value={row.specifications} onChange={e=>update(row.key,{specifications:e.target.value})}/></label></>}
   {mode==='supplier'&&<label className="field">{w('Unit price (SAR)','سعر الوحدة (ر.س)')}<input type="number" min="0.01" step="0.01" value={row.price} onChange={e=>update(row.key,{price:e.target.value})}/></label>}
   <Button type="button" variant="ghost" aria-label={`${w('Remove row','حذف الصف')} ${index+1}`} onClick={()=>setRows(current=>current.filter(r=>r.key!==row.key))}>{w('Remove','حذف')}</Button>
  </div>)}
  <div className="live-actions"><Button type="button" variant="outline" disabled={!ready||mode==='supplier'} onClick={add}>{w('Add Material Row','إضافة صف مادة')}</Button>{mode==='rfq'&&onImport&&<Button type="button" variant="outline" onClick={onImport}><IconFileSpreadsheet size={18}/>{w('Import Materials from Excel','استيراد المواد من Excel')}</Button>}{mode!=='supplier'&&<Button type="button" variant="outline" disabled={!ready} onClick={()=>setPasteOpen(!pasteOpen)}>{w('Paste Multiple Rows','لصق صفوف متعددة')}</Button>}</div>
  {pasteOpen&&<label className="field">{w('Paste tab-separated Description, Unit'+(mode==='rfq'?', Quantity':''),'الصق الوصف والوحدة'+(mode==='rfq'?' والكمية':'')+' مفصولة بعلامات التبويب')}<textarea value={paste} onChange={e=>setPaste(e.target.value)}/><Button type="button" onClick={pasteRows}>{w('Add pasted rows','إضافة الصفوف الملصقة')}</Button></label>}
  <div className="live-actions"><Button type="button" disabled={!ready} onClick={()=>void save('stay')}>{busy?w('Saving…','جارٍ الحفظ…'):w('Save Group','حفظ المجموعة')}</Button><Button type="button" variant="outline" disabled={!ready} onClick={()=>void save('sub')}>{w('Save and Next Subcategory','حفظ ثم الفئة الفرعية التالية')}</Button><Button type="button" variant="outline" disabled={!ready} onClick={()=>void save('main')}>{w('Add Another Main Category','إضافة فئة رئيسية أخرى')}</Button></div>
  </fieldset>
  {error&&<p role="alert" className="notice">{error}</p>}{message&&<p role="status">{message}</p>}
  <small>{w('Draft saved in this browser tab. Save or remove entered rows before changing their category.','تُحفظ المسودة في علامة التبويب هذه. احفظ الصفوف أو احذفها قبل تغيير الفئة.')}</small>
  {onImport&&mode!=='rfq'&&<div className="live-actions">{mode==='master'&&<DownloadAction path="imports/materials/template" filename="materials-template.xlsx" onError={setError}>{w('Download Excel Template','تنزيل قالب Excel')}</DownloadAction>}<Button type="button" variant="outline" onClick={onImport}>{w('Import Excel / CSV','استيراد Excel / CSV')}</Button></div>}
 </section>;
}

export async function saveMasterGroup(group:EntryGroup){
 for(let start=0;start<group.rows.length;start+=30){await api('taxonomy/materials/group',{method:'POST',body:json({...group,rows:group.rows.slice(start,start+30).map(({description,unit})=>({description,unit}))})});}
 window.dispatchEvent(new Event('procura:data-changed'));
}
