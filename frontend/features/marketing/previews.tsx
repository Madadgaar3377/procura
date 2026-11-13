import {IconArrowRight, IconBuilding, IconCheck, IconClock, IconFileText, IconLayoutDashboard, IconMapPin, IconSearch, IconShieldCheck} from '@tabler/icons-react';
import {marketingCopy, type MarketingLocale} from './copy';
import {WorkflowTabs} from './workflow-tabs';

function PreviewLabel({locale}:{locale:MarketingLocale}){return <span className="pm-sample-label"><i aria-hidden="true"/>{marketingCopy[locale].illustrative}</span>;}
function PreviewTop({locale,supplier=false}:{locale:MarketingLocale;supplier?:boolean}){const p=marketingCopy[locale].preview;return <div className="pm-preview-top"><span className="pm-preview-wordmark">Procurement</span><span>{supplier?p.supplierWorkspace:p.workspace}</span><span className="pm-preview-avatar" aria-hidden="true">{supplier?'S':'B'}</span></div>;}
function ItemTable({locale}:{locale:MarketingLocale}){const p=marketingCopy[locale].preview;return <div className="pm-mini-items"><div className="pm-mini-items-heading"><span>{p.description}</span><span>{p.quantity}</span><span>{p.unit}</span></div>{[[p.material,'12'],[p.materialTwo,'24']].map(([name,quantity])=><div className="pm-mini-item" key={name}><span>{name}</span><b dir="ltr">{quantity}</b><span>{p.pcs}</span></div>)}</div>;}

export function WorkflowPreview({locale='en'}:{locale?:MarketingLocale}){
  const c=marketingCopy[locale],p=c.preview;
  const suppliers=[p.supplierA,p.supplierB,p.supplierC];
  const panels=[
    <><div className="pm-preview-section-title"><IconFileText size={16}/>{p.requestDetails}<span>02</span></div><ItemTable locale={locale}/><div className="pm-preview-pair"><span><IconMapPin size={15}/>{p.riyadh}</span><span><IconClock size={15}/>{p.localDate}</span></div><div className="pm-preview-check"><IconCheck size={15}/>{p.structured}</div></>,
    <><div className="pm-preview-section-title"><IconSearch size={16}/>{p.matching}<span>03</span></div>{suppliers.map((name,index)=><div className="pm-supplier-match" key={name}><span className="pm-company-initial" aria-hidden="true">{String.fromCharCode(65+index)}</span><div><b>{name}</b><small>{p.electrical} / {p.tools} · {p.riyadh}</small></div><IconCheck className="pm-match-check" size={18}/></div>)}<div className="pm-preview-check"><IconShieldCheck size={15}/>{p.eligible} · {p.coverage}</div></>,
    <><div className="pm-preview-section-title"><IconLayoutDashboard size={16}/>{p.responses}<span>02 / 03</span></div>{suppliers.map((name,index)=><div className="pm-supplier-match" key={name}><IconBuilding size={20}/><div><b>{name}</b><small>{index<2?p.submitted:p.pending}</small></div><span className={`pm-status-dot${index===2?' pm-status-pending':''}`}/></div>)}<div className="pm-preview-check"><IconClock size={15}/>{p.pending}: 01</div></>,
    <><div className="pm-preview-section-title"><IconLayoutDashboard size={16}/>{p.tabs[3]}<span>SAR</span></div><div className="pm-mini-comparison"><div><span>{p.price}</span><b>A</b><b>B</b></div><div><span>{p.material}</span><b>85.00</b><b>92.00</b></div><div><span>{p.materialTwo}</span><b>45.00</b><b>48.00</b></div><div className="pm-mini-total"><span>{p.total}</span><b>2,415.00</b><b>2,594.40</b></div></div><div className="pm-preview-check"><IconShieldCheck size={15}/>{p.summary}</div></>,
  ];
  return <div className="pm-workflow-composition">
    <div className="pm-preview-caption"><PreviewLabel locale={locale}/><span className="pm-preview-secure"><IconShieldCheck size={15}/>{p.eligible}</span></div>
    <div className="pm-workflow-window"><PreviewTop locale={locale}/>
      <div className="pm-preview-body"><div className="pm-preview-request"><span className="pm-mono">RFQ / 001</span><span className="pm-pill"><i/>{p.structured}</span></div><h2>{p.request}</h2><div className="pm-preview-location"><IconMapPin size={14}/>{p.riyadh}<span>·</span>{p.electrical}<IconArrowRight className="pm-arrow" size={12}/>{p.tools}</div>
      <WorkflowTabs tabs={p.tabs} ariaLabel={p.workflowHint} locale={locale} panels={panels}/>
      </div><div className="pm-preview-bottom"><span>{c.notLive}</span><span className="pm-window-dots" aria-hidden="true"><i/><i/><i/></span></div>
    </div>
    <div className="pm-workflow-footnote"><span className="pm-footnote-line"/><span>{c.heroLabels[0]}<IconArrowRight className="pm-arrow" size={13}/>{c.heroLabels[3]}</span></div>
  </div>;
}
