"use client";

import {useEffect, useState} from 'react';
import {useAnimate} from 'motion/react-mini';
import {IconArrowRight, IconBuilding, IconCheck, IconClock, IconFileText, IconLayoutDashboard, IconMapPin, IconSearch, IconShieldCheck} from '@tabler/icons-react';
import {marketingCopy,type MarketingLocale} from './copy';

function PreviewLabel({locale}:{locale:MarketingLocale}){return <span className="pm-sample-label"><i aria-hidden="true"/>{marketingCopy[locale].illustrative}</span>;}
function PreviewTop({locale,supplier=false}:{locale:MarketingLocale;supplier?:boolean}){const p=marketingCopy[locale].preview;return <div className="pm-preview-top"><span className="pm-preview-wordmark">Procurement</span><span>{supplier?p.supplierWorkspace:p.workspace}</span><span className="pm-preview-avatar" aria-hidden="true">{supplier?'S':'B'}</span></div>;}
function ItemTable({locale}:{locale:MarketingLocale}){const p=marketingCopy[locale].preview;return <div className="pm-mini-items"><div className="pm-mini-items-heading"><span>{p.description}</span><span>{p.quantity}</span><span>{p.unit}</span></div>{[[p.material,'12'],[p.materialTwo,'24']].map(([name,quantity])=><div className="pm-mini-item" key={name}><span>{name}</span><b dir="ltr">{quantity}</b><span>{p.pcs}</span></div>)}</div>;}

export function WorkflowPreview({locale}:{locale:MarketingLocale}){
  const [tab,setTab]=useState(0);const c=marketingCopy[locale],p=c.preview;
  const [scope,animate]=useAnimate<HTMLDivElement>();
  useEffect(()=>{
    // The initial panel already enters with its parent; reserve Motion for tab interactions.
    if(tab===0)return;
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    const panel=scope.current?.querySelector<HTMLElement>(`#pm-workflow-panel-${tab}`);
    if(!panel)return;
    const animation=animate(panel,{translate:['0 6px','0 0']},{duration:.28,ease:'easeOut'});
    const preference=window.matchMedia('(prefers-reduced-motion: reduce)');
    // A tab can already be hidden during effect cleanup. Cancel without
    // committing computed styles to a non-rendered panel.
    const cancel=()=>animation.cancel();
    preference.addEventListener('change',cancel);
    return()=>{cancel();preference.removeEventListener('change',cancel);};
  },[tab,scope,animate]);
  const suppliers=[p.supplierA,p.supplierB,p.supplierC];
  function move(index:number){setTab(index);requestAnimationFrame(()=>document.querySelector<HTMLButtonElement>(`[data-workflow-tab="${index}"]`)?.focus());}
  return <div className="pm-workflow-composition">
    <div className="pm-preview-caption"><PreviewLabel locale={locale}/><span className="pm-preview-secure"><IconShieldCheck size={15}/>{p.eligible}</span></div>
    <div className="pm-workflow-window"><PreviewTop locale={locale}/>
      <div className="pm-preview-body"><div className="pm-preview-request"><span className="pm-mono">RFQ / 001</span><span className="pm-pill"><i/>{p.structured}</span></div><h2>{p.request}</h2><div className="pm-preview-location"><IconMapPin size={14}/>{p.riyadh}<span>·</span>{p.electrical}<IconArrowRight className="pm-arrow" size={12}/>{p.tools}</div>
      <div className="pm-preview-tabs" role="tablist" aria-label={p.workflowHint}>{p.tabs.map((label,index)=><button type="button" role="tab" id={`pm-workflow-tab-${index}`} aria-controls={`pm-workflow-panel-${index}`} aria-selected={tab===index} tabIndex={tab===index?0:-1} data-workflow-tab={index} key={label} onClick={()=>setTab(index)} onKeyDown={event=>{if(event.key==='ArrowRight'||event.key==='ArrowLeft'){event.preventDefault();const delta=(event.key==='ArrowRight'?1:-1)*(locale==='ar'?-1:1);move((tab+delta+4)%4);}if(event.key==='Home'){event.preventDefault();move(0);}if(event.key==='End'){event.preventDefault();move(3);}}}><span>{String(index+1).padStart(2,'0')}</span>{label}</button>)}</div>
      <div className="pm-preview-stage" ref={scope}>
        <div role="tabpanel" id="pm-workflow-panel-0" aria-labelledby="pm-workflow-tab-0" hidden={tab!==0}><div className="pm-preview-section-title"><IconFileText size={16}/>{p.requestDetails}<span>02</span></div><ItemTable locale={locale}/><div className="pm-preview-pair"><span><IconMapPin size={15}/>{p.riyadh}</span><span><IconClock size={15}/>{p.localDate}</span></div><div className="pm-preview-check"><IconCheck size={15}/>{p.structured}</div></div>
        <div role="tabpanel" id="pm-workflow-panel-1" aria-labelledby="pm-workflow-tab-1" hidden={tab!==1}><div className="pm-preview-section-title"><IconSearch size={16}/>{p.matching}<span>03</span></div>{suppliers.map((name,index)=><div className="pm-supplier-match" key={name}><span className="pm-company-initial" aria-hidden="true">{String.fromCharCode(65+index)}</span><div><b>{name}</b><small>{p.electrical} / {p.tools} · {p.riyadh}</small></div><IconCheck className="pm-match-check" size={18}/></div>)}<div className="pm-preview-check"><IconShieldCheck size={15}/>{p.eligible} · {p.coverage}</div></div>
        <div role="tabpanel" id="pm-workflow-panel-2" aria-labelledby="pm-workflow-tab-2" hidden={tab!==2}><div className="pm-preview-section-title"><IconLayoutDashboard size={16}/>{p.responses}<span>02 / 03</span></div>{suppliers.map((name,index)=><div className="pm-supplier-match" key={name}><IconBuilding size={20}/><div><b>{name}</b><small>{index<2?p.submitted:p.pending}</small></div><span className={`pm-status-dot${index===2?' pm-status-pending':''}`}/></div>)}<div className="pm-preview-check"><IconClock size={15}/>{p.pending}: 01</div></div>
        <div role="tabpanel" id="pm-workflow-panel-3" aria-labelledby="pm-workflow-tab-3" hidden={tab!==3}><div className="pm-preview-section-title"><IconLayoutDashboard size={16}/>{p.tabs[3]}<span>SAR</span></div><div className="pm-mini-comparison"><div><span>{p.price}</span><b>A</b><b>B</b></div><div><span>{p.material}</span><b>85.00</b><b>92.00</b></div><div><span>{p.materialTwo}</span><b>45.00</b><b>48.00</b></div><div className="pm-mini-total"><span>{p.total}</span><b>2,415.00</b><b>2,594.40</b></div></div><div className="pm-preview-check"><IconShieldCheck size={15}/>{p.summary}</div></div>
      </div></div><div className="pm-preview-bottom"><span>{c.notLive}</span><span className="pm-window-dots" aria-hidden="true"><i/><i/><i/></span></div>
    </div>
    <div className="pm-workflow-footnote"><span className="pm-footnote-line"/><span>{c.heroLabels[0]}<IconArrowRight className="pm-arrow" size={13}/>{c.heroLabels[3]}</span></div>
  </div>;
}
