import {PublicMarketingPage} from '@/features/marketing/public-page';
import {marketingLocale, marketingMetadata, breadcrumbSchema, webPageSchema} from '@/features/marketing/seo';

type Props = { searchParams: Promise<{ lang?: string | string[] }> };

export async function generateMetadata({searchParams}: Props) {
  return marketingMetadata(await marketingLocale((await searchParams).lang), '/contact');
}

export default async function Page({searchParams}: Props) {
  const locale = await marketingLocale((await searchParams).lang);
  const isAr = locale === 'ar';
  const breadcrumb = breadcrumbSchema(locale, [
    { name: isAr ? 'الرئيسية' : 'Home', path: isAr ? '/?lang=ar' : '/' },
    { name: isAr ? 'تواصل مع Procumaterial' : 'Contact Procumaterial', path: isAr ? '/contact?lang=ar' : '/contact' },
  ]);
  const webpage = webPageSchema(locale, '/contact', isAr ? 'تواصل مع Procumaterial | Procumaterial' : 'Contact Procumaterial | Procumaterial');

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb).replace(/</g, '\\u003c') }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(webpage).replace(/</g, '\\u003c') }}
      />
      <PublicMarketingPage locale={locale} kind="contact" />
    </>
  );
}
