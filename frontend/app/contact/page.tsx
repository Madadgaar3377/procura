import {PublicMarketingPage} from '@/features/marketing/public-page';
import {marketingLocale,marketingMetadata} from '@/features/marketing/seo';
type Props={searchParams:Promise<{lang?:string|string[]}>};
export async function generateMetadata({searchParams}:Props){return marketingMetadata(await marketingLocale((await searchParams).lang),'/contact');}
export default async function Page({searchParams}:Props){return <PublicMarketingPage locale={await marketingLocale((await searchParams).lang)} kind="contact"/>;}
