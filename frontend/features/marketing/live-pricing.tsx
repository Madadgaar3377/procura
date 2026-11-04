"use client";
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {useApi} from '@/lib/api/use-api';
import type {Schemas} from '@/lib/api/generated/schema';
import type {MarketingLocale} from './copy';

type Feature={label:string;included:boolean};
export default function LivePricing({locale='en',member=false}:{locale?:MarketingLocale;member?:boolean}){
 const ar=locale==='ar';const tr=(en:string,arabic:string)=>ar?arabic:en;
 const plans=useApi<Schemas['PlanOut'][]>('memberships/plans');
 const settings=useApi<{free_launch_mode:boolean;paid_memberships_enabled:boolean}>('memberships/settings');
 const [interval,setIntervalValue]=useState<'monthly'|'yearly'>('monthly');
 useEffect(()=>{const timer=window.setInterval(()=>{if(document.visibilityState==='visible'){plans.reload();settings.reload();}},30000);return()=>window.clearInterval(timer);},[plans.reload,settings.reload]);
 if(plans.error||settings.error)return <div className="procura-marketing pm-data-state" role="alert"><p>{tr('Unable to load membership plans. Please try again.','تعذر تحميل خطط العضوية. يرجى المحاولة مرة أخرى.')}</p><button className="pm-button pm-button-secondary" onClick={()=>{plans.reload();settings.reload();}}>{tr('Try again','حاول مرة أخرى')}</button></div>;
 if(plans.loading||settings.loading)return <div className="procura-marketing pm-data-skeleton" role="status" aria-label={tr('Loading plans','جار تحميل الخطط')}>{[0,1,2].map(i=><div key={i}><i/><b/><span/><span/></div>)}</div>;
 if(!plans.data?.length)return <div className="procura-marketing pm-memberships-empty"><h2>{tr('Membership plans are coming soon','خطط العضوية قريباً')}</h2><p>{tr('There are no published plans available yet. Contact the Procura team for membership information.','لا توجد خطط منشورة متاحة حالياً. تواصل مع فريق بروكيورا لمعلومات العضوية.')}</p><Link href="/contact">{tr('Contact the team','تواصل مع الفريق')}</Link></div>;
 const both=plans.data.some(plan=>plan.billing_intervals.includes('monthly'))&&plans.data.some(plan=>plan.billing_intervals.includes('yearly'));
 return <div className="procura-marketing pm-live-pricing" dir={ar?'rtl':'ltr'} data-live-pricing>
 {settings.data?.free_launch_mode&&<p className="pm-pricing-launch" role="status">{tr('Free launch is active. Paid plans are coming soon; no payments are collected.','الإطلاق المجاني متاح. الخطط المدفوعة قريباً ولا يتم تحصيل أي دفعات.')}</p>}
 {both&&<div className="pm-billing-toggle" role="group" aria-label={tr('Billing interval','دورة الفوترة')}>{(['monthly','yearly'] as const).map(value=><button key={value} aria-pressed={interval===value} onClick={()=>setIntervalValue(value)}>{value==='monthly'?tr('Monthly','شهري'):tr('Yearly','سنوي')}</button>)}</div>}
 <div className="pm-plan-grid">{plans.data.map(plan=>{
  const selected=both?interval:plan.billing_intervals[0];const offered=plan.billing_intervals.includes(selected);
  const price=selected==='monthly'?plan.monthly_price:plan.yearly_price;
  const free=Number(price)===0;const available=plan.available_for_subscription&&offered;
  const features=(plan.feature_details.length?plan.feature_details:plan.features.map(label=>({label,included:true}))) as Feature[];
  return <article key={plan.id} className={`pm-plan-card${plan.recommended?' pm-plan-recommended':''}`} data-plan-slug={plan.slug}>
   {plan.recommended&&<span className="pm-plan-recommended-badge">{tr('Recommended','موصى بها')}</span>}
   <h3>{plan.name}</h3><p>{plan.description}</p><p className="pm-plan-price">{offered?new Intl.NumberFormat(ar?'ar-SA':'en-SA',{style:'currency',currency:'SAR',minimumFractionDigits:2}).format(Number(price)):tr('Not offered','غير متاح')}</p>
   {offered&&<p>{selected==='monthly'?tr('per month','شهرياً'):tr('per year','سنوياً')}</p>}
   <ul>{features.map((feature,index)=><li className={feature.included?'':'pm-plan-excluded'} key={index}><span aria-label={feature.included?tr('Included','مشمول'):tr('Not included','غير مشمول')}>{feature.included?'✓':'—'}</span> {feature.label}</li>)}</ul>
   <p>{!available?tr('Not available for new subscriptions','غير متاح للاشتراكات الجديدة'):free?tr('Free plan','خطة مجانية'):tr('Coming soon','قريباً')}</p>
   {available&&free?<Link href={member?'/contact':`/register?role=supplier&lang=${locale}`} className="pm-button pm-button-secondary">{member?tr('Request membership change',"\u0637\u0644\u0628 \u062a\u063a\u064a\u064a\u0631 \u0627\u0644\u0639\u0636\u0648\u064a\u0629"):tr('Create supplier account','إنشاء حساب مورد')}</Link>:<button className="pm-button pm-button-secondary" disabled>{!available?tr('Unavailable','غير متاح'):tr('Coming soon','قريباً')}</button>}
  </article>;
 })}</div></div>;
}
