"use client";
import Link from 'next/link';
import {useLocaleCore} from '@/lib/locale-core';
import styles from './route-loading.module.css';
export default function RouteError({retry}:{retry:()=>void}){
 const {arabic}=useLocaleCore();
 return <main className={styles.screen}><section className={styles.card} role="alert"><div className={styles.brand}><strong>Procumaterial<span>.</span></strong></div><h1>{arabic?'تعذر تحميل الصفحة':'We could not load this page'}</h1><p>{arabic?'يرجى المحاولة مرة أخرى أو العودة لتسجيل الدخول.':'Please try again or return to sign in.'}</p><div className={styles.actions}><button type="button" onClick={retry}>{arabic?'إعادة المحاولة':'Try again'}</button><Link href="/login">{arabic?'تسجيل الدخول':'Return to login'}</Link></div></section></main>;
}
