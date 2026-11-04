import 'server-only';
import LivePricing from './live-pricing';
import {cache} from 'react';
import Link from 'next/link';
import {IconArrowRight, IconBolt, IconBuilding, IconCheck, IconMapPin, IconSettings, IconShieldCheck, IconUsers} from '@tabler/icons-react';
import type {Schemas} from '@/lib/api/generated/schema';
import {marketingCopy,type MarketingLocale} from './copy';
import {MarketingCTA} from './chrome';

export const publicRead=cache(async <T,>(path:string):Promise<{ok:true;data:T[]}|{ok:false}>=>{
  try{
    const configured=process.env.FASTAPI_INTERNAL_URL||process.env.BACKEND_URL||process.env.NEXT_PUBLIC_API_URL||'http://127.0.0.1:8000';
    const base=new URL(configured);
    if(!['http:','https:'].includes(base.protocol)||base.username||base.password)return {ok:false};
    const response=await fetch(`${base.origin}/api/v1/${path}`,{cache:'no-store',signal:AbortSignal.timeout(2200),headers:{Accept:'application/json'},credentials:'omit'});
    if(!response.ok)return {ok:false};
    const data:unknown=await response.json();
    return Array.isArray(data)?{ok:true,data:data as T[]}:{ok:false};
  }catch{return {ok:false};}
});

export async function SectorCards({locale}:{locale:MarketingLocale}){
  const c=marketingCopy[locale];const result=await publicRead<Schemas['CategoryOut']>('taxonomy/categories');
  const roots=result.ok?result.data.filter(category=>category.active&&!category.parent_id):[];
  if(!result.ok||!roots.length)return <div className="pm-data-state"><p>{locale==='ar'?(result.ok?'\u0644\u0627 \u062a\u0648\u062c\u062f \u0641\u0626\u0627\u062a \u0646\u0634\u0637\u0629 \u062d\u0627\u0644\u064a\u0627\u064b':'\u062a\u0639\u0630\u0631 \u062a\u062d\u0645\u064a\u0644 \u0627\u0644\u0641\u0626\u0627\u062a'):(result.ok?'No active categories are available yet.':'Unable to load categories.')}</p><Link href="/industries" className="pm-text-link">{c.nav[3]}<IconArrowRight size={17}/></Link></div>;
  const known=['civil','electrical','mechanical'];const icons=[IconBuilding,IconBolt,IconSettings];
  return <div className="pm-sector-grid">{roots.map((root,index)=>{
    const knownIndex=known.indexOf(root.name_en.trim().toLowerCase());
    const sector=knownIndex>=0?c.sectors[knownIndex]:undefined;
    const children=result.ok?result.data.filter(category=>category.active&&category.parent_id===root.id).slice(0,5):[];
    const Icon=icons[knownIndex]||IconBuilding;
    return <article className={`pm-sector pm-sector-${knownIndex>=0?knownIndex:0}`} key={root.id}><div className="pm-sector-icon"><Icon stroke={1.45} size={33}/><span>{String(index+1).padStart(2,'0')}</span></div><h3>{locale==='ar'&&root.name_ar?root.name_ar:root.name_en}</h3><p>{sector?.description||c.sectorBody}</p>{children.length>0&&<><span className="pm-example-caption">{c.configuredCategories}</span><ul>{children.map(category=><li key={category.id}><span/>{locale==='ar'&&category.name_ar?category.name_ar:category.name_en}</li>)}</ul></>}<Link href={`/suppliers?main_category_id=${encodeURIComponent(root.id)}&lang=${locale}`}>{c.exploreSuppliers}<IconArrowRight className="pm-arrow" size={17}/></Link></article>;
  })}</div>;
}

export function DataSkeleton({locale,kind}:{locale:MarketingLocale;kind:'directory'|'plans'|'sectors'}){
  const c=marketingCopy[locale];
  return <div className="pm-data-skeleton" role="status" aria-label={kind==='directory'?c.directoryLoading:kind==='plans'?c.membershipsLoading:c.sectorTitle}><span className="sr-only">{kind==='directory'?c.directoryLoading:kind==='plans'?c.membershipsLoading:c.sectorTitle}</span>{[0,1,2].map(index=><div key={index}><i/><b/><span/><span/></div>)}</div>;
}

export async function DirectoryCards({locale}:{locale:MarketingLocale}){
  const c=marketingCopy[locale];const result=await publicRead<Schemas['PublicSupplier']>('directory/suppliers?limit=3&include_categories=true');
  if(!result.ok||!result.data.length)return <div className="pm-data-state" data-public-state={result.ok?'empty':'error'}><span className="pm-state-icon"><IconUsers size={28}/></span><div><h3>{result.ok?c.directoryEmptyTitle:c.directoryErrorTitle}</h3><p>{result.ok?c.directoryEmptyBody:c.directoryErrorBody}</p></div>{result.ok?<MarketingCTA role="supplier" className="pm-button-secondary">{c.registerCompany}</MarketingCTA>:<Link className="pm-text-link" href="/suppliers">{c.exploreSuppliers}<IconArrowRight className="pm-arrow" size={17}/></Link>}</div>;
  return <div className="pm-directory-grid" data-public-state="loaded">{result.data.map(supplier=><article className="pm-directory-card" key={supplier.id}><div className="pm-directory-card-top"><span className="pm-directory-company-icon"><IconBuilding size={25} stroke={1.4}/></span>{supplier.verified&&<span className="pm-verified"><IconShieldCheck size={15}/>{c.verified}</span>}</div><h3>{supplier.name}</h3><p><IconMapPin size={15}/><span>{locale==='ar'?'\u0645\u062f\u064a\u0646\u0629 \u0627\u0644\u0645\u0643\u062a\u0628: ':'Office City: '}{supplier.city||c.locationMissing}</span></p><div className="pm-category-tags">{(locale==='ar'?supplier.main_categories_ar:supplier.main_categories)?.length?(locale==='ar'?supplier.main_categories_ar:supplier.main_categories)!.map(category=><span key={category}>{category}</span>):<span>{c.categoriesPending}</span>}</div><Link href={`/suppliers/${encodeURIComponent(supplier.id)}`} className="pm-text-link">{c.viewSupplier}<IconArrowRight className="pm-arrow" size={17}/></Link></article>)}</div>;
}

export function MembershipCards({locale}:{locale:MarketingLocale}){return <LivePricing locale={locale}/>;}
