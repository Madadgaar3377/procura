import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';

const origin=process.env.MARKETING_TEST_ORIGIN||'http://127.0.0.1:13092';
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
    const id=++sequence;const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Browser command timed out: '+method));},method==='Page.navigate'?60000:20000);
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

  assert.ok(['localhost','127.0.0.1'].includes(new URL(origin).hostname),'Synthetic profile tests require loopback');
  const response=await fetch('http://127.0.0.1:18081/api/v1/directory/suppliers?limit=1');
  assert.ok(response.ok);const [supplier]=await response.json();assert.ok(supplier,'Start the disposable marketing API fixture');
  for(const width of [320,375,430,768,1024,1280,1440,1920]){
    await command('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<768});
    for(const locale of ['en','ar']){
      await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin,path:'/'});
      await navigate('/suppliers/'+supplier.id+'?lang='+locale);
      await until(`!!document.querySelector('.sp-header h1')`);
      assert.equal(await evaluate(`document.querySelector('.sp-header h1').textContent`),supplier.name);
      const geometry=await evaluate(`({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})`);
      assert.ok(geometry.scroll<=geometry.width+1,JSON.stringify({width,locale,...geometry}));
      assert.equal(await evaluate(`document.documentElement.dir`),locale==='ar'?'rtl':'ltr');
      assert.equal(await evaluate(`document.querySelectorAll('h1').length`),1);
      assert.equal(await evaluate(`document.querySelectorAll('.supplier-profile [data-role-cta="buyer"]').length`),2,'One primary RFQ area and one final CTA');
      assert.ok(await evaluate(`!!document.querySelector('#supply-capabilities')&&!!document.querySelector('.sp-related')&&!!document.querySelector('.sp-contact')`));
      assert.equal(await evaluate(`document.querySelectorAll('a[href^="tel:"],a[href^="mailto:"]').length`),0,'Unconsented synthetic fixture contacts stay private');
      assert.ok(!await evaluate(`document.body.innerText.includes('not-public@example.test')`));
      observations.push({width,locale,...geometry});
      if([375,1440].includes(width))await screenshot('profile-'+width+'-'+locale,true);
    }
  }
  await navigate('/suppliers/missing?lang=en');
  await until(`document.body.innerText.includes('Company unavailable')`);
  assert.ok(await evaluate(`!!document.querySelector('.pm-header')&&!!document.querySelector('.pm-footer')`));
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await navigate('/suppliers/'+supplier.id+'?lang=en');
  await until(`!!document.querySelector('.sp-header h1')`);
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.sp-header')).animationName`),'none');
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(directory,'profile-results.json'),JSON.stringify({observations,errors},null,2));
  console.log('Supplier profile responsive/locale checks passed at eight widths, with private-contact and unavailable-profile checks.');
}finally{ws?.close();chrome.kill();}
