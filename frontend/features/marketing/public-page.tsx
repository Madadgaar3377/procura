import type {Schemas} from '@/lib/api/generated/schema';
import {Suspense} from 'react';
import ContactInquiryForm from '@/features/contact/inquiry';
import LiveDirectory from '@/features/suppliers/directory';
import SupplierProfile from '@/features/suppliers/profile';
import {MarketingHeader} from './chrome';
import {MarketingFooterServer as MarketingFooter} from './footer';
import {marketingFont} from './fonts';
import {marketingCopy,type MarketingLocale} from './copy';
import {MembershipCards,DataSkeleton} from './public-data';

export function PublicMarketingPage({locale,kind,supplierId,supplierInitial}:{locale:MarketingLocale;kind:'directory'|'pricing'|'contact';supplierId?:string;supplierInitial?:Schemas['PublicSupplier']}){
  const c=marketingCopy[locale];
  return <div className={`procura-marketing ${marketingFont.variable}`} lang={locale} dir={locale==='ar'?'rtl':'ltr'} id="top"><MarketingHeader locale={locale}/><main id="main-content">
    {kind==='directory'&&(supplierId?<SupplierProfile id={supplierId} initialData={supplierInitial}/>:<LiveDirectory/>)}
    {kind==='contact'&&<ContactInquiryForm/>}
    {kind==='pricing'&&<><section className="pm-information-hero"><div className="pm-container"><span className="pm-eyebrow"><i/>{c.membershipsEyebrow}</span><h1>{c.membershipsTitle}</h1><p>{c.membershipsBody}</p></div></section><section className="pm-section"><div className="pm-container"><Suspense fallback={<DataSkeleton locale={locale} kind="plans"/>}><MembershipCards locale={locale}/></Suspense></div></section></>}
  </main><MarketingFooter locale={locale}/></div>;
}
