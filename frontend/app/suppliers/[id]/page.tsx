import {PublicMarketingPage} from '@/features/marketing/public-page';
import {configuredSiteURL,marketingLocale,marketingMetadata} from '@/features/marketing/seo';
import {readPublicSupplier} from '@/features/suppliers/public-supplier';

type Props={params:Promise<{id:string}>;searchParams:Promise<{lang?:string|string[]}>};
export async function generateMetadata({params,searchParams}:Props){
 const id=(await params).id;const locale=await marketingLocale((await searchParams).lang);
 const metadata=marketingMetadata(locale,'/suppliers/'+encodeURIComponent(id));
 const supplier=await readPublicSupplier(id);
 if(!supplier)return {...metadata,robots:{index:false,follow:true}};
 const title=`${supplier.name} | Procura`;
 const categories=(locale==='ar'?supplier.main_categories_ar:supplier.main_categories)||[];
 const description=supplier.profile?.description?.slice(0,180)||(categories.length?`${supplier.name} · ${categories.join(', ')}`:metadata.description);
 return {...metadata,title,description,openGraph:{...metadata.openGraph,title,description:description||undefined},twitter:{...metadata.twitter,title,description:description||undefined}};
}
export default async function Page({params,searchParams}:Props){
 const id=(await params).id;const locale=await marketingLocale((await searchParams).lang);
 const supplier=await readPublicSupplier(id);const base=configuredSiteURL();
 const schema=supplier?{'@context':'https://schema.org','@type':'Organization',name:supplier.name,
  ...(base?{url:new URL('/suppliers/'+encodeURIComponent(id)+'?lang='+locale,base).href}:{}),
  ...(supplier.profile?.description?{description:supplier.profile.description}:{}),
  ...(supplier.profile?.business_phone?{telephone:supplier.profile.business_phone}:{}),
  ...(supplier.profile?.business_email?{email:supplier.profile.business_email}:{}),
 }:null;
 return <>{schema&&<script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(schema).replace(/</g,'\\u003c')}}/>}<PublicMarketingPage locale={locale} kind="directory" supplierId={id} supplierInitial={supplier||undefined}/></>;
}
