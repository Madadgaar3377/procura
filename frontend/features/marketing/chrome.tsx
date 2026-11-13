"use client";

import Link from 'next/link';
import {usePathname, useRouter} from 'next/navigation';
import {useCallback, useEffect, useRef, useState, type KeyboardEvent, type MouseEvent} from 'react';
import {useAnimate} from 'motion/react-mini';
import {IconArrowRight, IconArrowUp, IconMenu2, IconWorld, IconX} from '@tabler/icons-react';
import {useLocale} from '@/lib/locale';
import {marketingCopy, type MarketingLocale} from './copy';
import './navigation.css';

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
  }} title={marketingCopy[current].language} data-marketing-language><IconWorld size={17} aria-hidden="true"/><span lang={current==='ar'?'en':'ar'}>{current==='ar'?'English':'العربية'}</span></button>;
}

export function MarketingBrand({inverse=false}:{inverse?:boolean}){
  // Preserve the existing approved text wordmark. No replacement symbol is drawn.
  return <Link href="/" className={`pm-brand${inverse?' pm-brand-inverse':''}`} aria-label="Procumaterial"><span>Procumaterial</span><i aria-hidden="true"/></Link>;
}

export function MarketingHeader({locale}:{locale?:MarketingLocale}){
  const {arabic}=useLocale();const current=locale||(arabic?'ar':'en');const c=marketingCopy[current];
  const pathname=usePathname();const marker=useRef<HTMLSpanElement>(null);
  const [dialog,animate]=useAnimate<HTMLDialogElement>();
  const menuMotion=useRef<ReturnType<typeof animate>|null>(null);const closing=useRef(false);
  const [open,setOpen]=useState(false),[scrolled,setScrolled]=useState(false),[active,setActive]=useState(''),[interactive,setInteractive]=useState(false);
  useEffect(()=>{
    const scroll=()=>setScrolled(window.scrollY>16);
    const frame=requestAnimationFrame(()=>{setInteractive(true);scroll();});
    if(typeof IntersectionObserver==='function'&&marker.current){
      const observer=new IntersectionObserver(([entry])=>setScrolled(!entry.isIntersecting));
      observer.observe(marker.current);
      return()=>{cancelAnimationFrame(frame);observer.disconnect();};
    }
    window.addEventListener('scroll',scroll,{passive:true});
    return()=>{cancelAnimationFrame(frame);window.removeEventListener('scroll',scroll);};
  },[]);
  useEffect(()=>{if(!open)return;const previous=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{document.body.style.overflow=previous;};},[open]);
  useEffect(()=>{
    if(pathname!=='/'||typeof window.IntersectionObserver!=='function')return;
    const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting)setActive('/#'+entry.target.id);},{rootMargin:'-15% 0px -65% 0px'});
    document.querySelectorAll('section[id]').forEach(node=>observer.observe(node));return()=>observer.disconnect();
  },[pathname]);
  const resetMenuMotion=useCallback(()=>{
    // Cancel before hiding a panel: stop() would try to commit styles on hidden DOM.
    menuMotion.current?.cancel();menuMotion.current=null;
    dialog.current?.style.removeProperty('opacity');
    dialog.current?.style.removeProperty('transform');
  },[dialog]);
  useEffect(()=>{
    const preference=window.matchMedia('(prefers-reduced-motion: reduce)');
    const desktop=window.matchMedia('(min-width: 1280px)');
    const reduce=()=>{if(preference.matches){resetMenuMotion();if(closing.current)dialog.current?.close();}};
    const resize=()=>{if(desktop.matches){resetMenuMotion();dialog.current?.close();}};
    preference.addEventListener('change',reduce);desktop.addEventListener('change',resize);
    return()=>{preference.removeEventListener('change',reduce);desktop.removeEventListener('change',resize);resetMenuMotion();};
  },[dialog,resetMenuMotion]);
  function show(){
    const node=dialog.current;if(!node||node.open)return;
    resetMenuMotion();closing.current=false;node.showModal();setOpen(true);
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches||!node.animate)return;
    const animation=animate(node,{opacity:[.6,1],transform:['translateY(-8px) scale(.985)','translateY(0px) scale(1)']},{duration:.2,ease:'easeOut'});
    menuMotion.current=animation;
    void animation.then(()=>{if(menuMotion.current===animation)resetMenuMotion();});
  }
  function close(){
    const node=dialog.current;
    if(!node?.open||closing.current)return;
    resetMenuMotion();closing.current=true;
    const finish=()=>{resetMenuMotion();node.close();};
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches||!node.animate){finish();return;}
    const animation=animate(node,{opacity:[1,0],transform:['translateY(0px)','translateY(-6px)']},{duration:.16,ease:'easeOut'});
    menuMotion.current=animation;
    void animation.then(()=>{if(menuMotion.current===animation&&closing.current)finish();});
  }
  function containTab(event:KeyboardEvent<HTMLDialogElement>){
    if(event.key!=='Tab')return;
    const controls=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),[tabindex="0"]')).filter(node=>node.getClientRects().length&&node.tabIndex>=0);
    const first=controls[0],last=controls[controls.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
  function closeForNavigation(){
    // A route transition may hide the old tree immediately. Release its animation first.
    resetMenuMotion();closing.current=false;dialog.current?.close();
  }
  const links=(mobile=false)=>c.nav.map((label,index)=>{
    const href=navigation[index];const selected=href.includes('#')?pathname==='/'&&active===href:pathname.startsWith(href);
    return <Link href={href} key={href} aria-current={selected?(href.includes('#')?'location':'page'):undefined} onClick={mobile?()=>{if(href.startsWith(pathname+'#'))close();else closeForNavigation();}:undefined}>{label}</Link>;
  });
  return <>
    <a className="pm-skip" href="#main-content">{c.skipContent}</a>
    <span className="pm-navigation-marker" ref={marker} aria-hidden="true"/>
    <div className="pm-navigation-shell"><header className={`pm-header pm-glass-header${scrolled?' is-scrolled':''}`} data-interactive={interactive?'ready':undefined}>
      <div className="pm-container pm-nav-row"><MarketingBrand/>
        <nav className="pm-desktop-nav" aria-label={current==='ar'?'التنقل الرئيسي':'Main navigation'}>{links()}</nav>
        <div className="pm-nav-actions"><MarketingLanguage locale={current}/><Link href="/login" prefetch={false} className="pm-signin">{c.signIn}</Link><MarketingCTA role="auto" className="pm-header-cta">{c.getStarted}</MarketingCTA><button type="button" className="pm-menu-button" aria-label={c.openMenu} aria-expanded={open} aria-haspopup="dialog" aria-controls="pm-navigation-drawer" onClick={show}><IconMenu2 size={22} aria-hidden="true"/></button></div>
      </div>
    </header></div>
    <dialog ref={dialog} id="pm-navigation-drawer" className="pm-drawer pm-glass-menu" dir={current==='ar'?'rtl':'ltr'} onKeyDown={containTab} onCancel={event=>{event.preventDefault();close();}} onClose={()=>{resetMenuMotion();closing.current=false;setOpen(false);}} aria-label={current==='ar'?'قائمة التنقل':'Navigation menu'} onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();if(event.target===event.currentTarget&&(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom))close();}}>
      <div className="pm-drawer-head"><span className="pm-navigation-label">{current==='ar'?'استكشف المنصة':'Explore the platform'}</span><button type="button" className="pm-menu-button" onClick={close} aria-label={c.closeMenu} autoFocus><IconX size={21} aria-hidden="true"/></button></div>
      <nav aria-label={current==='ar'?'التنقل الرئيسي':'Main navigation'}>{links(true)}</nav><div className="pm-drawer-actions"><Link href="/login" prefetch={false} className="pm-button pm-button-secondary" onClick={closeForNavigation}>{c.signIn}</Link><MarketingCTA role="auto">{c.getStarted}</MarketingCTA></div><div className="pm-drawer-meta"><MarketingLanguage locale={current}/><p dir="ltr">{c.tagline}</p></div>
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

export function MarketingEnhancements({locale}:{locale?:MarketingLocale}){
  useEffect(()=>{
    const root=document.querySelector<HTMLElement>('.pm-home');if(!root)return;
    let disposed=false;let cleanup:(()=>void)|undefined;
    // Nonessential enhancements load separately from navigation and CTA hydration.
    void import('./home-motion').then(({mountHomeMotion})=>{
      if(!disposed)cleanup=mountHomeMotion(root);
    }).catch(()=>{/* Server-rendered content and native controls remain available. */});
    return()=>{disposed=true;cleanup?.();};
  },[locale]);
  return null;
}
