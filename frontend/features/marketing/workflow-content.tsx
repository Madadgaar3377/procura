import Link from 'next/link';
import {IconArrowRight} from '@tabler/icons-react';
import {MarketingCTA} from './chrome';
import {marketingCopy, type MarketingLocale} from './copy';

const details = {
  en: {
    prepare: 'Before you create your first request',
    prepareBody: 'A little preparation helps suppliers understand your requirements and makes their quotations easier to compare.',
    checklist: [
      ['Describe the requirement', 'List each item with its specification, unit and quantity. Include dimensions, grades or acceptable alternatives where they matter.'],
      ['Plan the delivery', 'Have the delivery location and required timing ready. Make any project constraints clear in the request.'],
      ['Set a response deadline', 'Allow suppliers time to review the details and prepare a useful offer. Keep the quotation deadline distinct from the delivery requirement.'],
      ['Know your priorities', 'Decide which technical requirements are essential and which commercial terms you need to clarify when reviewing responses.'],
    ],
    supplierTitle: 'How it works for suppliers',
    supplierBody: 'Build your business profile, show what you can supply and respond to opportunities that fit your capabilities.',
    matchingTitle: 'How requests reach relevant suppliers',
    matchingNote: 'Keep your company capabilities and service coverage current so they reflect the opportunities your business can serve.',
    comparisonTitle: 'What to review before choosing a supplier',
    comparisonBody: 'Read the complete offer alongside your original requirement. A lower total is useful only when the proposed supply meets your needs.',
    comparison: [
      ['Technical fit', 'Check specifications, brands and any alternatives against the requested items. Clarify exceptions before making a decision.'],
      ['Commercial detail', 'Review quantities, unit rates, VAT, totals and the terms included in each submitted quotation.'],
      ['Delivery expectations', 'Check proposed delivery times and confirm that they work for your location and project schedule.'],
    ],
    example: 'A practical example',
    exampleTitle: 'From an electrical requirement to comparable offers.',
    exampleBody: 'A buyer needs electrical tools for a project. They select the relevant category and subcategory, add item descriptions and quantities, and set a delivery location and quotation deadline. Eligible suppliers with matching capabilities can receive invitations and submit their offers. The buyer then reviews the quoted specifications, prices and delivery terms before deciding how to proceed.',
    exampleNote: 'This is an illustrative workflow; supplier eligibility and available actions depend on your account and platform configuration.',
    cta: 'Ready to put the process to work?',
    ctaBody: 'Start with a clear requirement as a buyer, or complete your company profile to introduce your capabilities as a supplier.',
    buyer: 'Create your first RFQ', supplier: 'Join as a supplier', faq: 'Explore frequently asked questions',
  },
  ar: {
    prepare: 'قبل إنشاء طلبك الأول',
    prepareBody: 'يساعد التحضير المسبق الموردين على فهم احتياجاتك، ويجعل مقارنة عروضهم أسهل.',
    checklist: [
      ['وضّح المتطلبات', 'أدرج كل بند بمواصفاته ووحدته وكميته. أضف الأبعاد أو الدرجات أو البدائل المقبولة عندما تكون مؤثرة.'],
      ['خطط للتسليم', 'جهّز موقع التسليم والتوقيت المطلوب، ووضّح أي قيود خاصة بالمشروع في الطلب.'],
      ['حدّد موعد الرد', 'امنح الموردين وقتاً لمراجعة التفاصيل وإعداد عرض مناسب. ميّز بين الموعد النهائي للعروض ومتطلبات التسليم.'],
      ['حدّد أولوياتك', 'حدّد المتطلبات الفنية الأساسية والشروط التجارية التي تحتاج إلى توضيحها عند مراجعة الردود.'],
    ],
    supplierTitle: 'كيف تعمل المنصة للموردين',
    supplierBody: 'أنشئ ملف شركتك ووضّح إمكانات التوريد لديك واستجب للفرص المناسبة لأعمالك.',
    matchingTitle: 'كيف تصل الطلبات إلى الموردين المناسبين',
    matchingNote: 'حدّث إمكانات شركتك ونطاق خدمتها باستمرار حتى تعكس الفرص التي تستطيع خدمتها.',
    comparisonTitle: 'ما الذي تراجعه قبل اختيار المورد؟',
    comparisonBody: 'راجع العرض كاملاً إلى جانب احتياجك الأصلي. تكون القيمة الإجمالية الأقل مفيدة عندما يلبي التوريد المقترح متطلباتك.',
    comparison: [
      ['التوافق الفني', 'تحقق من المواصفات والعلامات التجارية والبدائل مقارنة بالبنود المطلوبة، واستوضح الاستثناءات قبل اتخاذ القرار.'],
      ['التفاصيل التجارية', 'راجع الكميات وأسعار الوحدات وضريبة القيمة المضافة والإجماليات والشروط الواردة في كل عرض مقدم.'],
      ['توقعات التسليم', 'تحقق من مواعيد التسليم المقترحة ومدى ملاءمتها لموقعك وجدول مشروعك.'],
    ],
    example: 'مثال عملي', exampleTitle: 'من احتياج للأدوات الكهربائية إلى عروض قابلة للمقارنة.',
    exampleBody: 'يحتاج مشترٍ إلى أدوات كهربائية لمشروع. يختار الفئة الرئيسية والفرعية المناسبة ويضيف أوصاف البنود وكمياتها، ثم يحدد موقع التسليم والموعد النهائي للعروض. يمكن للموردين المؤهلين ذوي الإمكانات المطابقة تلقي دعوات وتقديم عروضهم. بعدها يراجع المشتري المواصفات والأسعار وشروط التسليم قبل تحديد الخطوة التالية.',
    exampleNote: 'هذا مسار توضيحي؛ تعتمد أهلية الموردين والإجراءات المتاحة على حسابك وإعدادات المنصة.',
    cta: 'هل أنت مستعد للبدء؟',
    ctaBody: 'ابدأ باحتياج واضح بصفتك مشترياً، أو أكمل ملف شركتك للتعريف بإمكاناتك بصفتك مورداً.',
    buyer: 'أنشئ طلبك الأول', supplier: 'انضم كمورد', faq: 'استعرض الأسئلة الشائعة',
  },
} as const;

