"use client";

import {useMemo, useState} from 'react';
import {useLocale} from '@/lib/locale';

type Channel = {value:string;label?:string;extension?:string|null};
type Contact = {id:string;name?:string|null;role?:string|null;emails?:Channel[];phones?:Channel[];faxes?:Channel[]};
type Row = {id:string;discipline?:string|null;main_category?:string|null;subcategory?:string|null;products?:string|null;brands?:string[];contact_ids:string[];mapping_status:string};
export type InspectionDetail = {company:Record<string,unknown>;status:{verification_status:string};contacts:Contact[];capability_inspection?:Row[]};

function value(raw:unknown, missing:string){return typeof raw==='string'&&raw.trim()?raw:missing;}
function channels(items:Channel[]|undefined, missing:string){return items?.length?items.map(item=>item.value+(item.extension?` ext. ${item.extension}`:'')).join(', '):missing;}

export function SupplierCompanyView({detail}:{detail:InspectionDetail}){
 const {t}=useLocale();const missing=t('Not provided');const c=detail.company;
 const fields:[string,string][]=[['name','Company name'],['country','Country'],['city','City'],['address','Address'],['cr_number','CR number'],['vat_number','VAT number'],['website','Website'],['description','Company description'],['business_email','Business email'],['business_phone','Business phone']];
 return <><h3>{t('Company information')}</h3><dl className="supplier-inspection-fields">{fields.map(([key,label])=><div key={key}><dt>{t(label)}</dt><dd>{value(c[key],missing)}</dd></div>)}<div><dt>{t('Verification status')}</dt><dd>{t(detail.status.verification_status||'unverified')}</dd></div></dl><h3>{t('General company contacts')}</h3><dl className="supplier-inspection-fields"><div><dt>{t('Contact person')}</dt><dd>{value(c.contact_name,missing)}</dd></div><div><dt>{t('Contact role')}</dt><dd>{value(c.designation,missing)}</dd></div></dl></>;
}

export function SupplierCapabilityView({detail}:{detail:InspectionDetail}){
 const {t}=useLocale();const missing=t('Not provided');const [search,setSearch]=useState('');const [discipline,setDiscipline]=useState('');
 const contacts=useMemo(()=>new Map(detail.contacts.map(item=>[item.id,item])),[detail.contacts]);
 const rows=detail.capability_inspection||[];
 const disciplines=[...new Set(rows.map(row=>row.discipline).filter((item):item is string=>Boolean(item)))].sort();
 const filtered=rows.filter(row=>{
  if(discipline&&row.discipline!==discipline)return false;
  const names=row.contact_ids.map(id=>contacts.get(id)?.name||'').join(' ');
  return [row.discipline,row.main_category,row.subcategory,row.products,(row.brands||[]).join(' '),names].join(' ').toLocaleLowerCase().includes(search.toLocaleLowerCase());
 });
 const groups=new Map<string,Row[]>();for(const row of filtered){const key=[row.discipline||missing,row.main_category||missing].join(' / ');groups.set(key,[...(groups.get(key)||[]),row]);}
 return <><div className="supplier-inspection-filters"><label>{t('Search capabilities')}<input value={search} onChange={event=>setSearch(event.target.value)} placeholder={t('Search products, brands or employees')}/></label><label>{t('Discipline')}<select value={discipline} onChange={event=>setDiscipline(event.target.value)}><option value="">{t('All disciplines')}</option>{disciplines.map(item=><option key={item} value={item}>{t(item)}</option>)}</select></label></div>{groups.size?[...groups].map(([key,items])=><details className="supplier-inspection-group" key={key} open={Boolean(search||discipline)||undefined}><summary>{key} <span>({items.length})</span></summary><div className="supplier-inspection-table"><table><thead><tr>{['Subcategory','Products','Brands','Responsible employee','Email','Telephone / mobile','Fax'].map(label=><th key={label}>{t(label)}</th>)}</tr></thead><tbody>{items.map(row=>{const people=[...new Set(row.contact_ids)].map(id=>contacts.get(id)).filter((person):person is Contact=>Boolean(person));return <tr key={row.id}><td>{value(row.subcategory,missing)}</td><td>{value(row.products,missing)}</td><td>{row.brands?.length?row.brands.join(', '):missing}</td><td>{people.length?people.map(person=>value(person.name,missing)).join(', '):missing}</td><td>{people.length?people.map(person=>channels(person.emails,missing)).join('; '):missing}</td><td>{people.length?people.map(person=>channels(person.phones,missing)).join('; '):missing}</td><td>{people.length?people.map(person=>channels(person.faxes,missing)).join('; '):missing}</td></tr>;})}</tbody></table></div></details>):<p>{t('No capabilities recorded')}</p>}</>;
}
