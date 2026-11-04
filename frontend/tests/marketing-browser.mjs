import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';

const origin=process.env.MARKETING_TEST_ORIGIN||'http://127.0.0.1:13091';
const before=process.argv.includes('--before');
const fixtures=process.env.MARKETING_API_FIXTURES==='true';
const quick=process.argv.includes('--quick');
const directory=path.resolve(process.env.MARKETING_ARTIFACTS_DIR||'.test-runtime/marketing');
const capturePath=process.env.MARKETING_CAPTURE_PATH||'/';
await fs.mkdir(directory,{recursive:true});
const port=23000+Math.floor(Math.random()*5000);
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
  '--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-sandbox','--disable-dev-shm-usage',
  '--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,
  `--user-data-dir=${path.join(directory,'chrome-'+process.pid)}`,'about:blank'
],{windowsHide:true,stdio:'ignore'});
let ws;
try{
  let target;
  for(let i=0;i<80;i++){
    try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(item=>item.type==='page');if(target)break;}catch{}
    await delay(200);
  }
  assert.ok(target,'Headless browser starts');
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();const errors=[];const requests=[];
  ws.addEventListener('message',event=>{
    const data=JSON.parse(event.data);
    if(data.method==='Network.requestWillBeSent')requests.push(data.params.request.url);
    if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails.exception?.description||data.params.exceptionDetails.text);
    if(data.method==='Runtime.consoleAPICalled'&&data.params.type==='error'){
      const message=data.params.args.map(arg=>arg.value||arg.description||'').join(' ');
      if(/hydrat|server rendered|didn.t match/i.test(message))errors.push(message);
    }
    if(data.id){const call=pending.get(data.id);pending.delete(data.id);if(data.error)call?.reject(new Error(data.error.message));else call?.resolve(data.result);}
  });
  function command(method,params={}){return new Promise((resolve,reject)=>{
    const id=++sequence;const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Browser command timed out: '+method));},20000);
    pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
    ws.send(JSON.stringify({id,method,params}));
  });}
  async function evaluate(expression){const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;}
  async function until(expression){for(let i=0;i<120;i++){if(await evaluate(expression))return;await delay(100);}await screenshot('failure');throw new Error('Condition timed out: '+expression+' | '+JSON.stringify(await evaluate(`({url:location.href,lang:document.documentElement.lang,h1:document.querySelector('h1')?.textContent,language:document.querySelector('[data-marketing-language]')?.textContent})`)));}
  async function navigate(route){
    await evaluate('window.__marketingPreviousDocument=true');
    await command('Page.navigate',{url:origin+route});
    await until('!window.__marketingPreviousDocument&&document.readyState==="complete"&&!!document.querySelector("h1")');
    if(!before)await until(`document.querySelector('.pm-header')?.dataset.interactive==='ready'`);
  }
  async function screenshot(name,full=false){
    const metrics=await command('Page.getLayoutMetrics');
    const result=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:full,...(full?{clip:{x:0,y:0,width:metrics.cssContentSize.width,height:metrics.cssContentSize.height,scale:1}}:{})});
    await fs.writeFile(path.join(directory,name+'.png'),Buffer.from(result.data,'base64'));
  }
  await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
  await command('Page.addScriptToEvaluateOnNewDocument',{source:`window.__marketingVitals={cls:0,lcp:0};try{new PerformanceObserver(list=>{for(const entry of list.getEntries())if(!entry.hadRecentInput)window.__marketingVitals.cls+=entry.value;}).observe({type:'layout-shift',buffered:true});new PerformanceObserver(list=>{for(const entry of list.getEntries())window.__marketingVitals.lcp=entry.startTime;}).observe({type:'largest-contentful-paint',buffered:true});}catch{}`});
  const observations=[];
  for(const width of before?[1440,375]:quick?[1440]:[320,375,430,768,1024,1280,1440,1920]){
    await command('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<768});
    for(const locale of before?['en']:['en','ar']){
      await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin,path:'/'});
      await navigate((before?capturePath:'/')+'?lang='+locale);await delay(500);
      if(!before){
        await until('!!document.querySelector(".pm-header")');
        assert.equal(await evaluate('document.documentElement.lang'),locale);
        const geometry=await evaluate(`({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,heading:document.querySelector('h1').getBoundingClientRect().toJSON(),h1s:document.querySelectorAll('h1').length})`);
        assert.ok(geometry.scroll<=geometry.width+1,'No horizontal overflow: '+JSON.stringify({width,locale,...geometry}));
        assert.equal(geometry.h1s,1,'One primary heading');
        assert.ok(await evaluate(`document.body.innerText.includes('Procurement Without Complexity.')`),'Official tagline present');
        assert.ok(await evaluate(`!!document.querySelector('#buyers')&&!!document.querySelector('#suppliers')&&!!document.querySelector('#comparison')&&!!document.querySelector('#faq')`),'Required journeys present');
        await evaluate(`document.querySelectorAll('.pm-reveal').forEach(node=>node.classList.add('is-visible'))`);
        if(width<1280){
          await evaluate(`document.querySelector('.pm-menu-button').click()`);await until(`!!document.querySelector('.pm-drawer[open]')`);await delay(260);
          const drawer=await evaluate(`(()=>{const r=document.querySelector('.pm-drawer').getBoundingClientRect();return {left:r.left,right:r.right,width:innerWidth};})()`);
          assert.ok(drawer.left>=-1&&drawer.right<=drawer.width+1,'Mobile menu fits: '+JSON.stringify(drawer));
          await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await until(`!document.querySelector('.pm-drawer[open]')`);
        }
        if(fixtures){await until(`!!document.querySelector('[data-public-state="loaded"]')&&!!document.querySelector('[data-membership-state="loaded"]')`);}
        observations.push({width,locale,...geometry,vitals:await evaluate('window.__marketingVitals')});
      }
      if(before||[375,768,1440].includes(width))await screenshot(`${before?'before':'after'}-${width}-${locale}`,width===1440);
      if(!before&&width===1440)await screenshot(`after-${width}-${locale}-hero`);
      if(!before&&width===1440){
        await evaluate(`document.querySelector('[data-workflow-tab="3"]').click()`);
        assert.equal(await evaluate(`document.querySelector('[data-workflow-tab="3"]').getAttribute('aria-selected')`),'true');
        await evaluate(`document.querySelector('#faq summary').click()`);
        assert.ok(await evaluate(`document.querySelector('#faq details').open`),'FAQ opens');
        if(locale==='en')for(const section of ['buyers','suppliers','matching','comparison','directory','memberships']){
          await evaluate(`document.querySelector('#${section}').scrollIntoView({behavior:'instant'})`);await delay(280);
          await screenshot('section-'+section);
        }
      }
    }
    console.log((before?'Before capture':'Marketing checks')+': '+width+'px');
  }
  if(!before){
    assert.equal(requests.filter(url=>/\/api(?:\/backend|\/v1)\/(?:auth\/me|organizations)(?:\?|$)/.test(url)).length,0,'No protected account requests while browsing marketing');
    for(const width of quick?[]:[320,1440]){
      await command('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<768});
      for(const locale of ['en','ar'])for(const route of ['/how-it-works','/industries','/faq','/about','/privacy','/terms','/suppliers','/pricing','/contact']){
        await navigate(route+'?lang='+locale);await delay(180);
        assert.equal(await evaluate('document.documentElement.lang'),locale);
        const geometry=await evaluate(`({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})`);
        assert.ok(geometry.scroll<=geometry.width+1,`${route} ${locale} ${width}: no page overflow`);
        assert.equal(await evaluate(`document.querySelectorAll('h1').length`),1);
        if(route==='/industries')assert.equal(await evaluate(`document.querySelectorAll('.pm-sector').length`),3,'Only the three supported disciplines');
        observations.push({route,width,locale,...geometry});
      }
      console.log('Public route checks: '+width+'px, English and Arabic');
    }
    await navigate('/?lang=en');await delay(200);
    await evaluate(`document.querySelector('.pm-nav-actions [data-marketing-language]').click()`);
    await until(`document.documentElement.lang==='ar'&&document.querySelector('.pm-hero h1').textContent.includes('المشتريات')&&location.search.includes('lang=ar')`);
    await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    await navigate('/?lang=en');
    assert.equal(await evaluate(`getComputedStyle(document.querySelector('.pm-hero h1')).animationName`),'none','Reduced-motion preference is respected');
    await command('Emulation.setEmulatedMedia',{features:[]});
    if(fixtures){
      for(const state of ['empty','error']){
        assert.ok((await fetch('http://127.0.0.1:18081/__marketing_test/state/'+state,{method:'POST'})).ok);
        await navigate('/?lang=en');await until(`!!document.querySelector('[data-public-state="${state}"]')&&!!document.querySelector('[data-membership-state="${state}"]')`);
        assert.ok(await evaluate(`!!document.querySelector('.pm-hero h1')`),'Public APIs never block the hero');
        assert.equal(await evaluate(`document.querySelectorAll('.pm-directory-card').length`),0,'No fake company fallback');
        await screenshot('state-'+state);
      }
      await fetch('http://127.0.0.1:18081/__marketing_test/state/loaded',{method:'POST'});
      for(const [role,phone,target] of [['buyer','+966500008801','/app/buyer/rfqs/new'],['supplier','+966500008802','/app/supplier/company'],['admin','+966500008803','/app/admin/dashboard']]){
        await navigate('/?lang=en');
        await evaluate(`fetch('/api/backend/auth/login',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:'${phone}',password:'MarketingTestPassword-123'})}).then(async response=>{if(!response.ok)throw Error(await response.text());})`);
        await evaluate(`document.querySelector('.pm-hero-actions [data-role-cta="${role==='supplier'?'supplier':'buyer'}"]').click()`);
        await until(`location.pathname==='${target}'`);
        await evaluate(`fetch('/api/backend/auth/logout',{method:'POST',credentials:'include'})`);
      }
      await navigate('/?lang=en');
      await evaluate(`document.querySelector('.pm-hero-actions [data-role-cta="supplier"]').click()`);
      await until(`location.pathname==='/register'&&location.search.includes('role=supplier')`);
      console.log('Real API directory/plan states and anonymous/buyer/supplier/admin CTA routing passed.');
    }
    const response=await fetch(origin+'/?lang=ar');const html=await response.text();
    assert.match(html,/<html[^>]*lang="ar"/);assert.match(html,/<html[^>]*dir="rtl"/);
    assert.ok(html.includes('Procurement Without Complexity.')&&html.includes('application/ld+json'),'Marketing content and structured data are server-rendered');
    assert.ok(!html.includes('procura.example')&&!html.includes('not-public@example.test'),'No invented domain or private supplier details');
    assert.ok(!html.includes('FAQPage'),'No unjustified FAQ rich-result markup');
    assert.match(html,/<link[^>]*rel="canonical"/);assert.match(html,/<link[^>]*hrefLang="ar"/i);
    assert.match(html,/<meta[^>]*property="og:title"/);assert.match(html,/<meta[^>]*name="twitter:card"/);
  }
  assert.deepEqual(errors,[],'No browser runtime exceptions');
  await fs.writeFile(path.join(directory,before?'before.json':'results.json'),JSON.stringify({observations,errors},null,2));
  console.log(before?'Current homepage captured.':'Marketing responsive, locale, menu, preview and FAQ checks passed.');
}finally{ws?.close();chrome.kill();}
