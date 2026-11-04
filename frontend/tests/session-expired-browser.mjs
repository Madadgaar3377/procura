import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
async function browserCheck({origin,frontend,rfqId,buyerOnly=false}){
 const port=20000+Math.floor(Math.random()*10000);
 const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-sandbox','--disable-dev-shm-usage','--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,`--user-data-dir=${path.join(frontend,'.test-runtime','chrome-profile-'+process.pid)}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
 let ws;
 try{
  let target;
  for(let i=0;i<80;i++){try{const r=await fetch(`http://127.0.0.1:${port}/json/list`,{signal:AbortSignal.timeout(2000)});target=(await r.json()).find(p=>p.type==='page');if(target)break;}catch{}await delay(250);}
  if(!target)throw new Error('Headless Chrome debugging interface unavailable');
  ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Chrome WebSocket connection timed out')),5000);ws.addEventListener('open',()=>{clearTimeout(timeout);resolve();},{once:true});ws.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();let intercept;const runtimeErrors=[];
  ws.addEventListener('message',e=>{const data=JSON.parse(e.data);if(data.method==='Fetch.requestPaused')intercept?.(data.params);if(data.method==='Runtime.exceptionThrown')runtimeErrors.push(data.params.exceptionDetails.text);if(data.id){const call=pending.get(data.id);pending.delete(data.id);if(data.error)call?.reject(new Error(data.error.message));else call?.resolve(data.result);}});
  function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;const timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Chrome command timed out: '+method));},20000);pending.set(id,{resolve:value=>{clearTimeout(timeout);resolve(value);},reject:error=>{clearTimeout(timeout);reject(error);}});ws.send(JSON.stringify({id,method,params}));});}
  async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
  async function until(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await delay(100);}await screenshot('browser-failure');throw new Error('Browser condition timed out: '+expression+' | '+await evaluate('document.body.innerText.slice(-2500)'));}
  async function navigate(route){await command('Page.navigate',{url:origin+route});await until('document.readyState === "complete"');await delay(350);}
  const artifacts=path.join(frontend,'.test-runtime','screenshots');await fs.mkdir(artifacts,{recursive:true});
  async function screenshot(name){const result=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(artifacts,name+'.png'),Buffer.from(result.data,'base64'));}
  await command('Page.enable');await command('Runtime.enable');await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});


  let authenticated=false;
  const calls=[];
  intercept=async({requestId,request})=>{
    const route=new URL(request.url).pathname.replace(/^.*\/(?:api\/v1|api\/backend)\//,'');calls.push(route);
    let status=200;let data=[];
    if(route==='auth/me'){status=authenticated?200:401;data=authenticated?{id:'session-page-test',username:'Session Test',is_admin:true,is_active:true}:{detail:'Not authenticated'};}
    if(route==='auth/browser/refresh'){status=401;data={detail:'Expired'};}
    await command('Fetch.fulfillRequest',{requestId,responseCode:status,responseHeaders:[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'}],body:Buffer.from(JSON.stringify(data)).toString('base64')});
  };
  await command('Fetch.enable',{patterns:[{urlPattern:'*api/v1/*',requestStage:'Request'},{urlPattern:'*api/backend/*',requestStage:'Request'}]});
  const enTitle='Your session has expired';
  const arTitle='\u0627\u0646\u062a\u0647\u062a \u0635\u0644\u0627\u062d\u064a\u0629 \u062c\u0644\u0633\u062a\u0643';
  async function checkCard(){
    await until(`!!document.querySelector('#session-expired-title')`);
    assert.ok(await evaluate(`!!document.querySelector('header')&&!!document.querySelector('footer')`));
    const geometry=await evaluate(`(()=>{const card=document.querySelector('section[aria-labelledby="session-expired-title"]').getBoundingClientRect();const main=document.querySelector('#main-content').getBoundingClientRect();return {horizontal:Math.abs(card.x+card.width/2-(main.x+main.width/2)),vertical:Math.abs(card.y+card.height/2-(main.y+main.height/2)),width:card.width,overflow:document.documentElement.scrollWidth>innerWidth+1,mainHeight:main.height};})()`);
    assert.ok(geometry.horizontal<2&&geometry.vertical<2,JSON.stringify(geometry));assert.ok(geometry.width<=480);assert.equal(geometry.overflow,false);assert.ok(geometry.mainHeight>=480);
    assert.equal(await evaluate(`document.querySelector('#main-content a').getAttribute('href')`),'/login');
    assert.equal(await evaluate(`document.querySelectorAll('#main-content a')[1].getAttribute('href')`),'/');
  }
  for(const locale of ['en','ar']){
    await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin});
    await navigate('/session-expired');
    await evaluate(`localStorage.setItem('procura.locale','${locale}')`);
    await navigate('/session-expired');
    await until(`document.documentElement.dir==='${locale==='ar'?'rtl':'ltr'}'`);
    for(const width of [320,375,430,768,1024,1440]){
      await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});await checkCard();
      assert.equal(await evaluate(`document.querySelector('h1').textContent`),locale==='ar'?arTitle:enTitle);
      if(width===320||width===1440)await screenshot(`session-expired-${locale}-${width}`);
    }
  }
  assert.equal(calls.length,0,'Session-expired page must not refresh or revoke a session');
  await evaluate(`document.querySelector('[data-marketing-language]').click()`);await until(`document.querySelector('h1').textContent===${JSON.stringify(enTitle)}`);
  await evaluate(`document.querySelector('[data-marketing-language]').click()`);await until(`document.querySelector('h1').textContent===${JSON.stringify(arTitle)}`);
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('section[aria-labelledby="session-expired-title"]')).animationName`),'none');
  await evaluate(`document.querySelector('#main-content a').click()`);await until(`location.pathname==='/login'&&!!document.querySelector('input[type=password]')`);
  await navigate('/session-expired?next=https://example.com&error=jwt-expired');await checkCard();
  assert.equal(await evaluate(`document.querySelector('#main-content').innerText.includes('jwt-expired')`),false);
  await evaluate(`document.querySelectorAll('#main-content a')[1].click()`);await until(`location.pathname==='/'`);
  await navigate('/app/admin/settings');await until(`location.pathname==='/session-expired'&&!!document.querySelector('#session-expired-title')`);
  assert.equal(await evaluate(`!!document.querySelector('.account-settings')`),false);
  authenticated=true;
  await evaluate(`document.querySelector('#main-content a').click()`);await until(`location.pathname==='/app/admin/dashboard'`);
  assert.deepEqual(runtimeErrors,[]);
  console.log('PASS: 12 responsive EN/AR layouts, centered card, header/footer, both navigation links, live language switching, reduced motion, no session API calls on page load, hostile return/error query ignored, expired protected-route guard, authenticated login recovery, no runtime errors. Auth responses intercepted in tests only.');

 }finally{ws?.close();chrome.kill();}
}
const frontend=path.resolve('.');const origin='http://127.0.0.1:14089';
const server=spawn(process.execPath,['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','14089'],{cwd:frontend,env:{...process.env,NEXT_BUILD_DIR:'.next-session-expired-test',NEXT_TELEMETRY_DISABLED:'1'},windowsHide:true,stdio:['ignore','pipe','pipe']});
let serverOutput='';server.stdout.on('data',d=>serverOutput+=d);server.stderr.on('data',d=>serverOutput+=d);
try{let ready=false;for(let i=0;i<150;i++){try{if((await fetch(origin+'/login',{signal:AbortSignal.timeout(2000)})).ok){ready=true;break;}}catch{}await delay(300);}assert.ok(ready,'Local Next server ready');await browserCheck({origin,frontend});}catch(e){console.error(e.message);console.error(serverOutput.slice(-1500));process.exitCode=1;}finally{server.kill();}
