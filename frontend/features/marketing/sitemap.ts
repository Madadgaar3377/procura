import {unstable_cache} from 'next/cache';
import {CANONICAL_SITE_URL} from './seo';

export const PUBLIC_SITEMAP_PATHS = ['/', '/how-it-works', '/suppliers', '/industries', '/pricing', '/about', '/contact', '/faq', '/privacy', '/terms'] as const;
const PAGE_SIZE = 100;
const MAX_PROFILES_IN_ROOT = 24990;
const XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>';
const NAMESPACE = 'http://www.sitemaps.org/schemas/sitemap/0.9';
type PublicSupplier = {id:string;verified:true};
type SupplierPage = {items:PublicSupplier[];total:number;page:number;limit:number};

export function xmlEscape(value:string):string {
  return value.replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'})[c]!);
}

export function sitemapUrl(path:string, locale:'en'|'ar'='en'):string {
  const url=new URL(path,CANONICAL_SITE_URL);
  if(locale==='ar')url.searchParams.set('lang','ar');
  return url.href;
}

export function serializeUrlset(paths:readonly string[]):string {
  // HTML hreflang remains in page metadata. XHTML elements suppress Chromium's
  // native XML tree viewer; HTML hreflang is an equivalent Google-supported method.
  const urls=[...new Set(paths.flatMap(path=>[sitemapUrl(path),sitemapUrl(path,'ar')]))];
  if(urls.length>50000)throw new Error('Sitemap must be split');
  return [XML_HEADER,`<urlset xmlns="${NAMESPACE}">`,...urls.map(url=>[
    '  <url>',`    <loc>${xmlEscape(url)}</loc>`,'  </url>',
  ].join('\n')),'</urlset>',''].join('\n');
}

export function serializeSitemapIndex(paths:string[]):string {
  return [XML_HEADER,`<sitemapindex xmlns="${NAMESPACE}">`,...paths.map(path=>[
    '  <sitemap>',`    <loc>${xmlEscape(sitemapUrl(path))}</loc>`,'  </sitemap>',
  ].join('\n')),'</sitemapindex>',''].join('\n');
}

async function supplierPage(page:number):Promise<SupplierPage> {
  const configured=process.env.FASTAPI_INTERNAL_URL||process.env.BACKEND_URL||process.env.NEXT_PUBLIC_API_URL||'http://127.0.0.1:8000';
  const base=new URL(configured);
  if(!['http:','https:'].includes(base.protocol)||base.username||base.password)throw new Error('Invalid sitemap backend URL');
  const endpoint=new URL('/api/v1/directory/suppliers',base);
  endpoint.searchParams.set('page',String(page));endpoint.searchParams.set('limit',String(PAGE_SIZE));endpoint.searchParams.set('paginated','true');
  // Reuse the public projection; no contacts, profiles, categories or detail reads.
  const response=await fetch(endpoint,{cache:'no-store',signal:AbortSignal.timeout(5000),headers:{Accept:'application/json'},credentials:'omit'});
  if(!response.ok)throw new Error('Public supplier directory unavailable');
  const data=await response.json() as SupplierPage;
  if(!data||!Array.isArray(data.items)||!Number.isSafeInteger(data.total)||data.total<0||data.page!==page||data.limit!==PAGE_SIZE||data.items.length>PAGE_SIZE||
     !data.items.every(item=>item&&typeof item.id==='string'&&item.id.length>0&&item.verified===true))throw new Error('Invalid public supplier directory response');
  return data;
}

// Failed reads throw and are not cached as successful empty supplier lists.
const cachedSupplierPage=unstable_cache(supplierPage,['public-supplier-sitemap-v2'],{revalidate:3600});
const profilePaths=(items:PublicSupplier[])=>items.map(item=>`/suppliers/${encodeURIComponent(item.id)}`);

export async function buildSitemapXml():Promise<{xml:string;complete:boolean}> {
  try {
    const first=await cachedSupplierPage(1);
    const pageCount=Math.ceil(first.total/PAGE_SIZE);
    if(first.total>MAX_PROFILES_IN_ROOT){
      if(pageCount>10000)throw new Error('Public directory pagination limit exceeded');
      return {xml:serializeSitemapIndex(['/sitemaps/pages.xml',...Array.from({length:pageCount},(_,i)=>`/sitemaps/suppliers/${i+1}.xml`)]),complete:true};
    }
    const profiles=[...first.items];
    for(let page=2;page<=pageCount;page+=4){
      const batch=await Promise.all(Array.from({length:Math.min(4,pageCount-page+1)},(_,i)=>cachedSupplierPage(page+i)));
      profiles.push(...batch.flatMap(result=>result.items));
    }
    return {xml:serializeUrlset([...PUBLIC_SITEMAP_PATHS,...profilePaths(profiles)]),complete:true};
  }catch{
    console.warn('Sitemap supplier discovery unavailable; serving public pages with a short cache.');
    return {xml:serializeUrlset(PUBLIC_SITEMAP_PATHS),complete:false};
  }
}

export async function buildSupplierSitemapXml(page:number):Promise<string|null> {
  const result=await cachedSupplierPage(page);
  if(page>Math.max(1,Math.ceil(result.total/PAGE_SIZE)))return null;
  return serializeUrlset(profilePaths(result.items));
}

export function sitemapResponse(xml:string,complete=true):Response {
  return new Response(xml,{headers:{
    'Content-Type':'application/xml; charset=utf-8',
    'Cache-Control':`public, max-age=0, s-maxage=${complete?3600:60}, must-revalidate`,
    'X-Content-Type-Options':'nosniff',
  }});
}
