import {InformationPage} from '@/features/marketing/information-page';
import {marketingLocale, marketingMetadata, breadcrumbSchema, webPageSchema} from '@/features/marketing/seo';

type Props = {searchParams: Promise<{lang?: string | string[]; discipline?: string | string[]}>};

export async function generateMetadata({searchParams}: Props) {
  const locale = await marketingLocale((await searchParams).lang);
  const metadata = marketingMetadata(locale, '/industries');
  return {
    ...metadata,
    title: locale === 'ar' ? 'استكشف المواد حسب القطاع' : 'Explore Materials by Industry',
    description: locale === 'ar'
      ? 'تصفح فئات المواد المدنية والكهربائية والميكانيكية وابحث في دليل المواد الرئيسي للعثور على الموردين ذوي الصلة.'
      : 'Browse Civil, Electrical, and Mechanical categories, search the Material Master, and discover relevant suppliers.',
  };
}

export default async function Page({searchParams}: Props) {
  const params = await searchParams;
  const locale = await marketingLocale(params.lang);
  const isAr = locale === 'ar';
  const title = isAr ? 'استكشف المواد حسب القطاع | Procumaterial' : 'Explore Materials by Industry | Procumaterial';
  const breadcrumb = breadcrumbSchema(locale, [
    {name: isAr ? 'الرئيسية' : 'Home', path: isAr ? '/?lang=ar' : '/'},
    {name: isAr ? 'استكشف المواد حسب القطاع' : 'Explore Materials by Industry', path: isAr ? '/industries?lang=ar' : '/industries'},
  ]);
  const webpage = webPageSchema(locale, '/industries', title);
  const initialDiscipline = Array.isArray(params.discipline) ? params.discipline[0] : params.discipline;
  return <>
    <script type="application/ld+json" dangerouslySetInnerHTML={{__html: JSON.stringify(breadcrumb).replace(/</g, '\\u003c')}} />
    <script type="application/ld+json" dangerouslySetInnerHTML={{__html: JSON.stringify(webpage).replace(/</g, '\\u003c')}} />
    <InformationPage locale={locale} slug="industries" initialDiscipline={initialDiscipline}/>
  </>;
}
