"use client";
import {useEffect} from 'react';
import {useApi} from '@/lib/api/use-api';
import type {Schemas} from '@/lib/api/generated/schema';
import {useLocale} from '@/lib/locale';

export function RateStatus({status,capabilityStatus}:{status:string;capabilityStatus:string}){
 const {arabic,t}=useLocale();const policy=useApi<Schemas['PricePolicyOut']>('pricebooks/policy');
 const declared=policy.data?.require_capability_approval?(arabic?'بانتظار اعتماد الفئة':'Category pending approval'):(arabic?'فئة يحددها المورد':'Self-declared category');
 return <span className="price-rate-status"><span className={`price-capability-badge ${status==='active'?'approved':'pending'}`}>{status==='inactive'?(arabic?'مسودة غير نشطة':'Inactive draft'):t(status.charAt(0).toUpperCase()+status.slice(1))}</span>{capabilityStatus==='pending'&&<small>{declared}</small>}{capabilityStatus==='not_selected'&&<small>{arabic?'فئة التوريد غير مختارة':'Supply category no longer selected'}</small>}</span>;
}

// The app uses useApi (not React Query). Its cache key includes the session and
// organization. Revalidate approvals made in another tab without caching them
// indefinitely in an open pricing form.
export function useApprovalRefresh(reload:()=>void){
 useEffect(()=>{
  const refresh=()=>{if(document.visibilityState==='visible')reload();};
  refresh();window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
  const timer=setInterval(refresh,15000);
  return()=>{clearInterval(timer);window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);};
 },[reload]);
}

