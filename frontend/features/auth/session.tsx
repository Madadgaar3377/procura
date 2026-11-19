"use client";
import { verificationPath } from './verification';
import type { WorkspaceOption, WorkspaceResolution } from './workspace';
import {useLocale} from '@/lib/locale';
import { createContext, useContext, useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api } from '@/lib/api/client';
import type { Schemas } from '@/lib/api/generated/schema';
import Link from 'next/link';
import { ApiScope, clearApiCache } from '@/lib/api/use-api';
import { AppLoadingScreen, ErrorState } from '@/features/shared/loading';
type User=Schemas['UserOut'];type Org=Schemas['OrganizationOut'];
type Session={
  user:User;
  organizations:Org[];
  org?:Org;
  allWorkspaces:WorkspaceOption[];
  select:(id:string)=>void;
  switchWorkspace:(nextRole:'buyer'|'supplier',nextOrgId:string)=>Promise<void>;
  reload:()=>Promise<void>;
  syncUser:(u:User)=>void;
};
const Context=createContext<Session|null>(null);
export function useOptionalSession(){return useContext(Context);}
export function useSession(){const value=useContext(Context);if(!value)throw new Error('Session provider required');return value;}

const ONBOARDING_EXEMPT_SEGMENTS = new Set(['company', 'company-verification', 'settings', 'sessions', 'profile', 'notifications']);

export function SessionGate({role,children}:{role:'buyer'|'supplier'|'admin';children:React.ReactNode}) {
 const {t}=useLocale();
 const router=useRouter();
 const pathname=usePathname();
 const [user,setUser]=useState<User>();
 const [organizations,setOrganizations]=useState<Org[]>([]);
 const [workspaceInfo,setWorkspaceInfo]=useState<WorkspaceResolution>();
 const [selected,setSelected]=useState<Record<string,string>>({});
 const [error,setError]=useState('');
 const [ready,setReady]=useState(false);
 const [verifying,setVerifying]=useState(false);

 async function reload(){
  const u=await api<User>('auth/me');
  const destination=verificationPath(u);
  if(destination){setVerifying(true);router.replace(destination);return;}
  const [orgs, ws]=u.is_admin
    ? [[], {role:'admin' as const, org_id:null, destination:'/app/admin/dashboard', workspaces:[{role:'admin' as const, org_id:null, org_name:'Procumaterial Operations'}], needs_onboarding:false}]
    : await Promise.all([api<Org[]>('organizations'), api<WorkspaceResolution>('auth/workspace')]);
  setVerifying(false);
  setUser(u);
  setOrganizations(orgs);
  setWorkspaceInfo(ws);
  const initialSelected:Record<string,string>={};
  for(const w of ['buyer','supplier','admin']){
   if(ws.role===w&&ws.org_id){initialSelected[w]=ws.org_id;}
   else if(u.last_org_id){initialSelected[w]=u.last_org_id;}
   else{
    try{initialSelected[w]=sessionStorage.getItem(`procura.organization:${u.id}:${w}`)||'';}catch{}
   }
  }
  setSelected(initialSelected);
 }

 useEffect(()=>{
  let active=true;
  reload().then(()=>{if(active)setReady(true);}).catch(()=>{if(active){setError('We could not load your workspace. Please try again.');setReady(true);}});
  const expire=()=>router.replace('/session-expired');
  window.addEventListener('procura:session-expired',expire);
  return()=>{active=false;window.removeEventListener('procura:session-expired',expire);};
 },[router]);

 const eligible=organizations.filter(o=>o.is_active&&(role==='admin'||role==='buyer'&&o.is_buyer||role==='supplier'&&o.is_supplier));
 const org=role==='admin'?undefined:eligible.find(o=>o.id===selected[role])||eligible[0];

 useEffect(()=>{
  if(!ready||verifying||!user||role==='admin'||user.is_admin)return;
  if(eligible.length===0&&workspaceInfo){
   const seg=(pathname.split('/').filter(Boolean)[2]||'dashboard');
   if(!workspaceInfo.needs_onboarding&&workspaceInfo.destination&&workspaceInfo.destination!==`/app/${role}/dashboard`){
    router.replace(workspaceInfo.destination);
    return;
   }
   if(workspaceInfo.needs_onboarding&&!ONBOARDING_EXEMPT_SEGMENTS.has(seg)){
    router.replace(workspaceInfo.destination||`/app/${role}/company`);
   }
  }
 },[ready,verifying,user,role,eligible.length,workspaceInfo,pathname,router]);

 if(!ready||verifying)return <AppLoadingScreen/>;
 if(!user)return <main className="auth-page"><div className="panel"><h1>{t("Sign in to continue")}</h1><ErrorState error={new Error(error||'Your session could not be verified.')} retry={()=>{setReady(false);setError('');reload().catch(()=>setError('We could not load your workspace. Please try again.')).finally(()=>setReady(true));}}/><Link href="/login">{t("Sign in")}</Link></div></main>;
 if(role==='admin'&&!user.is_admin)return <main className="auth-page"><div className="panel"><h1>{t("Access restricted")}</h1><Link href="/organizations">{t("Choose a workspace")}</Link></div></main>;

 async function persistWorkspace(nextRole:'buyer'|'supplier', nextOrgId:string){
  clearApiCache();
  setSelected(previous=>({...previous,[nextRole]:nextOrgId}));
  try{sessionStorage.setItem(`procura.organization:${user!.id}:${nextRole}`,nextOrgId);}catch{}
  try{
   const updated=await api<WorkspaceResolution>('auth/workspace',{method:'PUT',body:JSON.stringify({role:nextRole,org_id:nextOrgId})});
   setWorkspaceInfo(updated);
  }catch{/* Server validation failure will be reflected on next reload. */}
 }

 return <Context.Provider value={{
  user,
  organizations:eligible,
  org,
  allWorkspaces:workspaceInfo?.workspaces||[],
  select:(id)=>{if(role!=='admin')void persistWorkspace(role,id);},
  switchWorkspace:async(nextRole,nextOrgId)=>{
   await persistWorkspace(nextRole,nextOrgId);
   if(nextRole!==role)router.push(`/app/${nextRole}/dashboard`);
  },
  reload,
  syncUser:(u)=>setUser(u),
 }}><ApiScope.Provider key={`${user.id}:${role}:${org?.id||'none'}`} value={`${user.id}:${role}:${org?.id||'none'}`}>{children}</ApiScope.Provider></Context.Provider>;
}
export async function signOut(){await api('auth/logout',{method:'POST'});clearApiCache();window.location.assign('/login');}
