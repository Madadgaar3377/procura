import {IconArrowRight} from '@tabler/icons-react';
import Link from 'next/link';
import {MarketingCTA} from './chrome';
import type {MarketingLocale} from './copy';

const content = {
  en: {
    purpose: 'Why we exist',
    title: 'More clarity at every buying decision.',
    paragraphs: [
      'Business procurement starts with a simple question: who can supply what we need? Finding the answer often means searching disconnected directories, repeating requirements and following up across separate conversations.',
      'Procura is built to bring that process together. Our purpose is to help Saudi businesses discover relevant suppliers, communicate clear requirements and review quotations with the context they need to move forward.',
      'We focus on the work between discovering a potential partner and making a commercial decision, giving buyers and suppliers a shared starting point for better business relationships.',
    ],
    audience: 'Built for both sides of business',
    audienceBody: 'A useful marketplace helps purchasing teams find the right fit and gives suppliers a clear way to explain what they offer.',
    audiences: [
      ['For buyers', 'Turn a business requirement into a structured sourcing process.', ['Explore suppliers by sector and capabilities.', 'Set out specifications, quantities and delivery requirements in an RFQ.', 'Review supplier responses and clarify the details before choosing a partner.']],
      ['For suppliers', 'Make your capabilities easier for business customers to discover.', ['Present your company, products and services in one profile.', 'Respond to relevant requests with a quotation tailored to the requirement.', 'Give prospective buyers the information they need for a productive conversation.']],
    ],
    principles: 'The principles behind Procura',
    values: [
      ['Clarity from the start', 'Clear requirements and organized company information help both sides understand the opportunity before investing time in a response.'],
      ['Relevant connections', 'Sourcing works better when supplier capabilities match the actual business need. We put discovery and structured requests at the center of the process.'],
      ['Informed decisions', 'Price is one part of a buying decision. Specifications, availability and delivery expectations also deserve attention when reviewing a quotation.'],
      ['Business relationships', 'A quotation is the beginning of a conversation. Our aim is to make that first exchange more focused so businesses can build relationships on a clearer foundation.'],
    ],
    localTitle: 'Designed around Saudi business needs.',
    localBody: 'Procura brings an English and Arabic experience to supplier discovery and procurement. Whether your team is exploring a new category or looking for another source of supply, the goal is the same: make business capabilities easier to find and purchasing requirements easier to understand.',
    localLink: 'Explore industries',
    ctaTitle: 'Start with your next business requirement.',
    ctaBody: 'Create a request as a buyer, or introduce your company as a supplier. Choose the starting point that fits your business.',
    buyer: 'Create an RFQ', supplier: 'Join as a supplier',
  },
  ar: {
    purpose: 'لماذا وُجدنا', title: 'وضوح أكبر في كل قرار شراء.',
    paragraphs: [
      'تبدأ مشتريات الشركات بسؤال بسيط: من يستطيع توفير ما نحتاجه؟ وقد يتطلب الوصول إلى الإجابة البحث في أدلة متفرقة وتكرار المتطلبات ومتابعة محادثات منفصلة.',
      'صُممت Procura لجمع هذه الخطوات في مسار واحد. هدفنا مساعدة الشركات السعودية على اكتشاف الموردين المناسبين وتوضيح احتياجاتها ومراجعة عروض الأسعار بالمعلومات اللازمة لاتخاذ الخطوة التالية.',
      'نركز على العمل الذي يربط اكتشاف شريك محتمل باتخاذ قرار تجاري، لنمنح المشترين والموردين نقطة انطلاق مشتركة لبناء علاقات عمل أفضل.',
    ],
    audience: 'للمشترين والموردين معاً',
    audienceBody: 'تساعد المنصة فرق المشتريات على إيجاد الخيارات المناسبة، وتمنح الموردين وسيلة واضحة لعرض إمكاناتهم.',
    audiences: [
      ['للمشترين', 'حوّل احتياج شركتك إلى عملية بحث وشراء منظمة.', ['استكشف الموردين بحسب القطاع والإمكانات.', 'حدد المواصفات والكميات ومتطلبات التسليم في طلب عرض سعر.', 'راجع ردود الموردين واستوضح التفاصيل قبل اختيار الشريك المناسب.']],
      ['للموردين', 'اجعل الوصول إلى إمكانات شركتك أسهل للعملاء من الشركات.', ['اعرض شركتك ومنتجاتك وخدماتك في ملف واحد.', 'استجب للطلبات المناسبة بعرض سعر يلائم المتطلبات.', 'قدّم للمشترين المعلومات التي تساعد على بدء محادثة مثمرة.']],
    ],
    principles: 'المبادئ التي توجه Procura',
    values: [
      ['الوضوح منذ البداية', 'تساعد المتطلبات الواضحة ومعلومات الشركة المنظمة الطرفين على فهم الفرصة قبل تخصيص الوقت لإعداد الرد.'],
      ['التواصل المناسب', 'تتحسن عملية الشراء عندما تتوافق إمكانات المورد مع احتياج الشركة الفعلي. لذلك نضع اكتشاف الموردين والطلبات المنظمة في صميم التجربة.'],
      ['قرارات مبنية على المعلومات', 'السعر جزء من قرار الشراء. وتستحق المواصفات والتوافر وتوقعات التسليم الاهتمام أيضاً عند مراجعة العروض.'],
      ['علاقات العمل', 'عرض السعر بداية للحوار. نهدف إلى جعل هذا التبادل الأول أكثر تركيزاً حتى تتمكن الشركات من بناء علاقاتها على أساس أوضح.'],
    ],
    localTitle: 'مصممة لاحتياجات الشركات السعودية.',
    localBody: 'تقدم Procura تجربة بالعربية والإنجليزية لاكتشاف الموردين وإدارة متطلبات الشراء. سواء كان فريقك يستكشف فئة جديدة أو يبحث عن مصدر توريد آخر، يبقى الهدف واحداً: تسهيل اكتشاف إمكانات الشركات وفهم متطلبات المشتريات.',
    localLink: 'استكشف القطاعات',
    ctaTitle: 'ابدأ باحتياج شركتك القادم.',
    ctaBody: 'أنشئ طلباً بصفتك مشترياً، أو عرّف بشركتك بصفتك مورداً. اختر نقطة البداية المناسبة لأعمالك.',
    buyer: 'أنشئ طلب عرض سعر', supplier: 'انضم كمورد',
  },
} as const;

