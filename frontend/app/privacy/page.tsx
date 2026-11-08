import {InformationPage} from '@/features/marketing/information-page';
import {marketingLocale, marketingMetadata, breadcrumbSchema, webPageSchema} from '@/features/marketing/seo';

type Props = { searchParams: Promise<{ lang?: string | string[] }> };

export async function generateMetadata({searchParams}: Props) {
  return marketingMetadata(await marketingLocale((await searchParams).lang), '/privacy');
}

export default async function Page({searchParams}: Props) {
  const locale = await marketingLocale((await searchParams).lang);
  const isAr = locale === 'ar';
  const breadcrumb = breadcrumbSchema(locale, [
    { name: isAr ? 'الرئيسية' : 'Home', path: isAr ? '/?lang=ar' : '/' },
    { name: isAr ? 'سياسة الخصوصية' : 'Privacy Policy', path: isAr ? '/privacy?lang=ar' : '/privacy' },
  ]);
  const webpage = webPageSchema(locale, '/privacy', isAr ? 'سياسة الخصوصية | Procumaterial' : 'Privacy Policy | Procumaterial');

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
      <InformationPage locale={locale} slug="privacy" />
    </>
  );
}
