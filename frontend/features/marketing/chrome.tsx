"use client";

import Link from 'next/link';
import {usePathname, useRouter} from 'next/navigation';
import {useEffect, useRef, useState, type MouseEvent} from 'react';
import {IconArrowRight, IconArrowUp, IconMenu2, IconWorld, IconX} from '@tabler/icons-react';
import {useLocale} from '@/lib/locale';
import {marketingCopy, type MarketingLocale} from './copy';

const navigation=['/#buyers','/#suppliers','/#how-it-works','/industries','/suppliers','/pricing','/contact'];

export function MarketingCTA({role='buyer',children,className='',arrow=true}:{role?:'buyer'|'supplier'|'auto';children:React.ReactNode;className?:string;arrow?:boolean}){
  const [busy,setBusy]=useState(false);
  const fallback=role==='auto'?'/register':`/register?role=${role}`;
  async function follow(event:MouseEvent<HTMLAnchorElement>){
    if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    event.preventDefault();if(busy)return;setBusy(true);
    let target=fallback;
    try{
      // Session checks happen only after an intentional CTA click, never on page load.
      const options={credentials:'include' as const,cache:'no-store' as const,signal:AbortSignal.timeout(3000)};
      const response=await fetch('/api/backend/auth/me',options);
      if(response.ok){
        const user=await response.json();
        if(user.is_admin)target='/app/admin/dashboard';
        else{
          const organizations=await fetch('/api/backend/organizations',options);
          if(organizations.ok){
            const records=await organizations.json() as {is_active:boolean;is_buyer:boolean;is_supplier:boolean}[];
            target=role==='auto'?'/organizations':records.some(org=>org.is_active&&(role==='buyer'?org.is_buyer:org.is_supplier))
              ?role==='buyer'?'/app/buyer/rfqs/new':'/app/supplier/company':'/organizations';
          }else target='/organizations';
        }
      }
    }catch{/* An unavailable session check preserves the registration route. */}
    window.location.assign(target);
  }
  return <Link href={fallback} prefetch={false} className={`pm-button ${className}`} onClick={follow} aria-busy={busy} data-role-cta={role}>{children}{arrow&&<IconArrowRight className="pm-arrow" size={18} aria-hidden="true"/>}</Link>;
}

export function MarketingLanguage({locale}:{locale?:MarketingLocale}){
  const {arabic,setArabic}=useLocale();const router=useRouter();
  const current=locale||(arabic?'ar':'en');
  return <button className="pm-language" type="button" onClick={()=>{
    const next=current==='ar'?'en':'ar';setArabic(next==='ar');
    const url=new URL(window.location.href);url.searchParams.set('lang',next);
    router.replace(url.pathname+url.search+url.hash,{scroll:false});
  }} aria-label={marketingCopy[current].language} data-marketing-language><IconWorld size={17} aria-hidden="true"/><span lang={current==='ar'?'en':'ar'}>{current==='ar'?'English':'العربية'}</span></button>;
}

export function MarketingBrand({inverse=false}:{inverse?:boolean}){
  // Preserve the existing approved text wordmark. No replacement symbol is drawn.
  return <Link href="/" className={`pm-brand${inverse?' pm-brand-inverse':''}`} aria-label="Procumaterial"><span>Procumaterial</span><i aria-hidden="true"/></Link>;
}

