"use client";
import {useLocaleCore} from '@/lib/locale-core';
import styles from './route-loading.module.css';
export default function RouteLoading(){
 const {arabic}=useLocaleCore();
 return <main className={styles.screen} dir={arabic?'rtl':'ltr'}><div className={styles.card} role="status" aria-live="polite" aria-busy="true">
 <div className={styles.brand}><span className={styles.mark} aria-hidden="true">Pm</span><strong>Procumaterial<span>.</span></strong></div>
 <span className={styles.spinner} aria-hidden="true"/>
 <h1>{arabic?'جارٍ تجهيز صفحتك':'Preparing your page'}</h1>
 <p>{arabic?'لحظات وتصبح مساحة عملك جاهزة.':'Your business workspace will be ready shortly.'}</p>
 </div></main>;
}
