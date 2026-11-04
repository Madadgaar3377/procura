"use client";
import { verificationPath } from './verification';
import {useLocale} from '@/lib/locale';
import { createContext, useContext, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api/client';
import type { Schemas } from '@/lib/api/generated/schema';
import Link from 'next/link';
import { ApiScope, clearApiCache } from '@/lib/api/use-api';
import { AppLoadingScreen, ErrorState } from '@/features/shared/loading';
type User=Schemas['UserOut'];type Org=Schemas['OrganizationOut'];
type Session={user:User;organizations:Org[];org?:Org;select:(id:string)=>void;reload:()=>Promise<void>};
const Context=createContext<Session|null>(null);
export function useOptionalSession(){return useContext(Context);}
export function useSession(){const value=useContext(Context);if(!value)throw new Error('Session provider required');return value;}
export function SessionGate({role,children}:{role:'buyer'|'supplier'|'admin';children:React.ReactNode}) {
 const {t}=useLocale();
 const router=useRouter();const [user,setUser]=useState<User>();const [organizations,setOrganizations]=useState<Org[]>([]);const [selected,setSelected]=useState<Record<string,string>>({});const [error,setError]=useState('');const [ready,setReady]=useState(false);const [verifying,setVerifying]=useState(false);
 async function reload(){const [u,orgs]=await Promise.all([api<User>('auth/me'),api<Org[]>('organizations')]);const destination=verificationPath(u);if(destination){setVerifying(true);router.replace(destination);return;}setVerifying(false);setUser(u);setOrganizations(orgs);try{setSelected(Object.fromEntries(['buyer','supplier','admin'].map(workspace=>[workspace,sessionStorage.getItem(`procura.organization:${u.id}:${workspace}`)||''])));}catch{/* Storage may be disabled; membership-checked defaults still apply. */}}
 useEffect(()=>{let active=true;reload().then(()=>{if(active)setReady(true);}).catch(e=>{if(active){setError(e.message);setReady(true);}});const expire=()=>router.replace('/session-expired');window.addEventListener('procura:session-expired',expire);return()=>{active=false;window.removeEventListener('procura:session-expired',expire);};},[router]);
 if(!ready||verifying)return <AppLoadingScreen/>;
 if(!user)return <main className="auth-page"><div className="panel"><h1>{t("Sign in to continue")}</h1><ErrorState error={new Error(error||'Your session could not be verified.')} retry={()=>{setReady(false);setError('');reload().catch(e=>setError(e.message)).finally(()=>setReady(true));}}/><Link href="/login">{t("Sign in")}</Link></div></main>;
 if(role==='admin'&&!user.is_admin)return <main className="auth-page"><div className="panel"><h1>{t("Access restricted")}</h1><Link href="/organizations">{t("Choose a workspace")}</Link></div></main>;
 const eligible=organizations.filter(o=>o.is_active&&(role==='admin'||role==='buyer'&&o.is_buyer||role==='supplier'&&o.is_supplier));
 const org=role==='admin'?undefined:eligible.find(o=>o.id===selected[role])||eligible[0];
 return <Context.Provider value={{user,organizations:eligible,org,select:(id)=>{clearApiCache();setSelected(previous=>({...previous,[role]:id}));try{sessionStorage.setItem(`procura.organization:${user.id}:${role}`,id);}catch{/* Selection remains available in memory. */}},reload}}><ApiScope.Provider key={`${user.id}:${role}:${org?.id||'none'}`} value={`${user.id}:${role}:${org?.id||'none'}`}>{children}</ApiScope.Provider></Context.Provider>;
}
export async function signOut(){await api('auth/logout',{method:'POST'});clearApiCache();window.location.assign('/login');}
