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

  const user={id:'settings-ui',username:'settings_demo',email:'settings@example.test',phone:'+966501234571',email_verified:false,phone_verified:false,is_admin:true,is_active:true,notification_language:'en',email_notifications_enabled:false,sms_notifications_enabled:false,created_at:'2026-10-28T10:00:00Z'};
  const preferences={events:{rfq_invitation:true,deadline_reminder:true,quotation_submitted:true,quotation_status_changed:true,supplier_matching_updated:true},in_app_enabled:true,email_available:true,sms_available:true};
  let sessionRows=Array.from({length:21},(_,i)=>({id:'session-'+i,created_at:'2026-10-28T10:00:00Z',expires_at:'2026-11-28T10:00:00Z',current:i===20}));
  const calls=[];
  intercept=async({requestId,request})=>{try{
    const route=new URL(request.url).pathname.replace(/^.*\/(?:api\/v1|api\/backend)\//,'');calls.push(request.method+' '+route);
    let data={};let status=200;
    if(request.method==='OPTIONS')data={};
    else if(route==='auth/me')data=user;
    else if(route==='organizations')data=[{id:'org-ui',name:'Settings test company',is_active:true,is_buyer:true,is_supplier:true,verified_status:'verified'}];
    else if(route==='auth/sessions')data=sessionRows;
    else if(route.startsWith('auth/sessions/')&&request.method==='DELETE'){sessionRows=sessionRows.filter(row=>row.id!==route.split('/').pop());data={ok:true};}
    else if(route==='auth/account-preferences'){
      if(request.method==='PATCH'){const value=JSON.parse(request.postData);Object.assign(preferences.events,value.events||{});if(value.in_app_enabled!==undefined)preferences.in_app_enabled=value.in_app_enabled;}
      data=preferences;
    }
    else if(route==='auth/email/verification/request'){status=202;data={message:'Verification requested'};}
    else if(route==='auth/email/verification/confirm'){assert.deepEqual(JSON.parse(request.postData),{code:'123456'});user.email_verified=true;data={verified:true};}
    else if(route==='auth/notification-preferences'){const v=JSON.parse(request.postData);if(v.email_enabled!==undefined)user.email_notifications_enabled=v.email_enabled;if(v.sms_enabled!==undefined)user.sms_notifications_enabled=v.sms_enabled;data=v;}
    else if(route==='admin/settings')data={mock_notifications:true};
    else if(route==='notifications')data=[];
    else if(route==='auth/sessions/revoke-others'){sessionRows=sessionRows.filter(row=>row.current);data={ok:true};}
    else if(route==='auth/otp/request'){status=202;data={ok:true};}
    else if(route==='auth/otp/verify'){assert.deepEqual(JSON.parse(request.postData),{phone:user.phone,code:'123456'});user.phone_verified=true;data={verified:true};}
    else if(route==='auth/browser/logout')data={ok:true};
    else if(route==='auth/password/change')data={password_changed:true,sign_in_required:true};
    else data=[];
    await command('Fetch.fulfillRequest',{requestId,responseCode:status,responseHeaders:[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'},{name:'Access-Control-Allow-Methods',value:'GET, POST, PATCH, DELETE, OPTIONS'},{name:'Access-Control-Allow-Headers',value:'Content-Type, Authorization'}],body:Buffer.from(JSON.stringify(data)).toString('base64')});
  }catch(e){runtimeErrors.push(e.message);await command('Fetch.failRequest',{requestId,errorReason:'Failed'});}};
  await command('Fetch.enable',{patterns:[{urlPattern:'*api/v1/*',requestStage:'Request'},{urlPattern:'*api/backend/*',requestStage:'Request'}]});
  await navigate('/app/supplier/settings');
  await until(`!!document.querySelector('#settings-account')&&document.body.innerText.includes('Verify Email')`);
  async function tab(id){await evaluate(`document.getElementById('tab-${id}').click()`);await until(`!!document.querySelector('#settings-${id}')`);assert.equal(await evaluate(`document.querySelectorAll('[role=tabpanel]').length`),1);}
  async function click(text){await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent===${JSON.stringify(text)}).click()`);}
  async function enter(selector,text){await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);await command('Input.insertText',{text});}
  await screenshot('settings-desktop-unverified');
  for(const contact of ['Email','Phone']){
    await click('Verify '+contact);await until(`!!document.querySelector('input[autocomplete="one-time-code"]')`);
    await enter('input[autocomplete="one-time-code"]','123456');await click('Verify code');
    await until(`!document.querySelector('[role=dialog]')`);
  }
  assert.ok(calls.includes('POST auth/email/verification/confirm'));assert.ok(calls.includes('POST auth/otp/verify'));
  await tab('sessions');await until(`document.querySelectorAll('.settings-session-table tbody tr').length===5`);
  assert.ok(await evaluate(`document.querySelector('.settings-session-table tbody tr').innerText.includes('Current Session')`));
  await click('Next');await until(`document.querySelector('.settings-pagination').innerText.includes('Page 2 of 5')`);
  await evaluate(`document.querySelector('.settings-session-table tbody button').click()`);await until(`!!document.querySelector('[role=dialog]')`);
  await click('Cancel');assert.equal(calls.filter(c=>c.startsWith('DELETE')).length,0);
  await evaluate(`document.querySelector('.settings-session-table tbody button').click()`);await click('Confirm sign out');
  await until(`document.querySelector('.settings-pagination').innerText.includes('of 4')`);
  assert.equal(sessionRows.length,20);
  await tab('preferences');await until(`document.querySelectorAll('[role=switch]').length===8`);
  await evaluate(`document.querySelector('[role=switch]').click()`);await until(`document.querySelector('[role=switch]').checked&&!document.querySelector('[role=switch]').disabled`);
  await evaluate(`document.querySelectorAll('[role=switch]')[3].click()`);await until(`!document.querySelectorAll('[role=switch]')[3].checked&&!document.querySelectorAll('[role=switch]')[3].disabled`);
  await navigate('/app/buyer/settings');await until(`!!document.querySelector('#settings-account')`);await tab('preferences');
  await until(`document.querySelector('[role=switch]')?.checked===true`);assert.equal(await evaluate(`document.querySelectorAll('[role=switch]')[3].checked`),false);
  for(const locale of ['en','ar']){
    await evaluate(`localStorage.setItem('procura.locale','${locale}');document.cookie='procura.locale=${locale}; Path=/';`);
    await navigate('/app/supplier/settings');await until(`!!document.querySelector('#settings-account')&&document.documentElement.dir==='${locale==='ar'?'rtl':'ltr'}'`);
    for(const width of [320,375,430,768,1024,1280,1440]){
      await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});
      for(const id of ['account','password','sessions','preferences']){
        await tab(id);assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),true,`${locale} ${width} ${id} overflow`);
        assert.equal(await evaluate(`document.querySelector('[role=tabpanel]').innerText.includes('???')`),false);
      }
      await screenshot(`settings-${locale}-${width}`);
    }
  }
  await evaluate(`localStorage.setItem('procura.locale','en');document.cookie='procura.locale=en; Path=/';`);
  await navigate('/app/admin/settings');await until(`!!document.querySelector('#settings-account')`);
  await tab('sessions');await click('Sign Out Other Devices');await click('Confirm sign out');await until(`document.querySelectorAll('.settings-session-table tbody tr').length===1`);
  await tab('password');await enter('#current_password','CurrentPass1!');await enter('#new_password','NewPassword123!');await enter('#confirm_password','different');
  assert.equal(await evaluate(`document.querySelector('.settings-password-form button[type=submit],.settings-form-actions>button').disabled`),true);
  await evaluate(`document.querySelector('#confirm_password').select()`);await command('Input.insertText',{text:'NewPassword123!'});
  await evaluate(`document.querySelector('.settings-password-form .settings-form-actions>button').click()`);await until(`location.pathname==='/login'&&location.search.includes('passwordChanged=1')`);
  assert.ok(calls.includes('POST auth/password/change'));
  await navigate('/app/supplier/settings');await until(`!!document.querySelector('#settings-account')`);await tab('sessions');
  await click('Sign Out Current Device');await click('Confirm sign out');await until(`location.pathname==='/login'`);assert.ok(calls.includes('POST auth/browser/logout'));
  assert.deepEqual(runtimeErrors,[]);
  console.log('PASS: four tabs; email/phone verification; matching passwords and password endpoint; five-session pagination, revoke cancellation/confirmation, other/current sign-out; notification persistence; supplier/buyer/admin; seven viewport sizes in EN and AR; no overflow or runtime errors. API responses intercepted in browser tests only; live providers not tested.');

 }finally{ws?.close();chrome.kill();}
}
const frontend=path.resolve('.');const origin='http://127.0.0.1:14088';
const server=spawn(process.execPath,['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','14088'],{cwd:frontend,env:{...process.env,NEXT_BUILD_DIR:'.next-account-settings-test',NEXT_TELEMETRY_DISABLED:'1'},windowsHide:true,stdio:['ignore','pipe','pipe']});
let serverOutput='';server.stdout.on('data',d=>serverOutput+=d);server.stderr.on('data',d=>serverOutput+=d);
try{let ready=false;for(let i=0;i<150;i++){try{if((await fetch(origin+'/login',{signal:AbortSignal.timeout(2000)})).ok){ready=true;break;}}catch{}await delay(300);}assert.ok(ready,'Local Next server ready');await browserCheck({origin,frontend});}catch(e){console.error(e.message);console.error(serverOutput.slice(-1500));process.exitCode=1;}finally{server.kill();}
