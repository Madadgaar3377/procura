import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const port=13184,origin=`http://127.0.0.1:${port}`,debug=23184;
const web=spawn(process.execPath,['node_modules/next/dist/bin/next','start','-H','127.0.0.1','-p',String(port)],{cwd:root,env:{...process.env,NEXT_BUILD_DIR:process.env.NEXT_BUILD_DIR||'.next-recovery-validation'},windowsHide:true,stdio:'ignore'});
let chrome,ws;const pending=new Map();let seq=0;const counts={forgot:0,verify:0,reset:0};let expire=false;const errors=[];
try{
 for(let i=0;i<120;i++){try{if((await fetch(origin+'/forgot-password')).ok)break;}catch{}if(i===119)throw Error('Frontend did not start');await delay(250);}
 const profile=path.join(root,'.test-runtime','recovery-browser-'+process.pid);await fs.mkdir(profile,{recursive:true});
 chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-sandbox','--disable-dev-shm-usage','--disable-extensions','--disable-background-networking','--no-first-run','--no-default-browser-check',`--remote-debugging-port=${debug}`,`--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
 let target;
 for(let i=0;i<80;i++){try{target=(await (await fetch(`http://127.0.0.1:${debug}/json/list`)).json()).find(p=>p.type==='page');if(target)break;}catch{}await delay(250);}
 if(!target)throw Error('Headless browser unavailable');
 ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 function command(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>{pending.delete(id);reject(Error('Browser command timed out: '+method));},30000);pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id,method,params}));});}
 ws.addEventListener('message',async e=>{const m=JSON.parse(e.data);if(m.id){const call=pending.get(m.id);pending.delete(m.id);if(m.error)call?.reject(Error(m.error.message));else call?.resolve(m.result);}
  if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);
  if(m.method==='Fetch.requestPaused'){
   const {requestId,request}=m.params;
   if(!request.url.includes('/api/')){await command('Fetch.continueRequest',{requestId});return;}
   if(request.method==='OPTIONS'){await command('Fetch.fulfillRequest',{requestId,responseCode:204,responseHeaders:[{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'},{name:'Access-Control-Allow-Methods',value:'GET,POST,OPTIONS'},{name:'Access-Control-Allow-Headers',value:'Content-Type'}]});return;}
   const payload=request.postData?JSON.parse(request.postData):{};let status=401,data={detail:'Authentication required'};
   if(request.url.includes('/password/forgot')){counts.forgot++;status=202;data={message:'If an eligible account exists, a password recovery code will be sent'};await delay(150);}
   if(request.url.includes('/password/verify')){counts.verify++;status=payload.code==='123456'?200:400;data=status===200?{verified:true,reset_token:'verified-test-session'}:{detail:'Invalid or expired recovery code'};}
   if(request.url.includes('/password/reset')){counts.reset++;status=expire?400:200;data=expire?{detail:'Invalid or expired password reset session'}:{password_reset:true};}
   await command('Fetch.fulfillRequest',{requestId,responseCode:status,responseHeaders:[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'}],body:Buffer.from(JSON.stringify(data)).toString('base64')});
  }
 });
 async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('Browser evaluation failed');return r.result.value;}
 async function until(expression){for(let i=0;i<120;i++){if(await evaluate(expression))return;await delay(100);}throw Error('Browser assertion timed out: '+expression+' counts='+JSON.stringify(counts));}
 async function navigate(route){await command('Page.navigate',{url:origin+route});await until('document.readyState==="complete"');await delay(500);}
 async function fill(name,value){await evaluate(`(()=>{const e=document.querySelector('input[name=${name}]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
 const submit=()=>evaluate(`document.querySelector('form').requestSubmit()`);
 console.log('Headless browser connected');await command('Page.enable');await command('Runtime.enable');await command('Fetch.enable',{patterns:[{urlPattern:'*'}]});
 await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await navigate('/forgot-password');await until(`!!document.querySelector('input[name=identifier]')`);
 await fill('identifier','approved@example.test');await evaluate(`document.querySelector('form').requestSubmit();document.querySelector('form').requestSubmit()`);
 await until(`location.pathname==='/reset-password'&&!!document.querySelector('input[name=code]')`);assert.equal(counts.forgot,1);
 assert.equal(await evaluate(`document.querySelector('.auth-resend').disabled`),true);
 await fill('code','000000');await submit();await until(`document.body.innerText.includes('Invalid or expired recovery code')`);
 await fill('code','123456');await submit();await until(`!!document.querySelector('input[name=confirm_password]')`);
 await fill('password','NewPassword-123');await fill('confirm_password','DifferentPassword-123');await submit();await delay(250);assert.equal(counts.reset,0);
 await fill('confirm_password','NewPassword-123');await submit();await until(`document.body.innerText.includes('Password updated')`);assert.equal(counts.reset,1);
 assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`),true);
 expire=true;await command('Page.navigate',{url:'about:blank'});await delay(200);await navigate('/reset-password#token='+'a'.repeat(64));await until(`!!document.querySelector('input[name=confirm_password]')`);
 assert.equal(await evaluate('location.hash'),'');assert.equal(await evaluate(`!!document.querySelector('input[name=identifier]')`),false);
 await fill('password','NewPassword-123');await fill('confirm_password','NewPassword-123');await submit();await until(`!!document.querySelector('input[name=code]')&&document.body.innerText.includes('Invalid or expired password reset session')`);
 assert.deepEqual(errors,[]);
 console.log('Recovery browser checks passed: duplicate submission, generic confirmation, cooldown, wrong code, verification, password confirmation, success, link fragment removal, expired link recovery, mobile layout. All API calls intercepted locally.');
}finally{ws?.close();chrome?.kill();web.kill();}
