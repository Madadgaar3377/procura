"use client";
import { useState } from 'react';
import Link from 'next/link';
import {useServerList} from '@/features/shared/server-list';
import { useApi } from '@/lib/api/use-api';
import { useSession } from '@/features/auth/session';
import type { Schemas } from '@/lib/api/generated/schema';
import { DataTable,Heading,State } from '@/features/shared/ui';
import Company from '@/features/organizations/company';
import {RFQList,RFQWizard,RFQDetail,Matching} from '@/features/rfqs/workspace';
import {Invitations,Quotes,QuoteEditor,Comparison} from '@/features/quotations/workspace';
import {PriceBook,Capabilities,Membership} from '@/features/suppliers/workspace';
import {AdminCompanies,Verification,Taxonomy,Imports,AdminMembership,ContactInbox} from '@/features/admin/workspace';
import BusinessDashboard from '@/features/dashboard/workspace';
import AdminUsers from '@/features/admin/users';
import Notifications from '@/features/notifications/workspace';
import Settings from '@/features/auth/settings';
import Reports from '@/features/reports/workspace';
import { useLocale } from '@/lib/locale';
function RFQSelector({children}:{children:(id:string)=>React.ReactNode}){
 const {t}=useLocale();const {org,user}=useSession();
 const q=useServerList<Schemas['RFQOut']>(user.is_admin?'rfqs':org?`rfqs?org_id=${org.id}`:null,'Search RFQs');
 const [selected,setSelected]=useState(()=>typeof window!=='undefined'?new URLSearchParams(window.location.search).get('rfq')||'':'');
 const detail=useApi<Schemas['RFQDetail']>(selected?`rfqs/${selected}`:null);
 const chosen=detail.data?.rfq;const allowed=chosen&&(user.is_admin||chosen.buyer_org_id===org?.id);
 const rows=q.rows.filter(r=>user.is_admin||r.buyer_org_id===org?.id);
 const options=allowed&&!rows.some(r=>r.id===chosen.id)?[chosen,...rows]:rows;
 const id=selected?(allowed?selected:''):rows[0]?.id||'';
 return <>{q.controls}<label className="field live-selector">{t('Request for quotation')}<select value={id} onChange={e=>setSelected(e.target.value)}><option value="">{t('Select RFQ...')}</option>{options.map(r=><option key={r.id} value={r.id}>{r.reference} ? {r.title}</option>)}</select></label><State {...q} retry={q.reload}/><State loading={detail.loading} error={detail.error} retry={detail.reload}/>{children(id)}</>;
}

function Dashboard({role}:{role:string}){
 const {org}=useSession();const {t}=useLocale();const stats=useApi<Record<string,number>>(role==='admin'?'admin/dashboard':null);const report=useApi<Schemas['ReportSummary']>(role==='admin'?null:`reports/summary?workspace=${role}${org?`&org_id=${encodeURIComponent(org.id)}`:''}`);
 const details=role==='admin'?'Current database totals.':org?`${org.name} — ${org.verified_status}`:'Create your company profile to start.';
 const metrics=role==='buyer'?[['RFQs created',report.data?.rfqs_created],['Supplier invitations sent',report.data?.invitations],['Invitations accepted',report.data?.invitations_accepted],['Quotations received',report.data?.quotations_submitted]]:role==='supplier'?[['RFQ invitations received',report.data?.invitations],['Invitations accepted',report.data?.invitations_accepted],['Quotations submitted',report.data?.quotations_submitted],['Quotation drafts',report.data?.quotation_drafts]]:[];
 return <><Heading title={role==='admin'?t('Platform overview'):role==='buyer'?t('Buyer overview'):t('Supplier overview')} description={details}/><State loading={stats.loading||report.loading} error={stats.error||report.error}/>{role==='admin'?<div className="stat-grid">{Object.entries(stats.data||{}).map(([label,n])=><div className="stat-card" key={label}><div className="stat-top">{t(label.replaceAll('_',' '))}</div><div className="stat-value">{n}</div></div>)}</div>:<><div className="stat-grid">{metrics.map(([label,value])=><div className="stat-card" key={String(label)}><div className="stat-top">{t(String(label))}</div><div className="stat-value">{(value as number|undefined)??0}</div></div>)}</div><div className="panel"><h2>{t('Continue your work')}</h2><div className="live-actions"><Link href={`/app/${role}/company`}>{t('Company profile')}</Link><Link href={role==='buyer'?'/app/buyer/rfqs/new':'/app/supplier/inbox'}>{t(role==='buyer'?'Create an RFQ':'Review invitations')}</Link><Link href={`/app/${role}/quotations`}>{t('Quotations')}</Link></div></div></>}</>;
}
function Audit(){
 const {t}=useLocale();const q=useServerList<Schemas['AuditOut']>('admin/audit','Search audit log');return <><Heading title={t("Audit log")}/>{q.controls}<State {...q}/><DataTable paginate={false} rows={q.rows} columns={['created_at','action','actor_user_id','org_id','entity_id']}/></>;}
