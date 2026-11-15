// Local production browser checks with intercepted account API responses; never contacts real account services.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';

const origin=process.env.LOGIN_TEST_ORIGIN||'http://127.0.0.1:13110';
assert.ok(['localhost','127.0.0.1'].includes(new URL(origin).hostname));
const directory=path.resolve('.test-runtime/login');
await fs.mkdir(directory,{recursive:true});
const port=24000+Math.floor(Math.random()*3000);
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
  '--headless=new','--disable-extensions','--disable-background-networking',
  '--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,
  `--user-data-dir=${path.join(directory,`chrome-${process.pid}`)}`,'about:blank',
],{windowsHide:true,stdio:'ignore'});
let ws;
const errors=[],requests=[],observations=[]; let scenario="anonymous", signedIn=false;
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

  const user={id:'test-user',phone:'+966500000001',username:'test-user',phone_verified:true,email:'user@example.test',email_verified:true,is_admin:false};
  const org={id:'existing-org',name:'Existing organization',is_active:true,is_buyer:true,is_supplier:false};
  ws.addEventListener('message',event=>{
    const message=JSON.parse(event.data);if(message.method!=='Fetch.requestPaused')return;
    const {requestId,request}=message.params;
    if(request.url.includes('/app/')){if(scenario==='redirect-slow')return;void command('Fetch.continueRequest',{requestId}).catch(()=>{});return;}
    const url=new URL(request.url), path=url.pathname.replace(/^.*\/api\/v1\//,'');
    let status=200,body=[];
    if(path==='auth/me'){
      if(scenario==='slow')return; // A pending session check must never hide the form.
      if(scenario==='unavailable'){status=503;body={detail:'private server detail'};}
      else if(!signedIn&&['anonymous','failure','expired','login','org-error','redirect-slow'].includes(scenario)){status=401;body={detail:'Not authenticated'};}
      else body={...user,is_admin:scenario==='admin'};
    }else if(path==='auth/browser/refresh'){
      if(scenario==='expired'){signedIn=true;body={authenticated:true};}else{status=401;body={detail:'Expired'};}
    }else if(path==='auth/browser/logout'){signedIn=false;scenario='anonymous';body={};
    }else if(path==='auth/browser/login'){
      if(scenario==='failure'){status=401;body={detail:'Invalid credentials'};}else{signedIn=true;body={authenticated:true};}
    }else if(path==='organizations'){
      if(scenario==='org-error'){status=500;body={detail:'private database exception'};}
      else body=['no-org','claim'].includes(scenario)?[]:[{...org,is_buyer:scenario!=='supplier',is_supplier:scenario==='supplier'}];
    }else if(path==='organizations/claims/mine')body=scenario==='claim'?[{id:'claim',org_id:'existing-org',claimant_id:user.id,status:'pending'}]:[];
    const headers=[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'},{name:'Access-Control-Allow-Headers',value:'content-type'}];
    void command('Fetch.fulfillRequest',{requestId,responseCode:request.method==='OPTIONS'?204:status,responseHeaders:headers,body:Buffer.from(JSON.stringify(body)).toString('base64')}).catch(error=>{if(!error.message.includes('Invalid InterceptionId'))throw error;});
  });
  await command('Runtime.enable');await command('Network.enable');await command('Page.enable');
  await command('Fetch.enable',{patterns:[{urlPattern:'*/api/v1/*'},{urlPattern:'*/app/*'}]});
  async function navigate(locale='en'){
    const previous=requests.filter(x=>x.endsWith('/auth/me')).length;
    await command('Network.setCookie',{name:'procura.locale',value:locale,url:origin,path:'/'});
    await evaluate(`try{localStorage.setItem('procura.locale',${JSON.stringify(locale)})}catch{} window.__oldLogin=true`);
    await command('Page.navigate',{url:`${origin}/login?lang=${locale}`});
    await until(`!window.__oldLogin&&!!document.querySelector('input[name=password]')`);
    for(let i=0;i<100&&requests.filter(x=>x.endsWith('/auth/me')).length===previous;i++)await delay(50);
  }
  for(const width of [320,375,768,1024,1440,1920])for(const locale of ['en','ar']){
    scenario='slow';signedIn=false;
    await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});
    await navigate(locale);await delay(250);
    const layout=await evaluate(`(()=>{const f=document.querySelector('.live-auth'),b=f.getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth,formWidth:b.width,background:getComputedStyle(f).backgroundColor,dir:document.documentElement.dir};})()`);
    assert.equal(layout.overflow,false,`${width} ${locale}: no overflow`);assert.ok(layout.formWidth>240);assert.equal(layout.background,'rgb(255, 255, 255)');assert.equal(layout.dir,locale==='ar'?'rtl':'ltr');
    const shot=await command('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(directory,`${width}-${locale}.png`),Buffer.from(shot.data,'base64'));
    const workspaceRequests=requests.filter(x=>/\/api\/v1\/(organizations|dashboard|rfqs)/.test(x));assert.equal(workspaceRequests.length,0,'Pending auth never requests workspaces');
    observations.push({width,locale,layout});console.log(`${width}px ${locale}: form visible while authentication is pending`);
  }
  scenario='anonymous';await navigate();await until(`!document.querySelector('.auth-session-status')`);await command('Emulation.setFocusEmulationEnabled',{enabled:true});assert.ok(await evaluate(`!!document.querySelector('input[name=password]')`));
  await evaluate(`document.querySelector('input[name=password]').focus()`);console.log('Focus',await evaluate(`({tag:document.activeElement.tagName,shadow:getComputedStyle(document.activeElement).boxShadow,outline:getComputedStyle(document.activeElement).outlineStyle})`));assert.ok(await evaluate(`getComputedStyle(document.activeElement).boxShadow!=='none'||getComputedStyle(document.activeElement).outlineStyle!=='none'`),'Visible keyboard focus');
  for(const mode of ['failure','org-error','login']){
    scenario=mode;signedIn=false;await navigate();await until(`!document.querySelector('.auth-session-status')`);console.log('Submitting',mode);
    await evaluate(`document.querySelector('[name=identifier]').value='user@example.test';document.querySelector('[name=password]').value='password';document.querySelector('form.live-auth').requestSubmit()`);
    if(mode==='login'){await until(`location.pathname==='/app/buyer/dashboard'`);}
    else{await until(`!!document.querySelector('.notice[role=alert]')`);assert.ok(await evaluate(`!!document.querySelector('[name=password]')&&!document.querySelector('button[type=submit]').disabled`),'Failure releases submit state');assert.ok(!(await evaluate('document.body.innerText')).includes('private database exception'));}
  }
  for(const [mode,destination] of [['supplier','/app/supplier/dashboard'],['admin','/app/admin/dashboard'],['no-org','/organizations'],['claim','/app/supplier/company'],['expired','/app/buyer/dashboard']]){
    scenario=mode;signedIn=false;
    await command('Page.navigate',{url:origin+'/login'});await until(`location.pathname===${JSON.stringify(destination)}`);
    if(mode==='claim'){await until(`document.body.innerText.includes('Ownership verification pending')`);assert.equal(await evaluate(`!!document.querySelector('form')`),false,'Pending claims cannot create duplicate companies');}
    console.log(`${mode}: ${destination}`);
  }

  scenario='login';signedIn=true;await command('Page.navigate',{url:origin+'/app/buyer/dashboard'});
  await until(`!!document.querySelector('.user-menu button')`);
  await evaluate(`document.querySelector('.user-menu button').click()`);
  await until(`location.pathname==='/login'&&!!document.querySelector('[name=password]')`);
  assert.ok(requests.some(url=>url.endsWith('/auth/browser/logout')),'Logout uses the browser session endpoint');
  scenario='redirect-slow';signedIn=false;await navigate('en');await until(`!document.querySelector('.auth-session-status')`);
  await evaluate(`document.querySelector('[name=identifier]').value='user@example.test';document.querySelector('[name=password]').value='password';document.querySelector('form.live-auth').requestSubmit()`);
  await until(`!!document.querySelector('[aria-busy=true]')`);
  for(const width of [320,375,768,1024,1440,1920]){
    await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});
    assert.ok(await evaluate(`document.documentElement.scrollWidth<=innerWidth&&document.querySelector('main').getBoundingClientRect().height>=innerHeight`),'Loading screen fills viewport without overflow');
  }
  const loading=await command('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(directory,'loading-en.png'),Buffer.from(loading.data,'base64'));
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('[class*=spinner]')).animationName`),'none','Loading respects reduced motion');
  await command('Emulation.setEmulatedMedia',{features:[]});
  scenario='anonymous';signedIn=false;
  const protectedStart=requests.length;await command('Page.navigate',{url:origin+'/app/buyer/dashboard'});
  await until(`location.pathname==='/session-expired'`);
  assert.equal(requests.slice(protectedStart).filter(url=>/\/api\/v1\/organizations$/.test(url)).length,0,'Logged-out protected route never requests organizations');
  scenario='unavailable';await navigate();await until(`!!document.querySelector('.notice[role=alert]')`);assert.ok(await evaluate(`!!document.querySelector('[name=password]')`));
  scenario='anonymous';await evaluate(`document.querySelector('button.auth-resend').click()`);await until(`!document.querySelector('.notice[role=alert]')`);
  await command('Emulation.setScriptExecutionDisabled',{value:true});await command('Page.navigate',{url:origin+'/login'});await delay(1200);
  assert.ok(await evaluate(`!!document.querySelector('[name=password]')&&getComputedStyle(document.querySelector('.live-auth')).backgroundColor==='rgb(255, 255, 255)'`),'Styled login form is server rendered without JavaScript');
  await command('Emulation.setScriptExecutionDisabled',{value:false});
  assert.equal(errors.filter(x=>/hydration|did not match|server rendered/i.test(x)).length,0,'No hydration errors');
  console.log('Login failure, workspace failure, retry, role redirects, expired session and SSR passed');
}finally{
 await fs.writeFile(path.join(directory,'results.json'),JSON.stringify({observations,errors,requests},null,2));ws?.close();chrome.kill();
}
