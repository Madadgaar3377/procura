import type {MetadataRoute} from 'next';
import {configuredSiteURL} from '@/features/marketing/seo';

export default function sitemap(): MetadataRoute.Sitemap {
  const base = configuredSiteURL();
  const now = new Date();

  const publicRoutes: { path: string; priority: number; changeFrequency: 'daily' | 'weekly' | 'monthly' }[] = [
    { path: '/', priority: 1.0, changeFrequency: 'daily' },
    { path: '/how-it-works', priority: 0.9, changeFrequency: 'weekly' },
    { path: '/suppliers', priority: 0.9, changeFrequency: 'daily' },
    { path: '/industries', priority: 0.8, changeFrequency: 'weekly' },
    { path: '/pricing', priority: 0.8, changeFrequency: 'weekly' },
    { path: '/about', priority: 0.7, changeFrequency: 'monthly' },
    { path: '/contact', priority: 0.7, changeFrequency: 'monthly' },
    { path: '/faq', priority: 0.7, changeFrequency: 'weekly' },
    { path: '/privacy', priority: 0.3, changeFrequency: 'monthly' },
    { path: '/terms', priority: 0.3, changeFrequency: 'monthly' },
  ];

  return publicRoutes.flatMap(({ path, priority, changeFrequency }) => {
    const defaultUrl = new URL(path === '/' ? '/' : path, base).href;
    const enUrl = new URL(path === '/' ? '/' : path, base).href;
    const arUrl = new URL(`${path === '/' ? '' : path}?lang=ar`, base).href;

    return [
      {
        url: defaultUrl,
        lastModified: now,
        changeFrequency,
        priority,
        alternates: {
          languages: {
            en: enUrl,
            ar: arUrl,
            'x-default': defaultUrl,
          },
        },
      },
      {
        url: arUrl,
        lastModified: now,
        changeFrequency,
        priority: Number((priority * 0.95).toFixed(2)),
        alternates: {
          languages: {
            en: enUrl,
            ar: arUrl,
            'x-default': defaultUrl,
          },
        },
      },
    ];
  });
}
