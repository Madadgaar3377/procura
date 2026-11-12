import {buildSupplierSitemapXml,sitemapResponse} from '@/features/marketing/sitemap';
export const dynamic='force-dynamic';
export async function GET(_request:Request,{params}:{params:Promise<{page:string}>}){
  const page=(await params).page;
  if(!/^[1-9]\d{0,3}\.xml$/.test(page)&&page!=='10000.xml')return new Response(null,{status:404});
  try{
    const xml=await buildSupplierSitemapXml(Number(page.slice(0,-4)));
    return xml?sitemapResponse(xml):new Response(null,{status:404});
  }catch{return new Response('Supplier sitemap temporarily unavailable',{status:503,headers:{'Retry-After':'60','Cache-Control':'no-store'}});}
}
