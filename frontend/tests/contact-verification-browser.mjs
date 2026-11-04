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
  const user={id:'contact-test',username:'contacttest',email:'contact@example.test',phone:'+966511111119',email_verified:false,phone_verified:false,is_admin:true,is_active:true,notification_language:'en',created_at:'2026-10-28T10:00:00Z'};
  const calls=[];
  intercept=async({requestId,request})=>{try{
    const route=new URL(request.url).pathname.replace(/^.*\/(?:api\/v1|api\/backend)\//,'');calls.push(request.method+' '+route);
    let status=200,data=[];
    if(request.method==='OPTIONS')data={};
    else if(route==='auth/me'){status=authenticated?200:401;data=authenticated?user:{detail:'Not authenticated'};}
    else if(route==='auth/browser/refresh'){status=401;data={detail:'Not authenticated'};}
    else if(route==='auth/browser/login'){
      if(!user.phone_verified){status=403;data={detail:{code:'phone_verification_required',phone:user.phone,email:user.email}};}
      else{authenticated=true;data={authenticated:true};}
    }
    else if(route==='auth/otp/request'||route==='auth/email/verification/request')data={message:'If eligible, a verification code is queued for delivery'};
    else if(route==='auth/otp/verify'||route==='auth/email/verification/confirm'){
      if(JSON.parse(request.postData).code!=='123456'){status=400;data={detail:'Invalid or expired OTP'};}
      else{if(route==='auth/otp/verify')user.phone_verified=true;else user.email_verified=true;data={verified:true};}
    }
    await command('Fetch.fulfillRequest',{requestId,responseCode:status,responseHeaders:[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'},{name:'Access-Control-Allow-Methods',value:'GET, POST, PATCH, DELETE, OPTIONS'},{name:'Access-Control-Allow-Headers',value:'Content-Type, Authorization'}],body:Buffer.from(JSON.stringify(data)).toString('base64')});
  }catch(error){runtimeErrors.push(error.message);await command('Fetch.failRequest',{requestId,errorReason:'Failed'});}};
  await command('Fetch.enable',{patterns:[{urlPattern:'*api/v1/*',requestStage:'Request'},{urlPattern:'*api/backend/*',requestStage:'Request'}]});
  async function enter(selector,text){await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);await command('Input.insertText',{text});}
  async function submit(){await evaluate(`document.querySelector('.live-auth button[type=submit]').click()`);}
  async function login(){await until(`location.pathname==='/login'&&!!document.querySelector('input[name=password]')`);await enter('input[name=identifier]',user.email);await enter('input[name=password]','StrongPassword-123');await submit();}
  async function code(value){await until(`!!document.querySelector('input[name=code]')&&!document.querySelector('.live-auth button[type=submit]').disabled`);await enter('input[name=code]',value);await submit();}
  await navigate('/login');await login();
  await until(`location.pathname==='/verify-mobile'&&document.querySelector('input[name=phone]')?.value===${JSON.stringify(user.phone)}&&!document.querySelector('.live-auth button[type=submit]').disabled`);
  assert.equal(calls.filter(call=>call==='POST auth/otp/request').length,1);
  assert.equal(await evaluate(`document.querySelector('.auth-resend').disabled`),true);
  await code('111111');await until(`document.querySelector('[role=alert]')?.innerText.includes('Invalid or expired OTP')`);
  await evaluate(`document.querySelector('input[name=code]').select()`);await command('Input.insertText',{text:'123456'});await submit();
  await login();await until(`location.pathname==='/verify-email'&&document.body.innerText.includes(${JSON.stringify(user.email)})&&!document.querySelector('.live-auth button[type=submit]').disabled`);
  assert.equal(calls.filter(call=>call==='POST auth/email/verification/request').length,1);
  await navigate('/verify-email');await until(`!document.querySelector('.live-auth button[type=submit]')?.disabled`);
  assert.equal(calls.filter(call=>call==='POST auth/email/verification/request').length,1,'Reload must respect resend cooldown');
  await code('123456');await until(`location.pathname==='/app/admin/dashboard'`);
  await navigate('/verify-email');await until(`location.pathname==='/app/admin/dashboard'`);
  assert.equal(calls.filter(call=>call==='POST auth/email/verification/request').length,1,'Verified account must not request codes');
  user.email_verified=false;
  await evaluate(`sessionStorage.clear()`);await navigate('/app/admin/settings');
  await until(`location.pathname==='/verify-email'&&!document.querySelector('.live-auth button[type=submit]')?.disabled`);
  assert.equal(await evaluate(`!!document.querySelector('.account-settings')`),false);
  await command('Emulation.setDeviceMetricsOverride',{width:320,height:900,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),true);
  await screenshot('contact-verification-email-320');
  // A signed-in account without local pending metadata recovers its destination.
  user.phone_verified=false;await evaluate(`sessionStorage.clear()`);await navigate('/verify-mobile');
  await until(`document.querySelector('input[name=phone]')?.value===${JSON.stringify(user.phone)}&&!document.querySelector('.live-auth button[type=submit]').disabled`);
  const emailRequests=calls.filter(call=>call==='POST auth/email/verification/request').length;
  await code('123456');await until(`location.pathname==='/verify-email'&&!document.querySelector('.live-auth button[type=submit]')?.disabled`);
  assert.equal(calls.filter(call=>call==='POST auth/email/verification/request').length,emailRequests+1,'Each channel must initialize after client navigation');
  await code('123456');await until(`location.pathname==='/app/admin/dashboard'`);
  assert.deepEqual(runtimeErrors,[]);
  console.log('PASS: password-proven unverified login -> mobile code request -> invalid-code feedback -> mobile confirmation -> login -> email request/confirmation -> workspace; direct protected-route gate; verified-user bypass; resend cooldown survives reload; destination recovery without storage; mobile-to-email client navigation; 320px no overflow; no runtime errors. API responses intercepted in browser tests only.');

 }finally{ws?.close();chrome.kill();}
}
const frontend=path.resolve('.');const origin='http://127.0.0.1:14090';
const server=spawn(process.execPath,['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','14090'],{cwd:frontend,env:{...process.env,NEXT_BUILD_DIR:'.next-contact-verification-test',NEXT_TELEMETRY_DISABLED:'1'},windowsHide:true,stdio:['ignore','pipe','pipe']});
let serverOutput='';server.stdout.on('data',d=>serverOutput+=d);server.stderr.on('data',d=>serverOutput+=d);
try{let ready=false;for(let i=0;i<150;i++){try{if((await fetch(origin+'/login',{signal:AbortSignal.timeout(2000)})).ok){ready=true;break;}}catch{}await delay(300);}assert.ok(ready,'Local Next server ready');await browserCheck({origin,frontend});}catch(e){console.error(e.message);console.error(serverOutput.slice(-1500));process.exitCode=1;}finally{server.kill();}
