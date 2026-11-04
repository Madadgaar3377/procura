"use client";
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {api,json} from '@/lib/api/client';
import {useApi} from '@/lib/api/use-api';
import type {Schemas} from '@/lib/api/generated/schema';
import {Button} from '@/components/ui/button';
import {DataTable,State} from '@/features/shared/ui';
import {useLocale} from '@/lib/locale';
import {useToast} from '@/features/shared/toast';

type Plan=Schemas['PlanOut'];
type Feature={label:string;included:boolean};
const empty=()=>({name:'',slug:'',description:'',monthly_price:'0',yearly_price:'0',currency:'SAR',display_order:0,
 publication_status:'draft',visible_on_website:true,available_for_subscription:true,recommended:false,
 billing_intervals:['monthly','yearly'],feature_details:[] as Feature[],available_from:'',available_until:'',
 quote_limit:10,team_limit:1,auto_drafts:false,premium:false,active:true});
const localDate=(value:string|null)=>value?new Date(new Date(value).getTime()-new Date(value).getTimezoneOffset()*60000).toISOString().slice(0,16):'';
export default function PlanManagement(){
 const {t,arabic}=useLocale();const toast=useToast();const [search,setSearch]=useState('');const [q,setQ]=useState('');
 const [status,setStatus]=useState('all');const [sort,setSort]=useState('display_order');const [offset,setOffset]=useState(0);
 const [opened,setOpened]=useState(false);const [view,setView]=useState(false);const [original,setOriginal]=useState<Plan|null>(null);
 const [form,setForm]=useState(empty);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const dialog=useRef<HTMLDialogElement>(null);
 const page=useApi<Schemas['PlanPage']>(`memberships/admin/plan-page?q=${encodeURIComponent(q)}&status=${status}&sort=${sort}&limit=20&offset=${offset}`);
 useEffect(()=>{const timer=setTimeout(()=>{setQ(search);setOffset(0);},250);return()=>clearTimeout(timer);},[search]);
 useEffect(()=>{if(opened&&!dialog.current?.open)dialog.current?.showModal();else if(!opened&&dialog.current?.open)dialog.current?.close();},[opened]);
 const money=(value:string)=>new Intl.NumberFormat(arabic?'ar-SA':'en-SA',{style:'currency',currency:'SAR'}).format(Number(value));
 async function open(plan?:Plan,readOnly=false){
  setError('');setView(readOnly);
  try{const current=plan?await api<Plan>(`memberships/admin/plans/${plan.id}`):null;setOriginal(current);
   setForm(current?{...Object.fromEntries(Object.keys(empty()).map(key=>[key,(current as unknown as Record<string,unknown>)[key]??(empty() as unknown as Record<string,unknown>)[key]])) as ReturnType<typeof empty>,available_from:localDate(current.available_from),available_until:localDate(current.available_until),
    feature_details:current.feature_details.length?current.feature_details as Feature[]:current.features.map(label=>({label,included:true}))}:empty());
   setOpened(true);
  }catch(e){setError(e instanceof Error?e.message:t('Request failed'));}
 }
 async function mutate(plan:Plan,operation:'publish'|'unpublish'|'duplicate'|'archive'|'delete'){
  const message=operation==='delete'?'Permanently delete this unused plan? This cannot be undone.':operation==='archive'?'Archive this plan? Existing subscriptions are preserved.':operation==='duplicate'?'Create a draft copy of this plan?':'Change publication status?';
  if(busy||!window.confirm(t(message)))return;setBusy(true);setError('');
  const base=`memberships/admin/plans/${plan.id}`;
  try{
   if(operation==='archive'||operation==='delete')await api(`${base}${operation==='delete'?'/permanent':''}?expected_version=${plan.version}${operation==='delete'?'&confirmation=DELETE':''}`,{method:'DELETE'});
   else if(operation==='duplicate')await api(base+'/duplicate',{method:'POST',body:json({expected_version:plan.version})});
   else await api(base+'/publication',{method:'PATCH',body:json({expected_version:plan.version,publication_status:operation==='publish'?'published':'unpublished'})});
   page.reload();toast.success(t('Changes saved.'));
  }catch(e){setError(e instanceof Error?e.message:t('Request failed'));page.reload();}finally{setBusy(false);}
 }
 async function save(event:FormEvent<HTMLFormElement>){
  event.preventDefault();if(busy)return;
  const target=event.nativeEvent as SubmitEvent;const publish=(target.submitter as HTMLButtonElement|null)?.value==='publish';
  if(!form.billing_intervals.length){setError(t('Select at least one billing interval.'));return;}
  if(form.available_from&&form.available_until&&new Date(form.available_until)<=new Date(form.available_from)){setError(t('Availability end must follow start.'));return;}
  setBusy(true);setError('');
  try{
   const body={...form,publication_status:publish?'published':original?form.publication_status:'draft',
    features:form.feature_details.filter(f=>f.included).map(f=>f.label),
    available_from:form.available_from?new Date(form.available_from).toISOString():null,
    available_until:form.available_until?new Date(form.available_until).toISOString():null,
    ...(original?{expected_version:original.version}:{})};
   await api(`memberships/admin/plans${original?'/'+original.id:''}`,{method:original?'PUT':'POST',body:json(body)});
   setOpened(false);page.reload();toast.success(t('Changes saved.'));
  }catch(e){setError(e instanceof Error?e.message:t('Request failed'));}finally{setBusy(false);}
 }
 function moveFeature(index:number,direction:number){setForm(previous=>{const features=[...previous.feature_details];[features[index],features[index+direction]]=[features[index+direction],features[index]];return {...previous,feature_details:features};});}
 const updateFeature=(index:number,value:Partial<Feature>)=>setForm(previous=>({...previous,feature_details:previous.feature_details.map((feature,i)=>i===index?{...feature,...value}:feature)}));
 return <section aria-label={t('Manage Plans')}>
 <div className="toolbar" style={{flexWrap:'wrap'}}><h2>{t('Manage Plans')}</h2><Button onClick={()=>open()}>{t('Create plan')}</Button></div>
 <div className="toolbar" style={{flexWrap:'wrap'}}><label className="field">{t('Search')}<input type="search" value={search} placeholder={t('Plan name or code')} onChange={e=>setSearch(e.target.value)}/></label>
 <label className="field">{t('Status')}<select value={status} onChange={e=>{setStatus(e.target.value);setOffset(0);}}>{['all','draft','published','unpublished','archived'].map(value=><option key={value} value={value}>{t(value)}</option>)}</select></label>
 <label className="field">{t('Sort by')}<select value={sort} onChange={e=>{setSort(e.target.value);setOffset(0);}}>{['display_order','name','monthly_price','yearly_price','created_at'].map(value=><option key={value} value={value}>{t(value)}</option>)}</select></label></div>
 <State {...page} empty={!page.loading&&!page.error&&page.data?.total===0} retry={page.reload}/>
 {error&&!opened&&<p role="alert" className="notice">{error}</p>}
 <DataTable rows={(page.data?.items||[]).map(p=>({...p,monthly_price:money(p.monthly_price),yearly_price:money(p.yearly_price),status:p.archived?'archived':p.publication_status}))} columns={['name','monthly_price','yearly_price','subscribers','status','recommended','display_order']} searchable={false} paginate={false} action={row=>{
  const p=page.data!.items.find(plan=>plan.id===row.id)!;
  return <div className="live-actions"><Button size="sm" variant="outline" disabled={busy} onClick={()=>open(p,true)}>{t('View')}</Button>{!p.archived&&<><Button size="sm" variant="outline" disabled={busy} onClick={()=>open(p)}>{t('Edit')}</Button><Button size="sm" variant="outline" disabled={busy} onClick={()=>mutate(p,p.publication_status==='published'?'unpublish':'publish')}>{t(p.publication_status==='published'?'Unpublish':'Publish')}</Button></>}<Button size="sm" variant="outline" disabled={busy} onClick={()=>mutate(p,'duplicate')}>{t('Duplicate')}</Button>{!p.archived&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>mutate(p,'archive')}>{t('Archive')}</Button>}<Button size="sm" variant="destructive" disabled={busy} onClick={()=>mutate(p,'delete')}>{t('Delete')}</Button></div>;
 }}/>
 <div className="toolbar"><span>{page.data?.total||0} {t('plans')}</span><Button variant="outline" disabled={!offset} onClick={()=>setOffset(offset-20)}>{t('Previous')}</Button><Button variant="outline" disabled={offset+20>=(page.data?.total||0)} onClick={()=>setOffset(offset+20)}>{t('Next')}</Button></div>
 <dialog className="live-dialog plan-dialog" ref={dialog} aria-labelledby="plan-editor-title" onCancel={e=>{if(busy)e.preventDefault();else setOpened(false);}} onClose={()=>setOpened(false)}>
 <form className="live-form" onSubmit={save}><h2 id="plan-editor-title">{t(view?'View plan':original?'Edit plan':'Create plan')}</h2><p>{t('Prices are in SAR. Saving a price never activates billing.')}</p>
 <fieldset className="form-grid" disabled={busy||view}>
 <label className="field">{t('Plan Name')}<input required maxLength={80} value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
 <label className="field">{t('Plan Code / Slug')}<input required maxLength={100} pattern="[a-z0-9]+(-[a-z0-9]+)*" value={form.slug} onChange={e=>setForm({...form,slug:e.target.value})}/></label>
 <label className="field full">{t('Description')}<textarea maxLength={4000} value={form.description} onChange={e=>setForm({...form,description:e.target.value})}/></label>
 {(['monthly_price','yearly_price'] as const).map(key=><label className="field" key={key}>{t(key==='monthly_price'?'Monthly Price (SAR)':'Yearly Price (SAR)')}<input required inputMode="decimal" pattern="[0-9]{1,10}(\.[0-9]{1,2})?" value={form[key]} onChange={e=>setForm({...form,[key]:e.target.value})}/></label>)}
 <div className="field full"><span>{t('Billing intervals')}</span>{['monthly','yearly'].map(value=><label key={value}><input type="checkbox" checked={form.billing_intervals.includes(value)} onChange={e=>setForm({...form,billing_intervals:e.target.checked?[...form.billing_intervals,value]:form.billing_intervals.filter(x=>x!==value)})}/>{t(value)}</label>)}</div>
 <label className="field">{t('Publication status')}<select value={form.publication_status} onChange={e=>setForm({...form,publication_status:e.target.value})}>{['draft','published','unpublished'].map(value=><option key={value} value={value}>{t(value)}</option>)}</select></label>
 <label className="field">{t('Display Order')}<input required type="number" min="0" max="2147483647" value={form.display_order} onChange={e=>setForm({...form,display_order:Number(e.target.value)})}/></label>
 {(['recommended','visible_on_website','available_for_subscription'] as const).map(key=><label key={key}><input type="checkbox" checked={form[key]} onChange={e=>setForm({...form,[key]:e.target.checked})}/>{t(key==='recommended'?'Recommended Plan':key==='visible_on_website'?'Visible on Website':'Available for New Subscriptions')}</label>)}
 <label className="field">{t('Available from')}<input type="datetime-local" value={form.available_from} onChange={e=>setForm({...form,available_from:e.target.value})}/></label><label className="field">{t('Available until')}<input type="datetime-local" value={form.available_until} onChange={e=>setForm({...form,available_until:e.target.value})}/></label>
 <div className="field full"><h3>{t('Plan Features')}</h3>{form.feature_details.map((feature,index)=><div className="plan-feature-row" key={index}><input aria-label={`${t('Feature')} ${index+1}`} required maxLength={250} value={feature.label} onChange={e=>updateFeature(index,{label:e.target.value})}/><label><input type="checkbox" checked={feature.included} onChange={e=>updateFeature(index,{included:e.target.checked})}/>{t('Included')}</label><Button type="button" size="sm" variant="outline" aria-label={t('Move up')} disabled={index===0} onClick={()=>moveFeature(index,-1)}>↑</Button><Button type="button" size="sm" variant="outline" aria-label={t('Move down')} disabled={index===form.feature_details.length-1} onClick={()=>moveFeature(index,1)}>↓</Button><Button type="button" size="sm" variant="outline" onClick={()=>setForm({...form,feature_details:form.feature_details.filter((_,i)=>i!==index)})}>{t('Remove')}</Button></div>)}<Button type="button" variant="outline" disabled={form.feature_details.length>=50} onClick={()=>setForm({...form,feature_details:[...form.feature_details,{label:'',included:true}]})}>{t('Add feature')}</Button></div>
 <details className="field full"><summary>{t('Membership limits')}</summary><label>{t('Quotation allowance')}<input type="number" min="0" max="2147483647" value={form.quote_limit} onChange={e=>setForm({...form,quote_limit:Number(e.target.value)})}/></label><label>{t('Team allowance')}<input type="number" min="1" max="2147483647" value={form.team_limit} onChange={e=>setForm({...form,team_limit:Number(e.target.value)})}/></label>{(['auto_drafts','premium','active'] as const).map(key=><label key={key}><input type="checkbox" checked={form[key]} onChange={e=>setForm({...form,[key]:e.target.checked})}/>{t(key)}</label>)}</details>
 </fieldset>{error&&opened&&<p className="notice" role="alert">{error}</p>}<div className="live-actions">{!view&&<><Button type="submit" disabled={busy} value="save">{t(original?'Save changes':'Save Draft')}</Button><Button type="submit" disabled={busy} value="publish">{t('Save & Publish')}</Button></>}<Button type="button" variant="outline" disabled={busy} onClick={()=>setOpened(false)}>{t(view?'Close':'Cancel')}</Button></div></form></dialog>
 </section>;
}
