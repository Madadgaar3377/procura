import {PublicMarketingPage} from '@/features/marketing/public-page';
import {marketingLocale, marketingMetadata, breadcrumbSchema, webPageSchema} from '@/features/marketing/seo';

type Props = { searchParams: Promise<{ lang?: string | string[] }> };

export async function generateMetadata({searchParams}: Props) {
  return marketingMetadata(await marketingLocale((await searchParams).lang), '/pricing');
}

export default async function Page({searchParams}: Props) {
  const locale = await marketingLocale((await searchParams).lang);
  const isAr = locale === 'ar';
  const breadcrumb = breadcrumbSchema(locale, [
    { name: isAr ? 'الرئيسية' : 'Home', path: isAr ? '/?lang=ar' : '/' },
    { name: isAr ? 'عضويات الموردين' : 'Supplier Memberships', path: isAr ? '/pricing?lang=ar' : '/pricing' },
  ]);
  const webpage = webPageSchema(locale, '/pricing', isAr ? 'عضويات الموردين | Procumaterial' : 'Supplier Memberships | Procumaterial');

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
      <PublicMarketingPage locale={locale} kind="pricing" />
    </>
  );
}
