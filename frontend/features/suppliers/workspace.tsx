"use client";
import {useLocale} from '@/lib/locale';
import { useApi } from '@/lib/api/use-api';
import type { Schemas } from '@/lib/api/generated/schema';
import { useSession } from '@/features/auth/session';
import { DataTable,Heading,State } from '@/features/shared/ui';
import PromotionBadges from './promotion-badges';
import LivePricing from '@/features/marketing/live-pricing';
import CategorySelection from './category-selection';
export {PriceBook} from './price-book';
export function Capabilities({admin=false}:{admin?:boolean}){
 const {t}=useLocale();
 return <><Heading title={t("Capabilities and service areas")}/><CategorySelection/></>;
}

export function Membership(){
 const {t,arabic}=useLocale();const {org}=useSession();
 const subs=useApi<Schemas['SubscriptionDetailOut'][]>(org?`memberships/organizations/${org.id}`:null);
 const now=Date.now();const current=subs.data?.find(s=>s.status==='active'&&(!s.starts_at||Date.parse(s.starts_at)<=now)&&(!s.expires_at||Date.parse(s.expires_at)>now));
 const snapshot=current?.plan_snapshot as {features?:string[];name?:string}|null|undefined;
 return <><Heading title={t('Membership')} description={t('Billing is not active. Assigned membership terms are preserved.')}/>
 <State {...subs} retry={subs.reload}/><section className="panel"><h2>{t('Current assigned plan')}: {current?(snapshot?.name||current.plan_name):t('No active assigned plan')}</h2>
 {current&&<><p>{t('Status')}: {t(current.status)}</p><p>{t('Start')}: {current.starts_at?new Date(current.starts_at).toLocaleDateString(arabic?'ar-SA':'en-GB'):'?'}</p><p>{t('Expiration')}: {current.expires_at?new Date(current.expires_at).toLocaleDateString(arabic?'ar-SA':'en-GB'):'?'}</p><ul>{snapshot?.features?.map((feature,i)=><li key={i}>{feature}</li>)}</ul>{!snapshot&&<p>{t('Historical benefits were not recorded for this subscription. Contact the team for its agreed terms.')}</p>}</>}
 {org&&<PromotionBadges id={org.id}/>}</section><h2>{t('Subscription history')}</h2><DataTable rows={subs.data||[]} columns={['plan_name','status','starts_at','expires_at','price_snapshot','currency_snapshot','billing_interval']}/>
 <h2>{t('Available plans')}</h2><LivePricing locale={arabic?'ar':'en'} member/></>;
}
