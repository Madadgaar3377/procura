import MarketingHome from "@/components/marketing-home";
import {marketingLocale,marketingMetadata,organizationSchema} from '@/features/marketing/seo';

type Props={searchParams:Promise<{lang?:string|string[]}>};
export async function generateMetadata({searchParams}:Props){return marketingMetadata(await marketingLocale((await searchParams).lang));}
export default async function Home({searchParams}:Props) {
  const locale=await marketingLocale((await searchParams).lang);
  return <><script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(organizationSchema(locale)).replace(/</g,'\\u003c')}}/><MarketingHome locale={locale}/></>;
}
