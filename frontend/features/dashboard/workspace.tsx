"use client";

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { IconArrowUpRight, IconBuilding, IconCalendar, IconClock, IconFileText, IconPlus, IconReceipt, IconRefresh, IconSend } from '@tabler/icons-react';
import { Button } from '@/components/ui/button';
import { DashboardSkeleton } from '@/features/shared/loading';
import { State } from '@/features/shared/ui';
import {DataTable} from '@/features/shared/ui';
import { useApi } from '@/lib/api/use-api';
import { useLocale } from '@/lib/locale';
import { useSession } from '@/features/auth/session';
import type { Schemas } from '@/lib/api/generated/schema';

type Role='buyer'|'supplier';
function SupplierActivity({data}:{data?:Schemas['ReportSummary']}){
 const {t,arabic}=useLocale();
 const recent=data?.recent_enquiries||[];
 const quoteRows=data?.recent_quotations||[];
 const date=(value:unknown)=>{if(!value)return '\u2014';const d=new Date(String(value));return Number.isNaN(d.getTime())?'\u2014':new Intl.DateTimeFormat(arabic?'ar-SA-u-ca-gregory':'en-GB',{day:'numeric',month:'short',year:'numeric'}).format(d);};
 const status=(value:unknown)=><span className={`dashboard-badge ${String(value).toLowerCase().replaceAll(' ','_')}`}>{t(statusLabels[String(value)]||String(value).replaceAll('_',' '))}</span>;
 return <div className="supplier-activity">
  <section className="activity-section" aria-labelledby="supplier-invitations-heading"><div className="dashboard-panel-heading"><div><h2 id="supplier-invitations-heading">{t('Recent RFQ Invitations')}</h2><p>{t('Your latest enquiries and response deadlines.')}</p></div><Link className="dashboard-text-link" href="/app/supplier/inbox">{t('View all')} <IconArrowUpRight size={16}/></Link></div>
  {recent.length?<DataTable searchable={false} paginate={false} rows={recent} columns={['reference','buyer_project','categories','response_deadline','invitation_status','quotation_status']} labels={{reference:'RFQ reference',buyer_project:'Buyer / project',categories:'Main Category / Subcategory',response_deadline:'Deadline',invitation_status:'Invitation status',quotation_status:'Quotation status'}} render={{buyer_project:row=><><strong>{String(row.buyer||'\u2014')}</strong><span className="table-secondary">{String(row.project||'\u2014')}</span></>,response_deadline:row=>date(row.response_deadline),invitation_status:row=>status(row.invitation_status),quotation_status:row=>status(row.quotation_status)}} action={row=><Link className="dashboard-text-link" href={`/app/supplier/rfqs/${row.rfq_id}`}>{t('View RFQ')}</Link>}/>:<div className="dashboard-empty"><IconFileText size={28}/><h3>{t('No RFQ invitations yet')}</h3><p>{t('Keep your company verified and supply categories approved to receive matching RFQs.')}</p><Link className="dashboard-text-link" href="/app/supplier/company">{t('Review company profile')}</Link></div>}
  </section>
  <section className="activity-section" aria-labelledby="supplier-quotations-heading"><div className="dashboard-panel-heading"><div><h2 id="supplier-quotations-heading">{t('Recent Quotation Activity')}</h2><p>{t('Recent drafts and submitted offers, with their current status.')}</p></div><Link className="dashboard-text-link" href="/app/supplier/quotations">{t('View all')} <IconArrowUpRight size={16}/></Link></div>
  {!!quoteRows.length&&<DataTable paginate={false} rows={quoteRows} columns={['rfq','revision','status','created_at','submitted_at']} labels={{rfq:'RFQ reference',revision:'Revision',status:'Quotation status',created_at:'Created',submitted_at:'Submitted'}} render={{rfq:row=><Link className="dashboard-text-link" href={`/app/supplier/rfqs/${row.rfq_id}`}>{String(data?.recent_rfqs?.find(rfq=>rfq.id===row.rfq_id)?.reference||t('View RFQ'))}</Link>,status:row=>status(row.status),created_at:row=>date(row.created_at),submitted_at:row=>date(row.submitted_at)}} action={row=><Link className="dashboard-text-link" href={`/app/supplier/quote/${row.id}`}>{t(row.status==='draft'?'Continue draft':'Open quotation')}</Link>}/>}
  {data&&!quoteRows.length&&<div className="dashboard-empty"><IconReceipt size={28}/><h3>{t('No quotations yet')}</h3><p>{t('Review an invitation to prepare your first quotation.')}</p><Link className="dashboard-text-link" href="/app/supplier/inbox">{t('Review invitations')}</Link></div>}
  </section>
  <details className="metric-definitions"><summary>{t('How these metrics are calculated')}</summary><p>{t('Total enquiries counts invitations for this company. Pending responses counts invited, open RFQs before their deadline. Quotation cards count each quotation once in its current backend status; drafts are excluded.')}</p><Link className="dashboard-text-link" href="/app/supplier/reports">{t('Performance reports')}</Link></details>
 </div>;
}
const statusLabels:Record<string,string>={draft:'Draft',published:'Published',collecting_quotes:'Collecting quotations',closed:'Closed',cancelled:'Cancelled',invited:'Pending',accepted:'Accepted',declined:'Declined',submitted:'Submitted',expired:'Expired'};
const statusOrder=['draft','published','collecting_quotes','invited','accepted','submitted','declined','closed','cancelled','expired'];

