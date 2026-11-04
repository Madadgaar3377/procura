import type {MetadataRoute} from 'next';
import {configuredSiteURL} from '@/features/marketing/seo';
export default function robots():MetadataRoute.Robots{
  const base=configuredSiteURL();
  return {rules:{userAgent:'*',allow:'/',disallow:['/app/','/api/','/login','/register','/organizations','/forgot-password','/reset-password','/verify-mobile','/verify-email']},...(base?{sitemap:new URL('/sitemap.xml',base).href}:{})};
}