export default function LivePortal({role,current,entity}:{role:'buyer'|'supplier'|'admin';current:string;entity?:string}){
 const {t}=useLocale();
 if(current==='dashboard')return role==='admin'?<Dashboard role={role}/>:<BusinessDashboard role={role}/>;
 if(current==='reports')return <Reports workspace={role}/>;
 if(current==='company'||current==='company-verification')return <Company role={role}/>;
 if(current==='settings'&&role==='admin')return <><Settings/><PlatformSettings/></>;
 if(current==='settings'||current==='sessions'||current==='profile')return <Settings/>;
 if(current==='notifications'||current==='automation')return <Notifications admin={role==='admin'}/>;
 if(current==='rfqs'&&entity==='new'&&role==='buyer')return <RFQWizard/>;
 if(current==='rfqs'&&entity)return <RFQDetail id={entity} role={role}/>;
 if(current==='rfqs')return <RFQList role={role}/>;
 if(current==='quote'&&entity)return <QuoteEditor id={entity} role={role}/>;
 if(current==='quotations')return <Quotes role={role}/>;
 if(current==='compare')return <RFQSelector>{id=><Comparison id={id}/>}</RFQSelector>;
 if(current==='matches'||current==='matching')return <RFQSelector>{id=><Matching id={id}/>}</RFQSelector>;
 if(role==='supplier'){
  if(current==='inbox')return <Invitations/>;
  if(current==='price-book')return <PriceBook/>;
  if(current==='capabilities')return <Capabilities/>;
  if(current==='membership')return <Membership/>;
 }
 if(role==='admin'){
  if(current==='users')return <AdminUsers/>;
  if(current==='contact-inquiries')return <ContactInbox/>;
  if(current==='buyers'||current==='suppliers')return <AdminCompanies type={current}/>;
  if(current==='verification'||current==='claims')return <Verification/>;
  if(current==='materials'||current==='categories')return <Taxonomy kind={current}/>;
  if(current==='supplier-import'||current==='material-import')return <Imports kind={current==='supplier-import'?'suppliers':'materials'}/>;
  if(current==='membership')return <AdminMembership/>;
  if(current==='audit')return <Audit/>;
 }
 return <><Heading title={current==='reports'?t('Activity reporting'):current.replaceAll('-',' ')}/><div className="panel"><h2>{t("This workflow is not yet available")}</h2><p>{current==='roles'?t('Role administration needs a backend contract and member acceptance workflow.'):current==='reports'?t('Historical reporting and exports require additional authorized aggregate APIs.'):t('This page requires additional backend functionality.')}</p><Link href={`/app/${role}/dashboard`}>{t("Return to overview")}</Link></div></>;
}

function PlatformSettings(){
 const {t}=useLocale();const q=useApi<Record<string,unknown>>('admin/settings');return <><Heading title={t("Platform settings")} description="Current local configuration. Credentials are managed only on the backend."/><State {...q}/><DataTable rows={Object.entries(q.data||{}).map(([setting,value])=>({setting,value}))} columns={['setting','value']}/></>;}
