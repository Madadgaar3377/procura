import 'server-only';
import type {Metadata} from 'next';
import {cookies,headers} from 'next/headers';
import {marketingCopy, type MarketingLocale} from './copy';

export const CANONICAL_SITE_URL = 'https://procumaterial.com';

export function configuredSiteURL(): URL {
  const configured = process.env.NEXT_PUBLIC_SITE_URL || process.env.SITE_URL || process.env.FRONTEND_ORIGIN;
  if (configured) {
    try {
      const url = new URL(configured);
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
        return new URL(url.origin);
      }
    } catch {
      /* Fallback to canonical production domain */
    }
  }
  return new URL(CANONICAL_SITE_URL);
}

export async function marketingLocale(preferred?:string|string[]):Promise<MarketingLocale>{
  if(preferred==='en'||preferred==='ar')return preferred;
  const selected=(await headers()).get('x-procura-marketing-locale');
  if(selected==='en'||selected==='ar')return selected;
  return (await cookies()).get('procura.locale')?.value==='ar'?'ar':'en';
}

const pages: Record<string, [string, string, string, string]> = {
  '/': [
    'Procumaterial | Saudi Arabia B2B Procurement & Supplier Platform',
    'Procumaterial | منصة المشتريات وموردي الشركات في السعودية',
    'Connect with trusted Saudi suppliers, submit procurement RFQs, discover Civil, Electrical and Mechanical materials, and compare competitive supplier quotations through Procumaterial.',
    'تواصل مع موردين موثوقين في السعودية، وقدّم طلبات عروض أسعار لمواد الإنشاءات والكهرباء والميكانيكا، وقارن العروض التنافسية عبر Procumaterial.'
  ],
  '/how-it-works': [
    'How Procumaterial Works | Sourcing & RFQ Workflow in Saudi Arabia',
    'كيف تعمل Procumaterial | مسار المشتريات وطلبات العروض في السعودية',
    'Learn how Procumaterial simplifies procurement: define material requirements, auto-match verified Saudi suppliers, receive compliant offers, and compare quotations.',
    'تعرّف على كيفية عمل Procumaterial: حدد متطلبات المواد، وطابق الموردين المعتمدين في السعودية تلقائياً، واستلم عروض الأسعار وقارنها بسهولة.'
  ],
  '/suppliers': [
    'Saudi Suppliers Directory | Procumaterial B2B Sourcing',
    'دليل الموردين في السعودية | منصة Procumaterial',
    'Explore verified Saudi supplier profiles across Civil, Electrical, and Mechanical disciplines. Connect with qualified distributors and manufacturers for your RFQs.',
    'استكشف ملفات الموردين المعتمدين في السعودية للتخصصات المدنية والكهربائية والميكانيكية. تواصل مع موزعين ومصنّعين مؤهلين لتلبية متطلباتك.'
  ],
  '/industries': [
    'Civil, Electrical & Mechanical Material Procurement | Procumaterial',
    'مشتريات المواد المدنية والكهربائية والميكانيكية | Procumaterial',
    'Source construction, civil, electrical, and mechanical materials across Saudi Arabia. Connect with verified suppliers for project-ready procurement.',
    'اعثر على مواد البناء والأعمال المدنية والكهربائية والميكانيكية في جميع أنحاء السعودية. تواصل مع موردين معتمدين لتوريد مواد المشاريع.'
  ],
  '/pricing': [
    'Supplier Memberships & Sourcing Solutions | Procumaterial',
    'عضويات الموردين وحلول التوريد | Procumaterial',
    'Review transparent supplier membership plans and procurement features on Procumaterial. Expand your reach across Saudi enterprise projects.',
    'اطّلع على خطط عضوية الموردين ومزايا التوريد على منصة Procumaterial. وسّع نطاق وصولك لمشاريع الأعمال في المملكة.'
  ],
  '/faq': [
    'Frequently Asked Questions | Procumaterial Saudi Procurement',
    'الأسئلة الشائعة | Procumaterial للمشتريات السعودية',
    'Find answers to common questions about RFQ creation, supplier matching, private price books, company verification, and procurement workflows on Procumaterial.',
    'إجابات شاملة حول إنشاء طلبات عروض الأسعار، مطابقة الموردين، قوائم الأسعار الخاصة، توثيق الشركات، ومسارات المشتريات على Procumaterial.'
  ],
  '/about': [
    'About Procumaterial | Connecting Saudi Businesses with Trusted Suppliers',
    'عن Procumaterial | نربط الشركات السعودية بموردين موثوقين',
    'Discover Procumaterial, the Saudi B2B procurement platform built to streamline material sourcing, supplier discovery, and quotation evaluation for enterprise teams.',
    'تعرّف على منصة Procumaterial للمشتريات بين الشركات في السعودية، المصممة لتبسيط توريد المواد واكتشاف الموردين ومقارنة عروض الأسعار.'
  ],
  '/contact': [
    'Contact Procumaterial | Saudi Procurement & Sourcing Support',
    'تواصل مع Procumaterial | دعم المشتريات والتوريد في السعودية',
    'Get in touch with the Procumaterial team for buyer onboarding, supplier registration support, and enterprise sourcing partnerships in Saudi Arabia.',
    'تواصل مع فريق Procumaterial للاستفسار عن انضمام المشترين، تسجيل الموردين، وشراكات التوريد للشركات في المملكة العربية السعودية.'
  ],
  '/privacy': [
    'Privacy Policy | Procumaterial Saudi B2B Platform',
    'سياسة الخصوصية | منصة Procumaterial',
    'Read the Procumaterial Privacy Policy to learn how business data, accounts, and procurement records are protected and handled securely.',
    'اطّلع على سياسة الخصوصية لمنصة Procumaterial لمعرفة كيفية حماية وإدارة بيانات الأعمال والحسابات وسجلات المشتريات بأمان.'
  ],
  '/terms': [
    'Terms and Conditions | Procumaterial Platform',
    'الشروط والأحكام | منصة Procumaterial',
    'Read the terms of service and conditions for using Procumaterial, the Saudi B2B procurement and supplier sourcing platform.',
    'اقرأ شروط الاستخدام والأحكام الخاصة بمنصة Procumaterial لمشتريات وتوريد الشركات في السعودية.'
  ],
};