export function MarketingHeader({locale}:{locale?:MarketingLocale}){
  const {arabic}=useLocale();const current=locale||(arabic?'ar':'en');const c=marketingCopy[current];
  const pathname=usePathname();const dialog=useRef<HTMLDialogElement>(null);
  const [open,setOpen]=useState(false),[scrolled,setScrolled]=useState(false),[active,setActive]=useState(''),[interactive,setInteractive]=useState(false);
  useEffect(()=>{setInteractive(true);const scroll=()=>setScrolled(window.scrollY>12);scroll();window.addEventListener('scroll',scroll,{passive:true});return()=>window.removeEventListener('scroll',scroll);},[]);
  useEffect(()=>{if(!open)return;const previous=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{document.body.style.overflow=previous;};},[open]);
  useEffect(()=>{
    if(pathname!=='/')return;
    const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting)setActive('/#'+entry.target.id);},{rootMargin:'-15% 0px -65% 0px'});
    document.querySelectorAll('section[id]').forEach(node=>observer.observe(node));return()=>observer.disconnect();
  },[pathname]);
  function close(){dialog.current?.close();setOpen(false);}
  const links=(mobile=false)=>c.nav.map((label,index)=>{
    const href=navigation[index];const selected=href.includes('#')?pathname==='/'&&active===href:pathname.startsWith(href);
    return <Link href={href} key={href} aria-current={selected?(href.includes('#')?'location':'page'):undefined} onClick={mobile?close:undefined}>{label}</Link>;
  });
  return <>
    <a className="pm-skip" href="#main-content">{c.skipContent}</a>
    <header className={`pm-header${scrolled?' is-scrolled':''}`} data-interactive={interactive?'ready':undefined}>
      <div className="pm-container pm-nav-row"><MarketingBrand/>
        <nav className="pm-desktop-nav" aria-label={current==='ar'?'التنقل الرئيسي':'Main navigation'}>{links()}</nav>
        <div className="pm-nav-actions"><MarketingLanguage locale={current}/><Link href="/login" prefetch={false} className="pm-signin">{c.signIn}</Link><MarketingCTA role="auto" className="pm-header-cta">{c.getStarted}</MarketingCTA><button type="button" className="pm-menu-button" aria-label={c.openMenu} aria-expanded={open} aria-controls="pm-navigation-drawer" onClick={()=>{dialog.current?.showModal();setOpen(true);}}><IconMenu2 size={22}/></button></div>
      </div>
    </header>
    <dialog ref={dialog} id="pm-navigation-drawer" className="pm-drawer" onClose={()=>setOpen(false)} aria-label={current==='ar'?'قائمة التنقل':'Navigation menu'} onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();if(event.target===event.currentTarget&&(event.clientX<rect.left||event.clientX>rect.right))close();}}>
      <div className="pm-drawer-head"><MarketingBrand/><button type="button" className="pm-menu-button" onClick={close} aria-label={c.closeMenu}><IconX size={23}/></button></div>
      <nav>{links(true)}</nav><div className="pm-drawer-actions"><Link href="/login" className="pm-button pm-button-secondary" onClick={close}>{c.signIn}</Link><MarketingCTA role="auto">{c.getStarted}</MarketingCTA></div><p dir="ltr">{c.tagline}</p>
    </dialog>
  </>;
}

const footerHrefs=[['/how-it-works','/suppliers','/industries','/pricing'],['/app/buyer/rfqs/new','/#buyers','/#comparison'],['/register?role=supplier','/#suppliers','/app/supplier/company'],['/about','/contact','/#faq'],['/privacy','/terms']];

export function MarketingFooter({locale}:{locale?:MarketingLocale}){
  const {arabic}=useLocale();const current=locale||(arabic?'ar':'en');const c=marketingCopy[current];let labelIndex=0;
  return <footer className="pm-footer"><div className="pm-container"><div className="pm-footer-top"><div><MarketingBrand/><p dir="ltr" className="pm-official-tagline">{c.tagline}</p><p>{c.footerNote}</p></div><a href="#top" className="pm-back-top">{c.backToTop}<IconArrowUp size={18} aria-hidden="true"/></a></div>
    <div className="pm-footer-links">{footerHrefs.map((links,index)=><nav key={c.footerGroups[index]} aria-label={c.footerGroups[index]}><h3>{c.footerGroups[index]}</h3>{links.map(href=><Link key={href} href={href} prefetch={href.startsWith('/app')||href.startsWith('/register')?false:undefined}>{c.footerLinks[labelIndex++]}</Link>)}</nav>)}</div>
    <div className="pm-footer-support"><strong>{current==='ar'?'الدعم':'Support'}</strong><a href="mailto:support@procumaterial.com">support@procumaterial.com</a><a href="tel:0501496516">0501496516</a></div>
    <div className="pm-footer-bottom"><span>© {new Date().getFullYear()} Procumaterial</span><MarketingLanguage locale={current}/></div>
  </div></footer>;
}

export function MarketingEnhancements(){
  useEffect(()=>{
    const root=document.querySelector('.pm-home');if(!root||window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    root.setAttribute('data-motion','ready');
    const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){entry.target.classList.add('is-visible');observer.unobserve(entry.target);}},{threshold:0.06});
    root.querySelectorAll('.pm-reveal').forEach(node=>observer.observe(node));
    return()=>{observer.disconnect();root.removeAttribute('data-motion');};
  },[]);
  return null;
}
