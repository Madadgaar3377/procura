import type {MetadataRoute} from 'next';
import {configuredSiteURL} from '@/features/marketing/seo';
export default function sitemap():MetadataRoute.Sitemap{
  const base=configuredSiteURL();if(!base)return [];
  return ['/','/how-it-works','/suppliers','/industries','/pricing','/about','/contact','/faq','/privacy','/terms'].flatMap(path=>['en','ar'].map(locale=>({
    url:new URL(`${path}?lang=${locale}`,base).href,alternates:{languages:{en:new URL(`${path}?lang=en`,base).href,ar:new URL(`${path}?lang=ar`,base).href}},
  })));
}
