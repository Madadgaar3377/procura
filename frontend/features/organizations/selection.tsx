"use client";
import {AppLoadingScreen} from '@/features/shared/loading';
import {useLocale} from '@/lib/locale';
import { resolveWorkspaceDestination } from '@/features/auth/workspace';
import { api } from '@/lib/api/client';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useApi } from '@/lib/api/use-api';
import type { Schemas } from '@/lib/api/generated/schema';

export default function OrganizationSelection(){
 const {t}=useLocale();
 const router=useRouter();
 const [checking,setChecking]=useState(true);

 useEffect(()=>{
  let active=true;
  void (async()=>{
   try{
    const user=await api<Schemas['UserOut']>('auth/me');
    const destination=await resolveWorkspaceDestination(user);
    if(destination&&destination!=='/organizations'){
     router.replace(destination);
     return;
    }
    if(active)setChecking(false);
   }catch{
    router.replace('/login');
   }
  })();
  return()=>{active=false;};
 },[router]);

 const q=useApi<Schemas['OrganizationOut'][]>(checking?null:'organizations');
 const [role,setRole]=useState<'buyer'|'supplier'>('buyer');
 if(checking)return <AppLoadingScreen/>;
 return <main className="container public-content">
  <Link href="/" className="brand">Procurement</Link>
  <h1>{t("Choose your workspace")}</h1>
  {q.loading&&<p role="status">{t("Loading organizations...")}</p>}
  {q.error&&<p role="alert">{t("We could not load your organizations.")} <button type="button" onClick={q.reload}>{t("Try again")}</button> <Link href="/login">{t("Sign in")}</Link></p>}
  <div className="pill-tabs">
   <button onClick={()=>setRole('buyer')} className={role==='buyer'?'selected':''}>{t("Buyer")}</button>
   <button onClick={()=>setRole('supplier')} className={role==='supplier'?'selected':''}>{t("Supplier")}</button>
  </div>
  <p>{t("Select your company from the workspace selector after opening the portal.")}</p>
  <Link className="page-link" href={`/app/${role}/dashboard`}>{t("Open")} {role} {t("workspace \u2192")}</Link>
  <div className="directory-grid">
   {q.data?.map(org=><article className="directory-card" key={org.id}>
    <h2>{org.name}</h2>
    <p>{org.city} · {org.verified_status}</p>
    {org.is_buyer&&<Link href="/app/buyer/company">{t("Buyer workspace")}</Link>}
    {org.is_supplier&&<Link href="/app/supplier/company"> {t("Supplier workspace")}</Link>}
   </article>)}
  </div>
  <Link href={`/app/${role}/company`}>{t("Create a company")}</Link>
 </main>;
}
