import {InformationPage} from '@/features/marketing/information-page';
import {marketingLocale,marketingMetadata} from '@/features/marketing/seo';
type Props={searchParams:Promise<{lang?:string|string[]}>};
export async function generateMetadata({searchParams}:Props){return marketingMetadata(await marketingLocale((await searchParams).lang),'/how-it-works');}
export default async function Page({searchParams}:Props){return <InformationPage locale={await marketingLocale((await searchParams).lang)} slug="how-it-works"/>;}
