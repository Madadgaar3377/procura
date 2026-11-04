import type { Metadata } from "next";
import { cookies,headers } from "next/headers";
import "./globals.css";
import "./workspace.css";
import "./marketing.css";
import "./supplier-profile.css";
import {configuredSiteURL} from '@/features/marketing/seo';
import { LocaleProvider } from "@/lib/locale";
import { ToastProvider } from "@/features/shared/toast";

export const metadata: Metadata = {
  title: "Procura — Saudi Supplier Discovery & RFQs",
  description: "A clearer way for Saudi procurement teams to discover qualified suppliers and manage requests for quotation.",
  applicationName: "Procura",
  referrer: "no-referrer",
  ...(configuredSiteURL()?{metadataBase:configuredSiteURL()}:{}),
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const requestedLocale=(await headers()).get('x-procura-marketing-locale');
  const initialArabic=requestedLocale?requestedLocale==='ar':(await cookies()).get('procura.locale')?.value==='ar';
  return <html lang={initialArabic?'ar':'en'} dir={initialArabic?'rtl':'ltr'} suppressHydrationWarning><head><script id="procura-locale-bootstrap" dangerouslySetInnerHTML={{__html:"try{var q=new URLSearchParams(location.search).get('lang');var p=q==='ar'||q==='en'?q:localStorage.getItem('procura.locale');if(q==='ar'||q==='en')localStorage.setItem('procura.locale',q);if(p==='ar'||p==='en'){document.documentElement.lang=p;document.documentElement.dir=p==='ar'?'rtl':'ltr';document.cookie='procura.locale='+p+'; Path=/; Max-Age=31536000; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'');}}catch(e){}"}}/></head><body><LocaleProvider initialArabic={initialArabic}><ToastProvider>{children}</ToastProvider></LocaleProvider></body></html>;
}
