"use client";
import {useEffect,useRef,useState} from 'react';
import {IconSearch,IconX,IconChevronDown,IconShieldCheck} from '@tabler/icons-react';
import {Button} from '@/components/ui/button';
import {useSession} from '@/features/auth/session';
import {State} from '@/features/shared/ui';
import {useApi} from '@/lib/api/use-api';
import {api,json} from '@/lib/api/client';
import {useLocale} from '@/lib/locale';
import type {Schemas} from '@/lib/api/generated/schema';

type Capability=Schemas['CapabilityOut'];
export default function CategorySelection(){
 const {org,user}=useSession();const {t,arabic}=useLocale();
 const cats=useApi<Schemas['CategoryOut'][]>('taxonomy/categories');
 const saved=useApi<Capability[]>(org?`capabilities/${org.id}`:null);
 const [city,setCity]=useState(org?.city||'');const [search,setSearch]=useState('');
 const [expanded,setExpanded]=useState<string[]>([]);const [drafts,setDrafts]=useState<Record<string,string[]>>({});
 const [localSaved,setLocalSaved]=useState<Capability[]>();const [busy,setBusy]=useState(false),[error,setError]=useState(''),[done,setDone]=useState(false);
 const lock=useRef(false);const draftKey=`procura.capability-drafts:${user.id}:${org?.id}`;
 useEffect(()=>{try{const raw=sessionStorage.getItem(draftKey);if(raw)setDrafts(JSON.parse(raw));}catch{/* Draft storage is optional. */}},[draftKey]);
 useEffect(()=>{setLocalSaved(saved.data);},[saved.data]);
 const categories=cats.data||[];const records=localSaved||saved.data||[];
 const name=(category:Schemas['CategoryOut'])=>arabic&&category.name_ar?category.name_ar:category.name_en;
 const categoryById=new Map(categories.map(category=>[category.id,category]));
 const mains=categories.filter(category=>!category.parent_id&&category.active);
 const available=categories.filter(category=>category.active&&category.parent_id&&mains.some(main=>main.id===category.parent_id));
 const baseline=records.filter(cap=>cap.city===city&&available.some(category=>category.id===cap.category_id)).map(cap=>cap.category_id);
 const selected=[...new Set(drafts[city]??baseline)];
 const dirty=selected.length!==baseline.length||selected.some(id=>!baseline.includes(id));
 const matches=(category:Schemas['CategoryOut'])=>`${category.name_en} ${category.name_ar||''}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase());
 const visibleMains=mains.filter(main=>matches(main)||available.some(sub=>sub.parent_id===main.id&&matches(sub)));
 const approved=selected.filter(id=>records.some(cap=>cap.city===city&&cap.category_id===id&&cap.confirmed));
 function change(ids:string[]){setDone(false);setError('');setDrafts(previous=>{const next={...previous,[city]:[...new Set(ids)]};try{sessionStorage.setItem(draftKey,JSON.stringify(next));}catch{}return next;});}
 function toggle(id:string){change(selected.includes(id)?selected.filter(value=>value!==id):[...selected,id]);}
 async function save(){
  if(!org||lock.current||!dirty)return;lock.current=true;setBusy(true);setError('');setDone(false);
  try{const subcategory_ids=selected.filter(id=>available.some(category=>category.id===id));const main_category_ids=[...new Set(subcategory_ids.map(id=>categoryById.get(id)!.parent_id!))];
   const result=await api<Capability[]>('capabilities/selections',{method:'PUT',body:json({org_id:org.id,main_category_ids,subcategory_ids,city})});
   setLocalSaved(result);setDrafts(previous=>{const next={...previous};delete next[city];try{sessionStorage.setItem(draftKey,JSON.stringify(next));}catch{}return next;});setDone(true);saved.reload();
  }catch(cause){setError(cause instanceof Error?cause.message:'Request failed');}finally{lock.current=false;setBusy(false);}
 }
 return <section className="panel supply-categories"><div className="profile-section-heading"><h2>{t('Supply Categories')}</h2><p>{t('Select the subcategories you supply. New capabilities require administrator approval.')}</p></div>
 <State {...cats} retry={cats.reload}/><State {...saved} retry={saved.reload}/>{org&&<>
 <div className="coverage-toolbar"><label className="field">{t('Service city')}<input value={city} maxLength={100} disabled={busy} onChange={event=>{setCity(event.target.value);setDone(false);setError('');}} onBlur={()=>setCity(value=>value.trim())}/><small>{t('Supply coverage is separate from your office address.')}</small></label><div className="service-city-list"><span>{t('Saved service cities')}</span><div className="selected-categories">{[...new Set(records.map(cap=>cap.city))].map(value=><button type="button" key={value} disabled={busy} aria-pressed={city===value} onClick={()=>{setCity(value);setDone(false);setError('');}}>{value}</button>)}</div></div></div>
 <div className="capability-picker"><div className="category-browser"><label className="server-search category-search"><IconSearch size={19} aria-hidden="true"/><span className="sr-only">{t('Search categories')}</span><input type="search" value={search} onChange={event=>setSearch(event.target.value)} placeholder={t('Search main categories or subcategories')}/>{search&&<button type="button" aria-label={t('Clear search')} onClick={()=>setSearch('')}><IconX size={18}/></button>}</label>
 <div className="category-browser-heading"><strong>{t('Main categories')}</strong><span>{selected.length} {t('selected')}</span></div>
 <div className="category-selection-grid">{visibleMains.map(main=>{const children=available.filter(sub=>sub.parent_id===main.id);const matching=search.trim()&&!matches(main)?children.filter(matches):children;const open=!!search.trim()||expanded.includes(main.id);return <div className="category-group" key={main.id}><button type="button" className="category-group-toggle" aria-expanded={open} aria-controls={`category-group-${main.id}`} onClick={()=>setExpanded(previous=>previous.includes(main.id)?previous.filter(id=>id!==main.id):[...previous,main.id])}><span>{name(main)}</span><span className="category-group-count">{children.filter(sub=>selected.includes(sub.id)).length} / {children.length}</span><IconChevronDown size={18} className={open?'expanded':''}/></button><div id={`category-group-${main.id}`} hidden={!open} className="category-group-options">{matching.map(sub=>{const cap=records.find(record=>record.city===city&&record.category_id===sub.id);return <label className="checkbox-label" key={sub.id}><input type="checkbox" disabled={busy||saved.loading||cats.loading||!!saved.error||!!cats.error} checked={selected.includes(sub.id)} onChange={()=>toggle(sub.id)}/><span>{name(sub)}</span>{cap&&<small className={cap.confirmed?'capability-approved':'capability-pending'}>{t(cap.confirmed?'Approved':'Pending approval')}</small>}</label>;})}{!children.length&&<p>{t('No subcategories available.')}</p>}</div></div>;})}</div>
 {!cats.loading&&!cats.error&&!visibleMains.length&&<div className="table-empty"><p>{t(search?'No matching records.':'No categories available.')}</p>{search&&<Button variant="outline" onClick={()=>setSearch('')}>{t('Clear search')}</Button>}</div>}
 </div><aside className="capability-summary" aria-label={t('Selected Subcategories')}><div className="capability-summary-heading"><h3>{t('Selected Subcategories')}</h3><span>{selected.length}</span></div><p>{city||t('Choose a service city')}</p><div className="selected-categories selected-subcategories">{selected.map(id=>{const category=categoryById.get(id);return <button type="button" key={id} disabled={busy} onClick={()=>toggle(id)} aria-label={`${t('Remove')} ${category?name(category):id}`}><span>{category?name(category):id}</span><IconX size={15} aria-hidden="true"/></button>;})}</div>{!selected.length&&<p className="selection-empty">{t('Choose subcategories from the list to build your supply profile.')}</p>}
 <div className="capability-approval"><IconShieldCheck size={20}/><div><strong>{t('Capability approval')}</strong><p>{approved.length} {t('Approved')} &middot; {selected.filter(id=>records.some(cap=>cap.city===city&&cap.category_id===id&&!cap.confirmed)).length} {t('Pending approval')}</p>{selected.some(id=>!records.some(cap=>cap.city===city&&cap.category_id===id))&&<p>{t('New selections will be submitted for approval.')}</p>}</div></div>
 <p className="capability-save-note">{t('Removing a selection changes matching only after you save. Existing invitations are preserved.')}</p>
 {dirty&&<p className="unsaved-state" role="status">{t('Unsaved changes')}</p>}{error&&<p role="alert" className="notice">{t(error)}</p>}{done&&<p role="status" className="notice success">{t('Changes saved.')}</p>}
 <Button type="button" disabled={busy||saved.loading||cats.loading||!!saved.error||!!cats.error||!saved.data||!dirty||city.trim().length<2} onClick={()=>void save()}>{t(busy?'Saving...':'Save Changes')}</Button>
 </aside></div>
 </>}</section>;
}
