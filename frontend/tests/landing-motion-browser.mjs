// Local-only visual baselines and browser checks; never writes production data.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';

const origin=process.env.LANDING_TEST_ORIGIN||'http://127.0.0.1:13093';
assert.ok(['localhost','127.0.0.1'].includes(new URL(origin).hostname));
const baseline=process.argv.includes('--baseline');
const motionOnly=process.argv.includes('--motion-only');
const performance=process.argv.includes('--perf');
const stage=baseline?'before':'after';
const directory=path.resolve('.test-runtime/landing-motion');
await fs.mkdir(directory,{recursive:true});
const port=24000+Math.floor(Math.random()*3000);
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
 '--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking',
 '--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,
 `--user-data-dir=${path.join(directory,`chrome-${process.pid}`)}`,'about:blank'
],{windowsHide:true,stdio:'ignore'});
let ws;
try{
 let target;
 for(let i=0;i<100;i++){
  try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(x=>x.type==='page');if(target)break;}catch{}
  await delay(200);
 }
 assert.ok(target,'Local headless browser starts');
 ws=new WebSocket(target.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 let sequence=0;const pending=new Map(),errors=[],requests=[];
 ws.addEventListener('message',event=>{
  const result=JSON.parse(event.data);
  if(result.method==='Runtime.exceptionThrown')errors.push(result.params.exceptionDetails.exception?.description||result.params.exceptionDetails.text);
  if(result.method==='Runtime.consoleAPICalled'&&result.params.type==='error')errors.push(result.params.args.map(x=>x.value||x.description||'').join(' '));
  if(result.method==='Network.requestWillBeSent')requests.push(result.params.request.url);
  if(result.id){const task=pending.get(result.id);pending.delete(result.id);if(result.error)task?.reject(new Error(result.error.message));else task?.resolve(result.result);}
 });
 function command(method,params={}){return new Promise((resolve,reject)=>{
  const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(new Error('Timed out: '+method));},60000);
  pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
  ws.send(JSON.stringify({id,method,params}));
 });}
 async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
 async function until(expression){for(let i=0;i<200;i++){if(await evaluate(expression))return;await delay(100);}throw new Error('Condition timed out: '+expression);}
 async function navigate(locale){
  await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin,path:'/'});
  await evaluate('window.__oldLanding=true');
  await command('Page.navigate',{url:`${origin}/?lang=${locale}`});
  await until(`!window.__oldLanding&&document.readyState==='complete'&&document.querySelector('.pm-header')?.dataset.interactive==='ready'`);
  await evaluate(`document.fonts.ready.then(()=>{const s=document.createElement('style');s.textContent='nextjs-portal{display:none!important}';document.head.append(s);})`);
  await delay(200);
 }
 await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
 await command('Page.addScriptToEvaluateOnNewDocument',{source:`window.__landingVitals={cls:0,lcp:0,interactions:[],events:[]};try{new PerformanceObserver(l=>{for(const e of l.getEntries())if(!e.hadRecentInput)window.__landingVitals.cls+=e.value;}).observe({type:'layout-shift',buffered:true});new PerformanceObserver(l=>{for(const e of l.getEntries())window.__landingVitals.lcp=e.startTime;}).observe({type:'largest-contentful-paint',buffered:true});new PerformanceObserver(l=>{for(const e of l.getEntries())if(e.interactionId){window.__landingVitals.interactions.push(e.duration);window.__landingVitals.events.push({name:e.name,duration:e.duration,processing:e.processingEnd-e.processingStart,inputDelay:e.processingStart-e.startTime,id:e.interactionId});}}).observe({type:'event',buffered:true,durationThreshold:16});}catch{}`});
 await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 const observations=[];
 for(const width of motionOnly||performance?[]:[320,375,430,768,1024,1440,1920]){
  await command('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<768});
  for(const locale of ['en','ar']){
   await navigate(locale);
   const snapshot=await evaluate(`(()=>{const root=document.querySelector('.pm-home');const props=['fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','color','backgroundColor','backgroundImage','borderTop','borderBottom','borderLeft','borderRight','borderRadius','paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginRight','marginBottom','marginLeft','display','gap','gridTemplateColumns'];return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,height:root.scrollHeight,sections:[...root.querySelectorAll('main>section')].map(n=>n.id||n.className),elements:[...root.querySelectorAll('*')].filter(n=>n instanceof HTMLElement&&!['SCRIPT','STYLE'].includes(n.tagName)).map(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return {tag:n.tagName,text:n.children.length?null:n.textContent,href:n.getAttribute('href'),rect:[r.x,r.y,r.width,r.height].map(v=>Math.round(v*100)/100),styles:Object.fromEntries(props.map(p=>[p,s[p]]))};})};})()`);
   assert.ok(snapshot.scrollWidth<=width+1,`${width}/${locale}: no horizontal overflow`);
   await fs.writeFile(path.join(directory,`${stage}-${width}-${locale}.json`),JSON.stringify(snapshot));
   if(!baseline){const approved=JSON.parse(await fs.readFile(path.join(directory,`before-${width}-${locale}.json`),'utf8'));assert.deepEqual(snapshot,approved,`${width}/${locale}: approved geometry, copy, links and visual styles preserved`);}
   const metrics=await command('Page.getLayoutMetrics');
   const screenshot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width,height:metrics.cssContentSize.height,scale:1}});
   await fs.writeFile(path.join(directory,`${stage}-${width}-${locale}.png`),Buffer.from(screenshot.data,'base64'));
   observations.push({width,locale,height:snapshot.height,elements:snapshot.elements.length,vitals:await evaluate('window.__landingVitals')});
  }
  console.log(`${stage}: ${width}px English + Arabic captured`);
 }
 if(!baseline){
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await command('Emulation.setFocusEmulationEnabled',{enabled:true});
  if(performance){
   await command('Network.setCacheDisabled',{cacheDisabled:true});
   await command('Network.emulateNetworkConditions',{offline:false,latency:150,downloadThroughput:200000,uploadThroughput:93750});
   await command('Emulation.setCPUThrottlingRate',{rate:4});
  }
  for(const width of performance?[375,1440]:[320,375,430,768,1024,1440,1920]){
   await command('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<768});
   for(const locale of ['en','ar']){
    await navigate(locale);
    await delay(900);
    if(width<1280){
     await evaluate(`document.querySelector('.pm-menu-button').click()`);
     await until(`document.querySelector('.pm-drawer').open`);
     await delay(250);
     const menu=await evaluate(`(()=>{const n=document.querySelector('.pm-drawer'),r=n.getBoundingClientRect();return {left:r.left,right:r.right,focused:n.contains(document.activeElement)};})()`);
     assert.ok(menu.left>=-1&&menu.right<=width+1&&menu.focused,`${width}/${locale}: drawer fits and traps focus`);
     await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
     await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
     await until(`!document.querySelector('.pm-drawer').open&&document.querySelector('.pm-menu-button').getAttribute('aria-expanded')==='false'`);
     assert.equal(await evaluate(`document.querySelector('.pm-menu-button').getAttribute('aria-expanded')`),'false');
    }
    // Existing workflow tabs stay interactive, with no changing illustrative data.
    for(let index=0;index<4;index++){
     await evaluate(`document.querySelector('[data-workflow-tab="${index}"]').click()`);
     assert.equal(await evaluate(`document.querySelector('[data-workflow-tab="${index}"]').getAttribute('aria-selected')`),'true');
    }
    const height=await evaluate('document.documentElement.scrollHeight');
    for(let top=0;top<height;top+=750){await evaluate(`window.scrollTo({top:${top},behavior:'instant'})`);await delay(80);}
    await delay(1000);
    assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),true,`${width}/${locale}: scrolling animations cause no overflow`);
    assert.equal(await evaluate(`document.getAnimations().filter(a=>a.playState==='running').length`),0,'No continuous animations after reveal');
    await evaluate(`document.querySelector('#faq summary').scrollIntoView({block:'center',behavior:'instant'});document.querySelector('#faq summary').focus({preventScroll:true});window.__faqEvents=[];for(const type of ['keydown','keyup','click','toggle'])document.querySelector('#faq details').addEventListener(type,e=>window.__faqEvents.push({type:e.type,key:e.key,open:e.currentTarget.open}));`);
    await delay(100);
    for(const expected of [true,false]){
     await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r',unmodifiedText:'\r'});
     await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
     await delay(250);
     assert.equal(await evaluate(`document.querySelector('#faq details').open`),expected,`${width}/${locale}: keyboard FAQ opens and closes: `+JSON.stringify(await evaluate(`({active:document.activeElement.outerHTML,events:window.__faqEvents})`)));
    }
    assert.equal(await evaluate(`[...document.querySelectorAll('.pm-home a')].every(a=>{const h=a.getAttribute('href');return h&&['/','#','mailto:','tel:','http:','https:'].some(prefix=>h.startsWith(prefix));})`),true,'Existing link destinations remain valid');
    observations.push({motion:true,width,locale,vitals:await evaluate('window.__landingVitals')});
   }
   console.log(`motion + keyboard/menu checks: ${width}px English + Arabic`);
  }
  // Motion listeners survive the existing client-side language switch.
  await navigate('en');
  await evaluate(`document.querySelector('[data-marketing-language]').click()`);
  await until(`document.querySelector('.pm-home').lang==='ar'&&document.documentElement.lang==='ar'`);
  await delay(250);
  await evaluate(`document.querySelector('#faq summary').click()`);await delay(250);
  await evaluate(`document.querySelector('#faq summary').click()`);
  assert.equal(await evaluate(`document.querySelector('#faq details').open`),true,'Arabic FAQ retains its close animation after language switching');
  await delay(250);
  assert.equal(await evaluate(`document.querySelector('#faq details').open`),false);
  // Reduced motion also cancels animations already in progress.
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await delay(100);
  assert.equal(await evaluate(`document.getAnimations().filter(a=>a.playState==='running').length`),0);
  const noObserver=await command('Page.addScriptToEvaluateOnNewDocument',{source:'delete window.IntersectionObserver'});
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await navigate('en');await delay(750);
  assert.equal(await evaluate(`[...document.querySelectorAll('.pm-reveal')].every(n=>getComputedStyle(n).opacity==='1')`),true,'Content is never hidden when IntersectionObserver is unavailable');
  await command('Page.removeScriptToEvaluateOnNewDocument',{identifier:noObserver.identifier});
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await command('Emulation.setScriptExecutionDisabled',{value:true});
  await command('Page.navigate',{url:`${origin}/?lang=en`});
  await delay(1500);
  assert.equal(await evaluate(`!!document.querySelector('.pm-home h1')&&[...document.querySelectorAll('.pm-reveal')].every(n=>getComputedStyle(n).opacity==='1')`),true,'SSR content stays visible without JavaScript');
  await command('Emulation.setScriptExecutionDisabled',{value:false});
 }
 await fs.writeFile(path.join(directory,`${performance?'performance':stage}-results.json`),JSON.stringify({observations,errors,requests},null,2));
 assert.deepEqual(errors,[],'No browser console/hydration errors');
 assert.equal(requests.filter(url=>/\/api(?:\/backend|\/v1)\/(?:auth\/me|organizations)(?:\?|$)/.test(url)).length,0,'No protected-account API calls on page load');
}finally{ws?.close();chrome.kill();}
