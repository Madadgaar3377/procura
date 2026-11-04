import RouteView from "@/components/route-view";
import {marketingLocale,marketingMetadata} from '@/features/marketing/seo';

export async function generateMetadata({params,searchParams}:{params:Promise<{segments?:string[]}>;searchParams:Promise<{lang?:string|string[]}>}){
  const path='/'+((await params).segments||[]).join('/');
  const publicPaths=['/how-it-works','/suppliers','/industries','/pricing','/about','/contact','/faq','/privacy','/terms'];
  if(publicPaths.includes(path)||path.startsWith('/suppliers/'))return marketingMetadata(await marketingLocale((await searchParams).lang),path);
  return {robots:{index:false,follow:false}};
}

export default async function RoutedPage({ params }: { params: Promise<{ segments?: string[] }> }) {
  const { segments = [] } = await params;
  return <RouteView segments={segments} />;
}
