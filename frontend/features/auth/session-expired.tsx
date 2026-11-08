"use client";

import {useEffect} from 'react';
import Link from 'next/link';
import {IconLock} from '@tabler/icons-react';
import {MarketingHeader,MarketingFooter} from '@/features/marketing/chrome';
import {useLocale} from '@/lib/locale';
import {clearApiCache} from '@/lib/api/use-api';
import styles from './session-expired.module.css';

export default function SessionExpired(){
  const {arabic,t}=useLocale();

  // Discard cached workspace records. Cookie validation and refresh stay with
  // the existing API client and login flow; visiting this page never logs out
  // a user whose session is still valid.
  useEffect(()=>{clearApiCache();},[]);

  return <div className={`procura-marketing ${styles.page}`} dir={arabic?'rtl':'ltr'} lang={arabic?'ar':'en'} id="top">
    <MarketingHeader/>
    <main id="main-content" className={styles.main}>
      <section className={styles.card} aria-labelledby="session-expired-title" aria-describedby="session-expired-description">
        <span className={styles.icon} aria-hidden="true"><IconLock size={26} stroke={1.7}/></span>
        <h1 id="session-expired-title" className={styles.title}>{t('Your session has expired')}</h1>
        <p id="session-expired-description" className={styles.description}>{t("For your security, you've been signed out. Sign in again to continue using Procumaterial.")}</p>
        <div className={styles.actions}>
          <Link href="/login" prefetch={false} className={styles.primary}>{t('Sign In Again')}</Link>
          <Link href="/" className={styles.secondary}>{t('Return to Homepage')}</Link>
        </div>
        <p className={styles.tagline}>{t('Procumaterial — Connecting Saudi Businesses with Trusted Suppliers.')}</p>
      </section>
    </main>
    <MarketingFooter/>
  </div>;
}
