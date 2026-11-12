import {buildSitemapXml,sitemapResponse} from '@/features/marketing/sitemap';

// Resolve current suppliers at runtime, not once during deployment builds.
export const dynamic='force-dynamic';

export async function GET(): Promise<Response> {
  const result=await buildSitemapXml();
  return sitemapResponse(result.xml,result.complete);
}
