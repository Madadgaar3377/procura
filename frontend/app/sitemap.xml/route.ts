import {buildSitemapXml} from '@/features/marketing/sitemap';

export async function GET(): Promise<Response> {
  return new Response(await buildSitemapXml(), {
    status: 200,
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': 'public, max-age=0, s-maxage=3600, must-revalidate',
    },
  });
}
