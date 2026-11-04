"use client";
import LivePricing from '@/features/marketing/live-pricing';
import {useLocale} from '@/lib/locale';
export default function Plans(){const {arabic,t}=useLocale();return <section className="container public-content"><h1>{t('Membership')}</h1><LivePricing locale={arabic?'ar':'en'}/></section>;}