export function marketingMetadata(locale: MarketingLocale, path = '/'): Metadata {
  const root = path.startsWith('/suppliers/') ? '/suppliers' : path;
  const selected = pages[root] || pages['/'];
  const base = configuredSiteURL();
  const title = selected[locale === 'ar' ? 1 : 0];
  const description = selected[locale === 'ar' ? 3 : 2];
  const canonical = new URL(path === '/' ? (locale === 'ar' ? '/?lang=ar' : '/') : `${path}${locale === 'ar' ? '?lang=ar' : ''}`, base).href;

  return {
    title,
    description,
    applicationName: 'Procumaterial',
    metadataBase: base,
    alternates: {
      canonical,
      languages: {
        en: new URL(path === '/' ? '/' : path, base).href,
        ar: new URL(`${path === '/' ? '' : path}?lang=ar`, base).href,
        'x-default': new URL(path === '/' ? '/' : path, base).href,
      },
    },
    openGraph: {
      type: 'website',
      siteName: 'Procumaterial',
      title,
      description,
      locale: locale === 'ar' ? 'ar_SA' : 'en_US',
      alternateLocale: locale === 'ar' ? 'en_US' : 'ar_SA',
      url: canonical,
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
    },
  };
}

export function organizationSchema(locale: MarketingLocale) {
  const base = configuredSiteURL();
  const isArabic = locale === 'ar';
  const description = pages['/'][isArabic ? 3 : 2];

  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization',
        '@id': `${base.origin}/#organization`,
        name: 'Procumaterial',
        alternateName: isArabic ? 'بروكيو ماتيريال' : 'Procumaterial Saudi Arabia',
        url: base.origin,
        logo: `${base.origin}/opengraph-image`,
        slogan: isArabic
          ? 'ربط الشركات السعودية بموردين موثوقين.'
          : 'Connecting Saudi Businesses with Trusted Suppliers.',
        description,
        areaServed: {
          '@type': 'Country',
          name: 'Saudi Arabia',
        },
        knowsAbout: [
          'Civil Construction Materials',
          'Electrical Procurement',
          'Mechanical Equipment & Supplies',
          'B2B Supplier Sourcing',
          'Request for Quotation (RFQ)',
        ],
      },
      {
        '@type': 'WebSite',
        '@id': `${base.origin}/#website`,
        url: base.origin,
        name: 'Procumaterial',
        inLanguage: ['en', 'ar'],
        publisher: {
          '@id': `${base.origin}/#organization`,
        },
      },
      {
        '@type': 'SoftwareApplication',
        name: 'Procumaterial',
        applicationCategory: 'BusinessApplication',
        operatingSystem: 'Web',
        inLanguage: ['en', 'ar'],
        description,
        url: base.origin,
      },
    ],
  };
}

export function breadcrumbSchema(locale: MarketingLocale, items: { name: string; path: string }[]) {
  const base = configuredSiteURL();
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name: item.name,
      item: new URL(item.path, base).href,
    })),
  };
}

export function webPageSchema(locale: MarketingLocale, path: string, name: string) {
  const base = configuredSiteURL();
  const selected = pages[path] || pages['/'];
  const description = selected[locale === 'ar' ? 3 : 2];
  const url = new URL(path === '/' ? (locale === 'ar' ? '/?lang=ar' : '/') : `${path}${locale === 'ar' ? '?lang=ar' : ''}`, base).href;

  return {
    '@context': 'https://schema.org',
    '@type': 'WebPage',
    name,
    description,
    url,
    inLanguage: locale === 'ar' ? 'ar' : 'en',
    isPartOf: {
      '@type': 'WebSite',
      name: 'Procumaterial',
      url: base.origin,
    },
  };
}

export function faqSchema(locale: MarketingLocale) {
  const c = marketingCopy[locale];
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: c.faqs.map(([question, answer]) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: answer,
      },
    })),
  };
}