export function AboutContent({locale}: {locale: MarketingLocale}) {
  const c = content[locale];
  return <div className="pm-about-sections">
    <section className="pm-about-story" aria-labelledby="about-purpose">
      <div><span className="pm-eyebrow">{c.purpose}</span><h2 id="about-purpose">{c.title}</h2></div>
      <div>{c.paragraphs.map(p => <p key={p}>{p}</p>)}</div>
    </section>
    <section aria-labelledby="about-audience">
      <div className="pm-about-section-heading"><h2 id="about-audience">{c.audience}</h2><p>{c.audienceBody}</p></div>
      <div className="pm-about-audience-grid">{c.audiences.map(([title, body, items]) => <article className="pm-about-card" key={title}>
        <h3>{title}</h3><p>{body}</p><ul>{items.map(item => <li key={item}>{item}</li>)}</ul>
      </article>)}</div>
    </section>
    <section aria-labelledby="about-principles">
      <div className="pm-about-section-heading"><h2 id="about-principles">{c.principles}</h2></div>
      <div className="pm-about-values">{c.values.map(([title, body], index) => <article key={title}>
        <span className="pm-about-number">0{index + 1}</span><h3>{title}</h3><p>{body}</p>
      </article>)}</div>
    </section>
    <section className="pm-about-local" aria-labelledby="about-local">
      <h2 id="about-local">{c.localTitle}</h2><p>{c.localBody}</p>
      <Link href={`/industries?lang=${locale}`} className="pm-text-link">{c.localLink}<IconArrowRight className="pm-arrow" size={18}/></Link>
    </section>
    <section className="pm-about-cta" aria-labelledby="about-start">
      <h2 id="about-start">{c.ctaTitle}</h2><p>{c.ctaBody}</p>
      <div className="pm-information-actions"><MarketingCTA>{c.buyer}</MarketingCTA><MarketingCTA role="supplier" className="pm-button-secondary">{c.supplier}</MarketingCTA></div>
    </section>
  </div>;
}