export default function BusinessDashboard({role}:{role:Role}){
 const {org}=useSession();const {t,arabic}=useLocale();const [filter,setFilter]=useState('all');
 useEffect(()=>setFilter('all'),[org?.id]);
 const report=useApi<Schemas['ReportSummary']>(org?`reports/summary?workspace=${role}&org_id=${encodeURIComponent(org.id)}`:null);
 const data=report.data,buyer=role==='buyer';
 const number=new Intl.NumberFormat(arabic?'ar-SA':'en-SA');
 const dates=new Intl.DateTimeFormat(arabic?'ar-SA-u-ca-gregory':'en-GB',{day:'numeric',month:'short',year:'numeric'});
 const rfqStatuses=data?.rfq_statuses||{},invitationStatuses=data?.invitation_statuses||{};
 const statuses=buyer?rfqStatuses:invitationStatuses;
 const total=Object.values(statuses).reduce((sum,count)=>sum+count,0);
 const recent=data?.recent_rfqs||[];
 const visible=filter==='all'?recent:recent.filter(r=>r.status===filter);
 const metrics=buyer?[
  {label:'Total RFQs',value:data?.rfqs_created,icon:IconFileText,href:'/app/buyer/rfqs'},
  {label:'Draft RFQs',value:rfqStatuses.draft||0,icon:IconClock,href:'/app/buyer/rfqs'},
  {label:'Active RFQs',value:data?.active_rfqs,icon:IconSend,href:'/app/buyer/rfqs'},
  {label:'Quotations received',value:data?.quotations_submitted,icon:IconReceipt,href:'/app/buyer/quotations'},
  {label:'Pending Responses',value:data?.awaiting_response,icon:IconClock,href:'/app/buyer/rfqs'},
  {label:'Closed RFQs',value:data?.closed_rfqs,icon:IconFileText,href:'/app/buyer/rfqs'},
  {label:'Suppliers invited',value:data?.invitations,icon:IconBuilding,href:'/app/buyer/matches'},
 ]:[
  {label:'Total Enquiries',value:data?.invitations,icon:IconFileText,href:'/app/supplier/inbox'},
  {label:'Pending Responses',value:data?.awaiting_response,icon:IconClock,href:'/app/supplier/inbox'},
  {label:'Quotations Submitted',value:data?.quotations_submitted,icon:IconSend,href:'/app/supplier/quotations'},
  {label:'Shortlisted Quotations',value:data?.quotation_statuses?.shortlisted||0,icon:IconReceipt,href:'/app/supplier/quotations'},
  {label:'Awarded Quotations',value:data?.quotation_statuses?.awarded||0,icon:IconReceipt,href:'/app/supplier/quotations'},
  {label:'Rejected Quotations',value:data?.quotation_statuses?.rejected||0,icon:IconReceipt,href:'/app/supplier/quotations'},
 ];
 const label=(status:string)=>t(statusLabels[status]||status.replaceAll('_',' '));
 const formatDate=(value:string)=>{const date=new Date(value);return Number.isNaN(date.getTime())?'\u2014':dates.format(date);};
 return <section className={`business-dashboard ${buyer?'buyer-dashboard':'supplier-dashboard'}`}>
  <header className="dashboard-heading"><div><span className="section-label">{t('YOUR WORKSPACE')}</span><h1>{t(buyer?'Buyer overview':'Supplier overview')}</h1><p>{t(buyer?'Track your RFQs, supplier responses and next steps.':'Keep up with RFQ invitations, quotation progress and response deadlines.')}</p><div className="dashboard-company"><IconBuilding size={16}/><strong>{org?.name||t('Company profile needed')}</strong>{org&&<span className={`dashboard-badge ${org.verified_status==='verified'?'accepted':'draft'}`}>{t(org.verified_status==='verified'?'Verified':org.verified_status==='pending'?'Pending review':org.verified_status==='rejected'?'Rejected':'Not verified')}</span>}</div></div><div className="dashboard-heading-actions"><Button type="button" variant="outline" disabled={report.fetching||!org} onClick={report.reload}><IconRefresh size={17}/>{t('Refresh')}</Button><Button asChild><Link href={buyer?'/app/buyer/rfqs/new':'/app/supplier/inbox'}>{buyer?<IconPlus size={17}/>:<IconArrowUpRight size={17}/>} {t(buyer?'Create an RFQ':'Review invitations')}</Link></Button></div></header>
  {!org?<div className="panel dashboard-empty"><IconBuilding size={32}/><h2>{t('Set up your company to get started')}</h2><p>{t('Your company workspace brings RFQs, invitations and quotations together.')}</p><Button asChild><Link href={`/app/${role}/company`}>{t('Company profile')}</Link></Button></div>:<>
  <State error={report.error} retry={report.reload}/>{report.fetching&&data&&<p className="updating-state" role="status">{t("Updating records...")}</p>}{report.loading?<DashboardSkeleton metrics={metrics.length}/>:<>
  <div className="dashboard-metrics">{metrics.map(metric=><Link className="dashboard-metric" href={metric.href} key={metric.label}><span className="dashboard-metric-icon"><metric.icon size={19}/></span><span className="dashboard-metric-label">{t(metric.label)}</span><strong className="dashboard-metric-number">{data?number.format(metric.value??0):'\u2014'}</strong></Link>)}</div>
  {!buyer&&<SupplierActivity data={data} />}
  {buyer&&<div className="dashboard-main-grid">
   <section className="panel dashboard-recent" aria-labelledby="recent-rfqs-heading"><div className="dashboard-panel-heading"><div><h2 id="recent-rfqs-heading">{t('Recent RFQs')}</h2><p>{t(buyer?'Your six most recently created requests.':'The six newest requests your company has been invited to.')}</p></div><Link className="dashboard-text-link" href={buyer?'/app/buyer/rfqs':'/app/supplier/inbox'}>{t('View all')} <IconArrowUpRight size={16}/></Link></div>
   <div className="dashboard-filter-row"><label className="field">{t('RFQ status')}<select value={filter} onChange={event=>setFilter(event.target.value)}><option value="all">{t('All statuses')}</option>{Object.keys(rfqStatuses).sort((a,b)=>statusOrder.indexOf(a)-statusOrder.indexOf(b)).map(status=><option value={status} key={status}>{label(status)}</option>)}</select></label><span>{number.format(visible.length)} {t(visible.length===1?'recent request':'recent requests')}</span></div>
   {!!visible.length&&<div className="table-wrap"><table className="data-table dashboard-rfq-table"><thead><tr><th>{t('RFQ / title')}</th><th>{t('Status')}</th><th>{t('Response deadline')}</th><th>{t('Action')}</th></tr></thead><tbody>{visible.map(rfq=>{const expired=new Date(rfq.response_deadline).getTime()<Date.now();return <tr key={rfq.id}><td><Link className="dashboard-rfq-name" href={`/app/${role}/rfqs/${rfq.id}`}>{rfq.title}</Link><span className="dashboard-rfq-reference">{rfq.reference} &middot; {rfq.city}</span></td><td><span className={`dashboard-badge ${rfq.status}`}>{label(rfq.status)}</span></td><td><span className="dashboard-deadline"><IconCalendar size={15}/>{formatDate(rfq.response_deadline)}</span>{expired&&!['closed','cancelled'].includes(rfq.status)&&<small className="dashboard-expired">{t('Deadline passed')}</small>}</td><td><Link className="dashboard-text-link" href={`/app/${role}/rfqs/${rfq.id}`}>{t(buyer&&rfq.status==='draft'?'Continue draft':'View RFQ')}<IconArrowUpRight size={16}/></Link></td></tr>;})}</tbody></table></div>}
   {!report.loading&&!report.error&&!visible.length&&<div className="dashboard-empty"><IconFileText size={30}/><h3>{t(filter==='all'?'No RFQs yet':'No recent RFQs with this status')}</h3><p>{t(buyer?'Create a request to start receiving supplier quotations.':'New RFQ invitations will appear here when buyers invite your company.')}</p>{buyer&&filter==='all'&&<Link className="dashboard-text-link" href="/app/buyer/rfqs/new">{t('Create an RFQ')}<IconPlus size={16}/></Link>}</div>}
   </section>
   <section className="panel dashboard-status-panel" aria-labelledby="dashboard-status-heading"><div className="dashboard-panel-heading"><div><h2 id="dashboard-status-heading">{t(buyer?'RFQ status overview':'Invitation status overview')}</h2><p>{t('All records for the selected company.')}</p></div></div><div className="dashboard-status-total"><strong>{data?number.format(total):'\u2014'}</strong><span>{t(buyer?'Total RFQs':'Total invitations')}</span></div><div className="dashboard-status-list">{Object.entries(statuses).sort(([a],[b])=>statusOrder.indexOf(a)-statusOrder.indexOf(b)).map(([status,count])=><div className="dashboard-status-item" key={status}><div><span><i className={`dashboard-status-dot ${status}`}/>{label(status)}</span><strong>{number.format(count)}</strong></div><div className="dashboard-status-track" aria-hidden="true"><span className={status} style={{width:`${total?count/total*100:0}%`}}/></div></div>)}</div>{data&&!total&&<p>{t(buyer?'Your RFQ statuses will appear here.':'Your invitation statuses will appear here.')}</p>}</section>
  </div>}
  {buyer&&<div className="dashboard-bottom-grid"><section className="panel"><h2>{t('Recent quotations')}</h2>{data?.recent_quotations?.length?<DataTable paginate={false} rows={data.recent_quotations} columns={['status','submitted_at']} labels={{status:'Quotation status',submitted_at:'Submitted'}} action={quote=><Link className="dashboard-text-link" href={`/app/buyer/quote/${quote.id}`}>{t('Open quotation')}</Link>}/>:<p>{t('No quotations yet')}</p>}</section><section className="panel"><h2>{t('Upcoming RFQ deadlines')}</h2>{data?.upcoming_deadlines?.length?data.upcoming_deadlines.map(rfq=><p key={rfq.id}><Link className="dashboard-text-link" href={`/app/buyer/rfqs/${rfq.id}`}>{rfq.title}</Link><span className="table-secondary">{formatDate(rfq.response_deadline)}</span></p>):<p>{t('No active deadlines')}</p>}</section><section className="panel"><h2>{t('Recent notification activity')}</h2>{data?.recent_notifications?.length?data.recent_notifications.map(notice=><p key={notice.id}>{notice.message}<span className="table-secondary">{formatDate(notice.created_at)}</span></p>):<p>{t('No recent notifications')}</p>}</section></div>}
  <div className="dashboard-bottom-grid"><section className="panel dashboard-next"><span className="dashboard-metric-icon"><IconClock size={22}/></span><div><h2>{t(buyer?'Keep your requests moving':'Continue your quotations')}</h2><p>{t(buyer?'Draft RFQs ready to continue':'Quotation drafts ready to continue')}: <strong>{data?number.format(buyer?rfqStatuses.draft||0:data.quotation_drafts):'\u2014'}</strong></p></div><Link className="dashboard-text-link" href={buyer?'/app/buyer/rfqs':'/app/supplier/quotations'}>{t(buyer?'Review RFQs':'Review quotations')}<IconArrowUpRight size={16}/></Link></section><section className="panel dashboard-shortcuts"><h2>{t('Workspace shortcuts')}</h2><div><Link href={`/app/${role}/company`}>{t('Company profile')}<IconArrowUpRight size={16}/></Link><Link href={`/app/${role}/reports`}>{t('Performance reports')}<IconArrowUpRight size={16}/></Link><Link href={`/app/${role}/notifications`}>{t('Notifications')}<IconArrowUpRight size={16}/></Link></div></section></div>
  </>}</>}
  <section className="panel dashboard-support" aria-labelledby="dashboard-support-heading"><h2 id="dashboard-support-heading">{t('Need help?')}</h2><p>{t('Contact Procumaterial support for help with your workspace.')}</p><div className="live-actions"><Link className="dashboard-text-link" href="/contact">{t('Contact support')}<IconArrowUpRight size={16}/></Link><a className="dashboard-text-link" href="mailto:support@procumaterial.com">support@procumaterial.com</a><a className="dashboard-text-link" href="tel:0501496516">0501496516</a></div></section>
 </section>;
}
