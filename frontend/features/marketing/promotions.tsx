import Link from 'next/link';
import {publicRead} from './public-data';
import type {MarketingLocale} from './copy';
export default async function Promotions({locale}:{locale:MarketingLocale}){
 const [suppliers,rfqs]=await Promise.all([publicRead<{id:string;name:string;city:string}>('memberships/public/featured-suppliers'),publicRead<{id:string;title:string;city:string;urgent:boolean;featured:boolean}>('memberships/public/featured-rfqs')]);
 if((!suppliers.ok||!suppliers.data.length)&&(!rfqs.ok||!rfqs.data.length))return null;
 return <section className="pm-section"><div className="pm-container">{suppliers.ok&&suppliers.data.length>0&&<><h2>{locale==='ar'?'?????? ??????':'Featured suppliers'}</h2><div className="pm-directory-grid">{suppliers.data.map(s=><article className="pm-directory-card" key={s.id}><span>{locale==='ar'?'????? ????':'Featured ? Sponsored'}</span><h3><Link href={`/suppliers/${s.id}`}>{s.name}</Link></h3><p>{s.city}</p></article>)}</div></>}{rfqs.ok&&rfqs.data.length>0&&<><h2>{locale==='ar'?'??? ???????':'Procurement opportunities'}</h2><div className="pm-directory-grid">{rfqs.data.map((r,i)=><article className="pm-directory-card" key={`${r.id}-${i}`}><span>{r.urgent?(locale==='ar'?'????':'Urgent'):(locale==='ar'?'????':'Featured ? Sponsored')}</span><h3>{r.title}</h3><p>{r.city}</p></article>)}</div></>}</div></section>;
}
