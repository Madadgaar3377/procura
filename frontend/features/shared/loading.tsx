"use client";
import { Button } from '@/components/ui/button';
import { useLocale } from '@/lib/locale';

function Bars({count=3}:{count?:number}){return <div className="skeleton-bars" aria-hidden="true">{Array.from({length:count},(_,i)=><span className="skeleton-bar" key={i}/>)}</div>;}
export function CardSkeleton(){return <div className="panel skeleton-card"><Bars/></div>;}
export function TableSkeleton({rows=5}:{rows?:number}){return <div className="panel skeleton-table"><Bars count={rows}/></div>;}
export function ChartSkeleton(){return <div className="panel skeleton-chart"><Bars count={4}/></div>;}
export function FormSkeleton(){return <div className="panel skeleton-form"><Bars count={4}/></div>;}
export function DashboardSkeleton({metrics=4}:{metrics?:number}){const {t}=useLocale();return <div className="dashboard-loading" role="status" aria-live="polite" aria-label={t('Loading records...')}><div className="dashboard-metrics">{Array.from({length:metrics},(_,i)=><div className="dashboard-metric skeleton-metric" key={i}><Bars count={4}/></div>)}</div><div className="dashboard-main-grid"><TableSkeleton rows={6}/><ChartSkeleton/></div><div className="dashboard-bottom-grid"><CardSkeleton/><CardSkeleton/></div></div>;}
export function PageLoadingState(){const {t}=useLocale();return <div role="status" aria-live="polite" aria-label={t('Loading records...')} className="page-loading-state"><span className="sr-only">{t('Loading records...')}</span><TableSkeleton rows={3}/></div>;}
export function AppLoadingScreen(){const {t}=useLocale();return <main className="route-loading" role="status" aria-live="polite"><div className="route-loading-mark">PM</div><div className="route-loading-copy"><strong>Procumaterial</strong><span>{t('Loading records...')}</span></div><div className="route-loading-track" aria-hidden="true"><i/></div></main>;}
export function RetryButton({onClick}:{onClick:()=>void}){const {t}=useLocale();return <Button type="button" variant="outline" onClick={onClick}>{t('Try again')}</Button>;}
export function ErrorState({error,retry}:{error:Error;retry?:()=>void}){const {t}=useLocale();return <div className="notice" role="alert"><p>{t(error.message)}</p>{retry&&<RetryButton onClick={retry}/>}</div>;}
export function EmptyState(){const {t}=useLocale();return <div className="panel"><h2>{t('No records yet')}</h2><p>{t('Records will appear here when activity is available.')}</p></div>;}
