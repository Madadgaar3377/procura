import 'server-only';
import type {Metadata} from 'next';
import {cookies,headers} from 'next/headers';
import type {MarketingLocale} from './copy';

export function configuredSiteURL():URL|undefined{
  const configured=process.env.NEXT_PUBLIC_SITE_URL||process.env.SITE_URL||process.env.FRONTEND_ORIGIN;
  if(!configured)return undefined;
  try{const url=new URL(configured);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return undefined;return new URL(url.origin);}catch{return undefined;}
}

export async function marketingLocale(preferred?:string|string[]):Promise<MarketingLocale>{
  if(preferred==='en'||preferred==='ar')return preferred;
  const selected=(await headers()).get('x-procura-marketing-locale');
  if(selected==='en'||selected==='ar')return selected;
  return (await cookies()).get('procura.locale')?.value==='ar'?'ar':'en';
}

const pages:Record<string,[string,string,string,string]>={
  '/':['Procura | Saudi B2B Procurement & RFQ Management','Procura | منصة المشتريات وطلبات عروض الأسعار في السعودية','Find relevant Saudi suppliers, create structured material RFQs and compare quotations. Procurement Without Complexity.','اكتشف الموردين المناسبين في السعودية وأنشئ طلبات عروض أسعار منظمة وقارن العروض عبر Procura. مشتريات الشركات بلا تعقيد.'],
  '/how-it-works':['How Procura Works | From RFQ to Quotation','كيف تعمل Procura | من الطلب إلى عرض السعر','Explore the buyer and supplier journey: requirements, category-based supplier matching, invitations and quotation comparison.','تعرّف على رحلة المشتري والمورد: المتطلبات ومطابقة الفئات والدعوات ومقارنة عروض الأسعار.'],
  '/suppliers':['Supplier Directory | Procura Saudi Arabia','دليل الموردين | Procura السعودية','Explore verified public supplier profiles and discover businesses relevant to your procurement requirements.','استكشف الملفات العامة للموردين الموثقين واكتشف الشركات المناسبة لمتطلبات مشترياتك.'],
  '/industries':['Civil, Electrical & Mechanical Procurement | Procura','المشتريات المدنية والكهربائية والميكانيكية | Procura','Organize construction material RFQs and discover civil, electrical and mechanical suppliers in Saudi Arabia.','نظّم طلبات عروض أسعار مواد البناء واكتشف الموردين المدنيين والكهربائيين والميكانيكيين في السعودية.'],
  '/pricing':['Supplier Memberships | Procura','عضويات الموردين | Procura','Review the supplier membership plans and entitlements currently configured on Procura.','اطّلع على خطط عضوية الموردين والمزايا المعدّة حالياً على Procura.'],
  '/faq':['Frequently Asked Questions | Procura','الأسئلة الشائعة | Procura','Answers about Procura RFQs, supplier capabilities, private pricing, company verification and memberships.','إجابات حول طلبات عروض الأسعار وقدرات الموردين والأسعار الخاصة والتحقق من الشركات والعضويات.'],
  '/about':['About Procura | Procurement Without Complexity.','عن Procura | المشتريات بلا تعقيد','Learn about Procura and its connected procurement workflow for Saudi business teams.','تعرّف على Procura ومسار المشتريات المتكامل لفرق الأعمال السعودية.'],
  '/contact':['Contact Procura','تواصل مع Procura','Contact the Procura team about buyer workflows, supplier accounts and platform membership.','تواصل مع فريق Procura بشأن مسارات المشترين وحسابات الموردين وعضويات المنصة.'],
  '/privacy':['Privacy Policy | Procura','سياسة الخصوصية | Procura','Read the privacy information available for the Procura platform.','اطّلع على معلومات الخصوصية المتاحة لمنصة Procura.'],
  '/terms':['Terms and Conditions | Procura','الشروط والأحكام | Procura','Read the terms and conditions available for using Procura.','اطّلع على الشروط والأحكام المتاحة لاستخدام Procura.'],
};

export function marketingMetadata(locale:MarketingLocale,path='/'):Metadata{
  const root=path.startsWith('/suppliers/')?'/suppliers':path;
  const selected=pages[root]||pages['/'];const base=configuredSiteURL();
  const title=selected[locale==='ar'?1:0];const description=selected[locale==='ar'?3:2];
  const canonical=base?new URL(`${path}?lang=${locale}`,base).href:undefined;
  return {title,description,applicationName:'Procura',...(base?{metadataBase:base}:{}),
    ...(base?{alternates:{canonical,languages:{en:new URL(path+'?lang=en',base).href,ar:new URL(path+'?lang=ar',base).href,'x-default':new URL(path,base).href}}}:{}),
    openGraph:{type:'website',siteName:'Procura',title,description,locale:locale==='ar'?'ar_SA':'en_SA',alternateLocale:locale==='ar'?'en_SA':'ar_SA',...(canonical?{url:canonical}:{})},
    twitter:{card:'summary_large_image',title,description},
  };
}

export function organizationSchema(locale:MarketingLocale){
  const base=configuredSiteURL();const description=pages['/'][locale==='ar'?3:2];
  return {'@context':'https://schema.org','@graph':[
    {'@type':'Organization',name:'Procura',description,slogan:'Procurement Without Complexity.',...(base?{url:base.href}:{})},
    {'@type':'SoftwareApplication',name:'Procura',applicationCategory:'BusinessApplication',operatingSystem:'Web',inLanguage:['en','ar'],description,...(base?{url:base.href}:{})},
  ]};
}
