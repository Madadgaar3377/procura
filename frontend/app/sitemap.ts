import type {MetadataRoute} from 'next';

const SITE = 'https://procumaterial.com';
const LAST_MODIFIED = '2026-11-09T09:36:38.438Z';

const publicRoutes: {
  path: string;
  changeFrequency: NonNullable<MetadataRoute.Sitemap[number]['changeFrequency']>;
  enPriority: number;
  arPriority: number;
}[] = [
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

function routeUrl(path: string, language: 'en' | 'ar') {
  const url = new URL(path, SITE);
  if (language === 'ar') url.searchParams.set('lang', 'ar');
  return url.href;
}

export default function sitemap(): MetadataRoute.Sitemap {
  return publicRoutes.flatMap(({path, changeFrequency, enPriority, arPriority}) => {
    const english = routeUrl(path, 'en');
    const arabic = routeUrl(path, 'ar');
    const alternates = {languages: {en: english, ar: arabic, 'x-default': english}};
    const shared = {lastModified: LAST_MODIFIED, changeFrequency, alternates};

    return [
      {url: english, ...shared, priority: enPriority},
      {url: arabic, ...shared, priority: arPriority},
    ];
  });
}