export function WorkflowContent({locale}: {locale: MarketingLocale}) {
  const d = details[locale];
  const c = marketingCopy[locale];
  return <div className="pm-about-sections">
    <section aria-labelledby="workflow-prepare">
      <div className="pm-about-section-heading"><h2 id="workflow-prepare">{d.prepare}</h2><p>{d.prepareBody}</p></div>
      <div className="pm-about-values">{d.checklist.map(([title, body], index) => <article key={title}><span className="pm-about-number">0{index + 1}</span><h3>{title}</h3><p>{body}</p></article>)}</div>
    </section>
    <section aria-labelledby="workflow-supplier">
      <div className="pm-about-section-heading"><h2 id="workflow-supplier">{d.supplierTitle}</h2><p>{d.supplierBody}</p></div>
      <ol className="pm-information-timeline">{c.supplierSteps.map((step, index) => <li key={step}><span>0{index + 1}</span><p>{step}</p></li>)}</ol>
      <p>{c.supplierNote}</p>
    </section>
    <section className="pm-about-local" aria-labelledby="workflow-matching">
      <h2 id="workflow-matching">{d.matchingTitle}</h2><p>{c.matchingBody}</p><p>{c.matchingExample}</p><p>{c.matchingNote}</p><p>{d.matchingNote}</p>
    </section>
    <section aria-labelledby="workflow-compare">
      <div className="pm-about-section-heading"><h2 id="workflow-compare">{d.comparisonTitle}</h2><p>{d.comparisonBody}</p></div>
      <div className="pm-about-values">{d.comparison.map(([title, body]) => <article key={title}><h3>{title}</h3><p>{body}</p></article>)}</div>
    </section>
    <section className="pm-about-card" aria-labelledby="workflow-example">
      <span className="pm-eyebrow">{d.example}</span><h2 id="workflow-example">{d.exampleTitle}</h2><p>{d.exampleBody}</p><p>{d.exampleNote}</p>
    </section>
    <section className="pm-about-cta" aria-labelledby="workflow-start">
      <h2 id="workflow-start">{d.cta}</h2><p>{d.ctaBody}</p>
      <div className="pm-information-actions"><MarketingCTA>{d.buyer}</MarketingCTA><MarketingCTA role="supplier" className="pm-button-secondary">{d.supplier}</MarketingCTA></div>
      <p><Link href={`/faq?lang=${locale}`} className="pm-text-link">{d.faq}<IconArrowRight className="pm-arrow" size={18}/></Link></p>
    </section>
  </div>;
}
