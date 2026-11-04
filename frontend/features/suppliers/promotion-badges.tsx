"use client";
import {useApi} from '@/lib/api/use-api';
import {useLocale} from '@/lib/locale';
export default function PromotionBadges({id}:{id:string}){const {t}=useLocale();const q=useApi<{premium:boolean;featured:boolean}>(`memberships/public/suppliers/${id}/status`);return <>{q.data?.premium&&<span className="sp-chips">{t('Premium')}</span>}{q.data?.featured&&<span className="sp-chips">{t('Featured ? sponsored placement')}</span>}</>;}
