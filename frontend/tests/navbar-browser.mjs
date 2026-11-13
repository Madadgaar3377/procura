// Local production browser checks. Never calls account APIs or writes server data.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';

const origin=process.env.NAVBAR_TEST_ORIGIN||'http://127.0.0.1:13100';
assert.ok(['localhost','127.0.0.1'].includes(new URL(origin).hostname));
const directory=path.resolve('.test-runtime/navbar');
await fs.mkdir(directory,{recursive:true});
const port=24000+Math.floor(Math.random()*3000);
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
  '--headless=new','--disable-extensions','--disable-background-networking',
  '--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,
  `--user-data-dir=${path.join(directory,`chrome-${process.pid}`)}`,'about:blank',
],{windowsHide:true,stdio:'ignore'});
let ws;
const errors=[],requests=[],observations=[];
try{
  let target;
  for(let i=0;i<100;i++){
    try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(x=>x.type==='page');if(target)break;}catch{}
    await delay(200);
  }
  assert.ok(target,'Headless Chrome starts');
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();
  ws.addEventListener('message',event=>{
    const result=JSON.parse(event.data);
    if(result.method==='Runtime.exceptionThrown')errors.push(result.params.exceptionDetails.exception?.description||result.params.exceptionDetails.text);
    if(result.method==='Runtime.consoleAPICalled'&&result.params.type==='error')errors.push(result.params.args.map(x=>x.value||x.description||'').join(' '));
    if(result.method==='Network.requestWillBeSent')requests.push(result.params.request.url);
    if(result.id){const task=pending.get(result.id);pending.delete(result.id);if(result.error)task?.reject(new Error(result.error.message));else task?.resolve(result.result);}
  });
  function command(method,params={}){return new Promise((resolve,reject)=>{
    const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(new Error('Timed out: '+method));},30000);
    pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
    ws.send(JSON.stringify({id,method,params}));
  });}
  async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
  async function until(expression){for(let i=0;i<150;i++){if(await evaluate(expression))return;await delay(100);}throw new Error('Timed out: '+expression);}
  async function navigate(locale,pathname='/'){
    await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin,path:'/'});
    await evaluate('window.__previousNavbar=true');
    await command('Page.navigate',{url:`${origin}${pathname}?lang=${locale}`});
    await until(`!window.__previousNavbar&&document.readyState==='complete'&&document.querySelector('.pm-header')?.dataset.interactive==='ready'`);
    await evaluate('document.fonts.ready');await delay(400);
  }
  async function click(selector){
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'nearest',behavior:'instant'})`);
    const point=await evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    await command('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
    await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
    await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
  }
  async function key(key,code=key,windowsVirtualKeyCode=key==='Escape'?27:9,modifiers=0){
    await command('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode,modifiers});
    await command('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode,modifiers});
  }
  async function screenshot(name){const r=await command('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(directory,name+'.png'),Buffer.from(r.data,'base64'));}
  async function openMenu(){await click('.pm-header .pm-menu-button');await until(`document.querySelector('.pm-glass-menu').open`);await delay(250);}
  async function closed(){await until(`!document.querySelector('.pm-glass-menu').open&&document.querySelector('.pm-header .pm-menu-button').getAttribute('aria-expanded')==='false'`);}
  await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
  await command('Emulation.setFocusEmulationEnabled',{enabled:true});
  await command('Page.addScriptToEvaluateOnNewDocument',{source:`window.__navbarCLS=0;new PerformanceObserver(l=>{for(const e of l.getEntries())if(!e.hadRecentInput)window.__navbarCLS+=e.value;}).observe({type:'layout-shift',buffered:true});`});
  const destinations=['/#buyers','/#suppliers','/#how-it-works','/industries','/suppliers','/pricing','/contact'];
  for(const width of [320,375,768,1024,1279,1280,1440,1920])for(const locale of ['en','ar']){
    await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});
    await navigate(locale);
    const layout=await evaluate(`(()=>{const box=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};return {width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,header:box('.pm-header'),row:box('.pm-nav-row'),brand:box('.pm-header .pm-brand'),nav:box('.pm-desktop-nav'),actions:box('.pm-nav-actions'),mainTop:document.querySelector('main').getBoundingClientRect().top+scrollY,glass:getComputedStyle(document.querySelector('.pm-nav-row'),'::before').backdropFilter,radius:getComputedStyle(document.querySelector('.pm-nav-row')).borderRadius,links:[...document.querySelectorAll('.pm-desktop-nav a')].map(a=>a.getAttribute('href')),signin:document.querySelector('.pm-header .pm-signin').getAttribute('href'),signup:document.querySelector('.pm-header [data-role-cta=auto]').getAttribute('href'),direction:document.documentElement.dir};})()`);
    assert.equal(layout.overflow,false,`${width}/${locale}: no overflow`);
    assert.deepEqual(layout.links,destinations,'All original destinations remain');
    assert.equal(layout.signin,'/login');assert.equal(layout.signup,'/register');
    assert.equal(layout.direction,locale==='ar'?'rtl':'ltr');
    assert.equal(layout.radius,'9999px');assert.match(layout.glass,/blur\(16px\)/);
    assert.ok(layout.row.y>=12&&layout.row.y<=20,'Comfortable top gutter');
    assert.ok(layout.row.x>=12&&layout.row.right<=width-12&&layout.row.width<=1280,'Floating bar fits');
    if(width>=1280){
      const ordered=locale==='ar'?[layout.actions,layout.nav,layout.brand]:[layout.brand,layout.nav,layout.actions];
      assert.ok(ordered[0].right<=ordered[1].x+1&&ordered[1].right<=ordered[2].x+1,`${width}/${locale}: logo, links and actions do not overlap: ${JSON.stringify(ordered)}`);
    }
    if([320,375,1280,1440].includes(width))await screenshot(`${width}-${locale}-top`);
    await evaluate(`document.querySelector('#buyers').scrollIntoView({block:'start',behavior:'instant'})`);
    await until(`document.querySelector('.pm-header').classList.contains('is-scrolled')&&document.querySelector('.pm-desktop-nav a[href="/#buyers"]').getAttribute('aria-current')==='location'`);
    await delay(220);
    const sticky=await evaluate(`({headerTop:document.querySelector('.pm-header').getBoundingClientRect().top,mainTop:document.querySelector('main').getBoundingClientRect().top+scrollY,glassTransform:getComputedStyle(document.querySelector('.pm-nav-row'),'::before').transform})`);
    assert.equal(sticky.headerTop,0,'Header remains sticky');
    assert.ok(Math.abs(sticky.mainTop-layout.mainTop)<1,'Compacting glass never moves page content');
    assert.match(sticky.glassTransform,/matrix\(1, 0, 0, 0.9,/,'Glass surface compacts');
    if(width<1280){
      await openMenu();
      const panel=await evaluate(`(()=>{const n=document.querySelector('.pm-glass-menu'),r=n.getBoundingClientRect();return {x:r.x,right:r.right,y:r.y,bottom:r.bottom,focus:n.contains(document.activeElement),expanded:document.querySelector('.pm-header .pm-menu-button').getAttribute('aria-expanded'),links:[...n.querySelectorAll('nav a')].map(a=>a.getAttribute('href')),touch:[...n.querySelectorAll('a,button')].every(a=>a.getBoundingClientRect().height>=44)};})()`);
      assert.ok(panel.x>=0&&panel.right<=width+1&&panel.y>=84&&panel.bottom<=900,'Dropdown fits viewport');
      assert.ok(panel.focus&&panel.touch,'Focus containment and 44px targets');assert.equal(panel.expanded,'true');assert.deepEqual(panel.links,destinations);
      await key('Tab','Tab',9,8);
      assert.ok(await evaluate(`document.activeElement===document.querySelector('.pm-glass-menu [data-marketing-language]')`),'Shift+Tab wraps to the last control');
      await key('Tab');
      for(let i=0;i<14;i++){await key('Tab');assert.ok(await evaluate(`document.querySelector('.pm-glass-menu').contains(document.activeElement)`),'Tab stays inside modal');}
      await key('Escape');await closed();
      assert.ok(await evaluate(`document.activeElement===document.querySelector('.pm-header .pm-menu-button')`),'Escape restores focus');
      await openMenu();
      await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:width/2,y:4});
      await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:width/2,y:4});
      await closed();
      await openMenu();
      await click('.pm-glass-menu nav a[href="/#buyers"]');await closed();
      assert.equal(await evaluate('location.hash'),'#buyers','A section link navigates and closes');
    }
    observations.push({width,locale,layout,sticky,cls:await evaluate('window.__navbarCLS')});
    console.log(`${width}px ${locale}: floating/sticky/links${width<1280?'/menu/keyboard/outside click':''} passed`);
  }
  // A short viewport scrolls only inside the panel; language remains reachable.
  await command('Emulation.setDeviceMetricsOverride',{width:320,height:568,deviceScaleFactor:1,mobile:true});
  await navigate('en');await openMenu();await screenshot('320-en-menu');
  await click('.pm-glass-menu [data-marketing-language]');
  await until(`document.documentElement.lang==='ar'&&document.querySelector('.pm-glass-menu').dir==='rtl'`);
  assert.ok(await evaluate(`document.querySelector('.pm-glass-menu').open&&document.documentElement.scrollWidth<=innerWidth`),'Language switch preserves the open panel and geometry');
  await delay(250);await screenshot('320-ar-menu');
  assert.ok(await evaluate(`document.querySelector('.pm-glass-menu').getBoundingClientRect().bottom<=innerHeight-11`),'Short viewport panel stays in bounds');
  await key('Escape');await closed();
  // Collapse a modal on desktop resize so no hidden focus trap/body lock survives.
  await openMenu();
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});await closed();
  assert.notEqual(await evaluate('document.body.style.overflow'),'hidden');
  // Public routes share the same bar and active destination; dashboard shell is separate.
  await navigate('en','/about');
  assert.ok(await evaluate(`!!document.querySelector('.pm-glass-header')`));
  await command('Emulation.setDeviceMetricsOverride',{width:375,height:900,deviceScaleFactor:1,mobile:true});
  await openMenu();await key('Escape');await closed();
  await openMenu();await click('.pm-glass-menu nav a[href="/#buyers"]');
  await until(`location.pathname==='/'&&document.querySelector('.pm-header')?.dataset.interactive==='ready'`);await closed();
  await openMenu();await click('.pm-glass-menu nav a[href="/contact"]');
  await until(`location.pathname==='/contact'&&document.querySelector('.pm-header')?.dataset.interactive==='ready'`);await closed();
  assert.equal(await evaluate(`document.querySelector('.pm-desktop-nav a[href="/contact"]').getAttribute('aria-current')`),'page','Client navigation updates the active destination');
  assert.notEqual(await evaluate('document.body.style.overflow'),'hidden','Route navigation releases the body lock');
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await navigate('ar');await openMenu();
  assert.equal(await evaluate(`document.getAnimations().filter(a=>a.playState==='running').length`),0,'Reduced motion has no running decorative/menu animation');
  await key('Escape');await closed();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await openMenu();await click('.pm-glass-menu .pm-menu-button');
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});await closed();
  const noObserver=await command('Page.addScriptToEvaluateOnNewDocument',{source:'delete window.IntersectionObserver'});
  await navigate('en');await evaluate(`window.scrollTo({top:300,behavior:'instant'})`);
  await until(`document.querySelector('.pm-header').classList.contains('is-scrolled')`);
  await command('Page.removeScriptToEvaluateOnNewDocument',{identifier:noObserver.identifier});
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
  await command('Emulation.setScriptExecutionDisabled',{value:true});
  await command('Page.navigate',{url:`${origin}/?lang=en`});await delay(1200);
  assert.ok(await evaluate(`getComputedStyle(document.querySelector('.pm-nav-row')).opacity==='1'&&document.querySelectorAll('.pm-desktop-nav a[href]').length===7`),'Navigation renders without hydration');
  await command('Emulation.setScriptExecutionDisabled',{value:false});
  assert.equal(requests.filter(url=>/\/api\/(?:backend|v1)\/(?:auth\/me|organizations)(?:\?|$)/.test(url)).length,0,'No account APIs on initial navigation');
  assert.deepEqual(errors,[],'No console or hydration errors');
  console.log('Short viewport, language switch, resize, public routes, reduced motion, observer fallback and SSR passed');
}finally{
  await fs.writeFile(path.join(directory,'results.json'),JSON.stringify({observations,errors,requests},null,2));
  ws?.close();chrome.kill();
}
