import {PUBLIC_SITEMAP_PATHS,serializeUrlset,sitemapResponse} from '@/features/marketing/sitemap';
export function GET(){return sitemapResponse(serializeUrlset(PUBLIC_SITEMAP_PATHS));}
