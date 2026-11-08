import {InformationPage} from '@/features/marketing/information-page';
import {marketingLocale, marketingMetadata, breadcrumbSchema, webPageSchema} from '@/features/marketing/seo';

type Props = { searchParams: Promise<{ lang?: string | string[] }> };

export async function generateMetadata({searchParams}: Props) {
  return marketingMetadata(await marketingLocale((await searchParams).lang), '/industries');
}

export default async function Page({searchParams}: Props) {
  const locale = await marketingLocale((await searchParams).lang);
  const isAr = locale === 'ar';
  const breadcrumb = breadcrumbSchema(locale, [
    { name: isAr ? 'الرئيسية' : 'Home', path: isAr ? '/?lang=ar' : '/' },
    { name: isAr ? 'المشتريات المدنية والكهربائية والميكانيكية' : 'Civil, Electrical & Mechanical Procurement', path: isAr ? '/industries?lang=ar' : '/industries' },
  ]);
  const webpage = webPageSchema(locale, '/industries', isAr ? 'المشتريات المدنية والكهربائية والميكانيكية | Procumaterial' : 'Civil, Electrical & Mechanical Procurement | Procumaterial');

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
      <InformationPage locale={locale} slug="industries" />
    </>
  );
}
