import {MarketingHeader,MarketingFooter} from '@/features/marketing/chrome';
import {marketingLocale} from '@/features/marketing/seo';
import {marketingFont} from '@/features/marketing/fonts';

export default async function Loading(){
 const locale=await marketingLocale();const ar=locale==='ar';
 return <div className={`procura-marketing ${marketingFont.variable}`} lang={locale} dir={ar?'rtl':'ltr'}><MarketingHeader locale={locale}/><main id="main-content"><section className="supplier-profile pm-container" role="status" aria-label={ar?'جارٍ تحميل ملف الشركة':'Loading company profile'}><h1 className="sr-only">{ar?'ملف الشركة':'Company profile'}</h1><div className="sp-header sp-skeleton"><i/><i/><i/></div><div className="sp-columns"><div className="sp-panel sp-skeleton"><i/><i/><i/></div><div className="sp-panel sp-skeleton"><i/><i/><i/></div></div><div className="sp-related-grid">{[0,1,2].map(index=><div className="sp-panel sp-skeleton" key={index}><i/><i/></div>)}</div></section></main><MarketingFooter locale={locale}/></div>;
}
