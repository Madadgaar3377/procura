import type { Metadata, Viewport } from "next";
import { cookies,headers } from "next/headers";
import "./globals.css";
import "./workspace.css";
import "./marketing.css";
import "./supplier-profile.css";
import {configuredSiteURL} from '@/features/marketing/seo';
import { LocaleProvider } from "@/lib/locale";
import { ToastProvider } from "@/features/shared/toast";

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export const metadata: Metadata = {
  metadataBase: configuredSiteURL(),
  title: {
    default: "Procumaterial | Saudi Arabia B2B Procurement & Supplier Platform",
    template: "%s | Procumaterial",
  },
  description: "Connect with trusted Saudi suppliers, submit procurement RFQs, discover Civil, Electrical and Mechanical materials, and compare competitive supplier quotations through Procumaterial.",
  applicationName: "Procumaterial",
  referrer: "no-referrer",
  keywords: [
    "Saudi Arabia procurement platform",
    "B2B procurement Saudi Arabia",
    "Saudi suppliers directory",
    "Supplier sourcing Saudi Arabia",
    "RFQ platform Saudi Arabia",
    "Construction material suppliers Saudi Arabia",
    "Electrical suppliers Saudi Arabia",
    "Mechanical suppliers Saudi Arabia",
    "Civil material suppliers Saudi Arabia",
    "Request for quotation Saudi Arabia",
    "B2B supplier matching Saudi Arabia",
  ],
  alternates: {
    canonical: "/",
    languages: {
      en: "/?lang=en",
      ar: "/?lang=ar",
      "x-default": "/",
    },
  },
  openGraph: {
    type: "website",
    siteName: "Procumaterial",
    title: "Procumaterial | Saudi Arabia B2B Procurement & Supplier Platform",
    description: "Connect with trusted Saudi suppliers, submit procurement RFQs, discover Civil, Electrical and Mechanical materials, and compare competitive supplier quotations through Procumaterial.",
    url: "https://procumaterial.com",
    locale: "en_US",
    alternateLocale: "ar_SA",
  },
  twitter: {
    card: "summary_large_image",
    title: "Procumaterial | Saudi Arabia B2B Procurement & Supplier Platform",
    description: "Connect with trusted Saudi suppliers, submit procurement RFQs, discover Civil, Electrical and Mechanical materials, and compare competitive supplier quotations through Procumaterial.",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-video-preview": -1,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const requestedLocale=(await headers()).get('x-procura-marketing-locale');
  const initialArabic=requestedLocale?requestedLocale==='ar':(await cookies()).get('procura.locale')?.value==='ar';
  return <html lang={initialArabic?'ar':'en'} dir={initialArabic?'rtl':'ltr'} suppressHydrationWarning><head><script id="procura-locale-bootstrap" dangerouslySetInnerHTML={{__html:"try{var q=new URLSearchParams(location.search).get('lang');var p=q==='ar'||q==='en'?q:localStorage.getItem('procura.locale');if(q==='ar'||q==='en')localStorage.setItem('procura.locale',q);if(p==='ar'||p==='en'){document.documentElement.lang=p;document.documentElement.dir=p==='ar'?'rtl':'ltr';document.cookie='procura.locale='+p+'; Path=/; Max-Age=31536000; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'');}}catch(e){}"}}/></head><body><LocaleProvider initialArabic={initialArabic}><ToastProvider>{children}</ToastProvider></LocaleProvider></body></html>;
}
