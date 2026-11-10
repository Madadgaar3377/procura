import {CANONICAL_SITE_URL} from './seo';

type SitemapEntry = {
  path: string;
  changeFrequency: 'daily' | 'weekly' | 'monthly';
  enPriority: number;
  arPriority: number;
};

const publicPages: SitemapEntry[] = [
  {path: '/', changeFrequency: 'daily', enPriority: 1, arPriority: 0.95},
  {path: '/how-it-works', changeFrequency: 'weekly', enPriority: 0.9, arPriority: 0.85},
  {path: '/suppliers', changeFrequency: 'daily', enPriority: 0.9, arPriority: 0.85},
  {path: '/industries', changeFrequency: 'weekly', enPriority: 0.8, arPriority: 0.76},
  {path: '/pricing', changeFrequency: 'weekly', enPriority: 0.8, arPriority: 0.76},
  {path: '/about', changeFrequency: 'monthly', enPriority: 0.7, arPriority: 0.66},
  {path: '/contact', changeFrequency: 'monthly', enPriority: 0.7, arPriority: 0.66},
  {path: '/faq', changeFrequency: 'weekly', enPriority: 0.7, arPriority: 0.66},
  {path: '/privacy', changeFrequency: 'monthly', enPriority: 0.3, arPriority: 0.28},
  {path: '/terms', changeFrequency: 'monthly', enPriority: 0.3, arPriority: 0.28},
];

function xmlEscape(value: string): string {
  return value.replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&apos;',
  })[character]!);
}

function localizedUrl(path: string, locale: 'en' | 'ar'): string {
  const url = new URL(path, CANONICAL_SITE_URL);
  if (locale === 'ar') url.searchParams.set('lang', 'ar');
  return url.href;
}

async function publicSupplierIds(): Promise<string[]> {
  try {
    const configured = process.env.FASTAPI_INTERNAL_URL || process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
    const base = new URL(configured);
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) return [];

    const ids: string[] = [];
    for (let page = 1; page <= 250; page += 1) {
      const endpoint = new URL('/api/v1/directory/suppliers', base);
      endpoint.searchParams.set('page', String(page));
      endpoint.searchParams.set('limit', '100');
      const response = await fetch(endpoint, {
        next: {revalidate: 3600},
        signal: AbortSignal.timeout(2200),
        headers: {Accept: 'application/json'},
        credentials: 'omit',
      });
      if (!response.ok) return ids;
      const data: unknown = await response.json();
      if (!Array.isArray(data)) return ids;
      const verified = data.filter((item): item is {id: string; verified: true} =>
        !!item && typeof item === 'object' && 'id' in item && typeof item.id === 'string' && 'verified' in item && item.verified === true,
      );
      ids.push(...verified.map(supplier => supplier.id));
      if (data.length < 100) return ids;
    }
    return ids;
  } catch {
    return [];
  }
}

export async function buildSitemapXml(): Promise<string> {
  const profiles = [...new Set(await publicSupplierIds())].slice(0, 24990).map(id => ({
    path: `/suppliers/${encodeURIComponent(id)}`,
    changeFrequency: 'monthly' as const,
    enPriority: 0.6,
    arPriority: 0.57,
  }));
  const pages = [...publicPages, ...profiles];

  const urls = pages.flatMap(({path, changeFrequency, enPriority, arPriority}) => {
    const english = localizedUrl(path, 'en');
    const arabic = localizedUrl(path, 'ar');
    const alternatives = [
      ['en', english],
      ['ar', arabic],
      ['x-default', english],
    ] as const;

    return [
      {url: english, priority: enPriority},
      {url: arabic, priority: arPriority},
    ].map(({url, priority}) => [
      '  <url>',
      `    <loc>${xmlEscape(url)}</loc>`,
      ...alternatives.map(([language, alternate]) =>
        `    <xhtml:link rel="alternate" hreflang="${language}" href="${xmlEscape(alternate)}" />`,
      ),
      `    <changefreq>${changeFrequency}</changefreq>`,
      `    <priority>${priority}</priority>`,
      '  </url>',
    ].join('\n'));
  });

  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ...urls,
    '</urlset>',
    '',
  ].join('\n');
}
