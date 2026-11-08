import {Suspense} from 'react';
import {AboutContent} from './about-content';
import {WorkflowContent} from './workflow-content';
import Link from 'next/link';
import {IconArrowRight,IconCheck,IconChevronDown} from '@tabler/icons-react';
import {marketingCopy,type MarketingLocale} from './copy';
import {MarketingCTA,MarketingFooter,MarketingHeader} from './chrome';
import {SectorCards,DataSkeleton} from './public-data';
import {marketingFont} from './fonts';

export type InformationSlug='industries'|'how-it-works'|'faq'|'about'|'privacy'|'terms';

export function InformationPage({locale,slug}:{locale:MarketingLocale;slug:InformationSlug}){
  const c=marketingCopy[locale];const arabic=locale==='ar';
  const legal=slug==='privacy'||slug==='terms';
  const title=slug==='industries'?c.sectorTitle:slug==='how-it-works'?c.workflowTitle:slug==='faq'?c.faqTitle:slug==='about'?(arabic?'المشتريات بلا تعقيد.':'Procurement Without Complexity.'):slug==='privacy'?(arabic?'إشعار الخصوصية':'Privacy notice'):(arabic?'شروط الاستخدام':'Terms of use');
  const eyebrow=slug==='industries'?c.nav[3]:slug==='how-it-works'?c.nav[2]:slug==='faq'?c.footerLinks[12]:slug==='about'?c.footerLinks[10]:c.footerGroups[4];
  const body=slug==='industries'?c.sectorBody:slug==='how-it-works'?c.workflowBody:slug==='faq'?(arabic?'إجابات عملية للمشترين والموردين وفرق العمل.':'Practical answers for buyers, suppliers and business teams.'):slug==='about'?(arabic?'تجمع Procumaterial اكتشاف الموردين ومتطلبات الشراء وعروض الأسعار في مسار واضح للشركات السعودية.':'Procumaterial brings supplier discovery, procurement requirements and quotation reviews into one clear workflow for Saudi businesses.'):slug==='privacy'?(arabic?'يصف هذا الإشعار التوضيحي مبادئ الخصوصية المقصودة لمنصة مشتريات بين الشركات. يجب مراجعة سياسة الإنتاج واعتمادها من الجهة المشغلة قبل الإطلاق.':'This demonstration describes the privacy principles intended for a business procurement marketplace. A production policy must be reviewed and approved by the operating entity before launch.'):(arabic?'هذه الشروط التوضيحية استرشادية. يجب إعداد الشروط النهائية واعتمادها من الجهة المشغلة قبل الإطلاق الفعلي.':'These demonstration terms are illustrative. Final user terms must be prepared and approved by the operating entity before a production launch.');
  const legalItems=slug==='privacy'?(arabic?['تُستخدم معلومات حسابات الأعمال لتشغيل ملفات الشركات ومسارات المشتريات.','تُشارك تفاصيل الطلب مع الموردين المدعوين للرد.','تتاح سجلات التحقق للمراجعين المخوّلين في المنصة فقط.','تتطلب قواعد الاحتفاظ والوصول والمتطلبات القانونية للإنتاج اعتماد الجهة المشغلة.']:['Business account information is used to operate company profiles and procurement workflows.','RFQ details are shared with suppliers invited to respond.','Verification records are available only to authorized platform reviewers.','Production retention, access and legal requirements require operator approval.']):(arabic?['تتحمل الشركات مسؤولية دقة معلومات الشركة وعروض الأسعار.','يدير المشترون والموردون قراراتهم واتفاقياتهم التجارية بأنفسهم.','تدعم المنصة اكتشاف الموردين وتبادل طلبات عروض الأسعار المنظمة.','تخضع شروط العضويات والاتصالات لإعدادات المنصة.']:['Businesses are responsible for accurate company and quotation information.','Buyers and suppliers manage their own commercial decisions and agreements.','The platform supports discovery and structured RFQ exchange.','Membership and communication terms are subject to platform configuration.']);
  return <div className={`procura-marketing ${marketingFont.variable}`} lang={locale} dir={arabic?'rtl':'ltr'} id="top"><MarketingHeader locale={locale}/><main id="main-content"><section className="pm-information-hero"><div className="pm-container"><span className="pm-eyebrow"><i/>{eyebrow}</span><h1>{title}</h1><p>{body}</p></div></section><section className="pm-section pm-information-content"><div className="pm-container">
    {slug==='industries'&&<><h2 className="sr-only">{c.nav[3]}</h2><Suspense fallback={<DataSkeleton locale={locale} kind="sectors"/>}><SectorCards locale={locale}/></Suspense></>}
    {slug==='how-it-works'&&<><ol className="pm-information-timeline">{c.steps.map(([heading,description],index)=><li key={heading}><span>0{index+1}</span><div><h2>{heading}</h2><p>{description}</p></div></li>)}</ol><div className="pm-information-actions"><MarketingCTA>{c.createRFQ}</MarketingCTA><MarketingCTA role="supplier" className="pm-button-secondary">{c.joinSupplier}</MarketingCTA></div></>}
    {slug==='faq'&&<div className="pm-faq-list pm-information-faq" id="faq">{c.faqs.map(([question,answer])=><details key={question}><summary>{question}<IconChevronDown size={20}/></summary><div><p>{answer}</p></div></details>)}<Link href="/contact" className="pm-text-link">{c.faqContact}<IconArrowRight className="pm-arrow" size={18}/></Link></div>}
    {slug==='about'&&<div className="pm-about-layout"><div><h2>{c.trustTitle}</h2><p>{c.heroBody}</p><Link href="/how-it-works" className="pm-text-link">{c.nav[2]}<IconArrowRight className="pm-arrow" size={18}/></Link></div><ul className="pm-feature-list">{c.trustItems.map(item=><li key={item}><IconCheck size={18}/>{item}</li>)}</ul></div>}
    {slug==='about'&&<AboutContent locale={locale}/>}
    {slug==='how-it-works'&&<WorkflowContent locale={locale}/>}
    {legal&&<div className="pm-legal-content"><ul>{legalItems.map(item=><li key={item}>{item}</li>)}</ul><Link href="/contact" className="pm-text-link">{c.faqContact}<IconArrowRight className="pm-arrow" size={18}/></Link></div>}
  </div></section></main><MarketingFooter locale={locale}/></div>;
}
