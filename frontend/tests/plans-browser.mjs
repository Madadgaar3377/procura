// Real loopback API + production Next build + headless Chrome. No live data.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
const frontend=process.cwd(),backend=path.resolve('../backend');
const origin='http://127.0.0.1:13095',apiOrigin='http://127.0.0.1:18095';
const output=path.resolve('.test-runtime/plan-browser');await fs.mkdir(output,{recursive:true});
const children=[];let ws;let logs='';
function start(command,args,cwd,env={}){const child=spawn(command,args,{cwd,env:{...process.env,...env},windowsHide:true,stdio:['ignore','pipe','pipe']});child.stdout.on('data',d=>logs+=d);child.stderr.on('data',d=>logs+=d);children.push(child);return child;}
async function ready(url){for(let i=0;i<120;i++){try{if((await fetch(url)).ok)return;}catch{}await delay(250);}throw new Error('Test server failed to start: '+url);}
try{
 for(const url of [origin,apiOrigin]){let reachable=false;try{await fetch(url);reachable=true;}catch{}assert.ok(!reachable,'Dedicated test ports must be free');}
 start('python',['tests/local_plan_server.py'],backend);
 start(process.execPath,['node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','13095'],frontend,{NEXT_BUILD_DIR:'.next-plan-validation',BACKEND_URL:apiOrigin,FASTAPI_INTERNAL_URL:apiOrigin});
 await Promise.all([ready(apiOrigin+'/health/live'),ready(origin+'/pricing')]);
 const port=29000+Math.floor(Math.random()*1000);
 start('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-sandbox','--disable-dev-shm-usage','--disable-background-networking','--disable-extensions','--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,`--user-data-dir=${path.join(output,'chrome-'+process.pid)}`,'about:blank'],frontend);
 let target;for(let i=0;i<100;i++){try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page');if(target)break;}catch{}await delay(100);}
 assert.ok(target,'Chrome starts');ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 let sequence=0;const pending=new Map();
 ws.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.method==='Runtime.consoleAPICalled'&&message.params.type==='error')logs+='\nCONSOLE: '+JSON.stringify(message.params);if(message.method==='Runtime.exceptionThrown')logs+='\nBROWSER: '+JSON.stringify(message.params);if(message.id){const task=pending.get(message.id);pending.delete(message.id);message.error?task?.reject(new Error(message.error.message)):task?.resolve(message.result);}});
 function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>reject(new Error('CDP timeout: '+method)),20000);pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id,method,params}));});}
 async function evaluate(expression){const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;}
 async function until(expression){for(let i=0;i<200;i++){try{if(await evaluate(`Boolean(${expression})`))return;}catch{}await delay(100);}throw new Error('Timed out: '+expression+' | '+await evaluate('document.body.innerText.slice(-1600)'));}
 async function navigate(route){await evaluate('window.__previousDocument=true');await command('Page.navigate',{url:origin+route});await until('!window.__previousDocument&&document.readyState==="complete"&&location.pathname==='+JSON.stringify(route.split('?')[0]));}
 async function screenshot(name){const capture=await command('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(output,name+'.png'),Buffer.from(capture.data,'base64'));}
 async function api(route,method='GET',body){return await evaluate(`(async()=>{const r=await fetch(${JSON.stringify(apiOrigin+'/api/v1/'+route)},{method:${JSON.stringify(method)},credentials:'include',headers:{'Content-Type':'application/json'},${body===undefined?'':`body:JSON.stringify(${JSON.stringify(body)}),`}});const data=await r.json();if(!r.ok)throw new Error(JSON.stringify(data));return data;})()`);}
 async function click(text){await evaluate(`(()=>{const button=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}&&!b.disabled);if(!button)throw new Error('Missing button: '+${JSON.stringify(text)});button.click();})()`);}
 async function field(label,value){await evaluate(`(()=>{const element=[...document.querySelectorAll('dialog label')].find(l=>l.textContent.startsWith(${JSON.stringify(label)}))?.querySelector('input,textarea,select');if(!element)throw new Error('Missing field');const prototype=element instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:element instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(element,${JSON.stringify(value)});element.dispatchEvent(new Event('input',{bubbles:true}));element.dispatchEvent(new Event('change',{bubbles:true}));})()`);}
 await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
 await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await navigate('/pricing');await until('document.body.innerText.includes("Membership plans are coming soon")');
 await api('auth/browser/login','POST',{identifier:'+966500009903',password:'LocalPlanTest-123'});
 await navigate('/app/admin/membership');await until('document.body.innerText.includes("Manage Plans")');await delay(1500);
 await click('Create plan');await until('document.querySelector("dialog[open]")');
 await field('Plan Name','Browser isolated plan');await field('Plan Code / Slug','browser-isolated-plan');await field('Monthly Price (SAR)','89.50');await field('Yearly Price (SAR)','899.00');
 await click('Add feature');await until('document.querySelector(".plan-feature-row > input")');
 await field('Plan Name','Browser isolated plan');
 await evaluate(`(()=>{const e=document.querySelector('[aria-label="Feature 1"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'Configurable browser benefit');e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
 await click('Save Draft');await until('!document.querySelector("dialog[open]")');
 let plans=await api('memberships/admin/plans');assert.equal(plans.length,1);let plan=plans[0];assert.equal(plan.publication_status,'draft');assert.equal(plan.monthly_price,'89.50');
 assert.equal((await api('memberships/plans')).length,0);
 await until('document.body.innerText.includes("Browser isolated plan")');await click('Edit');await until('document.querySelector("dialog[open]")');
 assert.equal(await evaluate('document.querySelector("dialog input").value'),'Browser isolated plan');await screenshot('admin-plan-editor');
 await field('Monthly Price (SAR)','90.25');
 await evaluate(`(()=>{const e=document.querySelector('.plan-feature-row > input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'Updated configurable benefit');e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
 await click('Save changes');await until('!document.querySelector("dialog[open]")');
 plan=(await api('memberships/admin/plans'))[0];assert.equal(plan.monthly_price,'90.25');assert.equal(plan.features[0],'Updated configurable benefit');await screenshot('admin-plans-table');
 plan=await api(`memberships/admin/plans/${plan.id}/publication`,'PATCH',{expected_version:plan.version,publication_status:'published'});
 await navigate('/pricing');await until('document.querySelector("[data-plan-slug=browser-isolated-plan]")');
 assert.match(await evaluate('document.querySelector("[data-live-pricing]").innerText'),/90\.25/);
 assert.equal(await evaluate('!!document.querySelector("[data-live-pricing] button:disabled")'),true);
 await click('Yearly');assert.match(await evaluate('document.querySelector("[data-live-pricing]").innerText'),/899\.00/);
 const put=(({id,created_at,updated_at,version,archived,archived_at,...rest})=>({...rest,expected_version:version,monthly_price:'99.75',recommended:true}))(plan);
 plan=await api(`memberships/admin/plans/${plan.id}`,'PUT',put);
 await evaluate('window.dispatchEvent(new Event("focus"))');await click('Monthly');await until('document.querySelector("[data-live-pricing]").innerText.includes("99.75")');
 assert.equal(await evaluate('!!document.querySelector(".pm-plan-recommended-badge")'),true);await screenshot('pricing-desktop-en');
 for(const width of [375,768,1440])for(const lang of ['en','ar']){
  await command('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<768});await navigate('/pricing?lang='+lang);await until('document.querySelector("[data-plan-slug=browser-isolated-plan]")');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),true,'No horizontal overflow '+width+' '+lang);
  assert.equal(await evaluate('document.querySelector("[data-live-pricing]").dir'),lang==='ar'?'rtl':'ltr');await screenshot('pricing-'+width+'-'+lang);
 }
 plan=await api(`memberships/admin/plans/${plan.id}/publication`,'PATCH',{expected_version:plan.version,publication_status:'unpublished'});
 await evaluate('window.dispatchEvent(new Event("focus"))');await until('!document.querySelector("[data-plan-slug=browser-isolated-plan]")&&document.body.innerText.includes("خطط العضوية قريباً")');
 const copy=await api(`memberships/admin/plans/${plan.id}/duplicate`,'POST',{expected_version:plan.version});assert.equal(copy.publication_status,'draft');
 await api(`memberships/admin/plans/${copy.id}/permanent?confirmation=DELETE&expected_version=${copy.version}`,'DELETE');
 await api(`memberships/admin/plans/${plan.id}?expected_version=${plan.version}`,'DELETE');
 assert.equal((await api('memberships/plans')).length,0);
 await evaluate("localStorage.setItem('procura.locale','ar');document.cookie='procura.locale=ar; Path=/'");
 await navigate('/app/admin/membership');await until('document.body.innerText.includes('+JSON.stringify('\u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u062e\u0637\u0637')+')');
 await screenshot('admin-plans-ar');
 await click('\u0625\u0646\u0634\u0627\u0621 \u062e\u0637\u0629');await until('document.querySelector("dialog[open]")');
 await screenshot('admin-plan-editor-ar');
 console.log('PASS: admin UI draft creation and prefilled price/feature editing, real API publish/edit/unpublish/archive/duplicate/delete, live focus refresh, exact prices, features, free-launch disabled checkout, recommended display, monthly/yearly toggle, 6 English/Arabic responsive viewports. Screenshots: '+output);
}catch(error){await fs.writeFile(path.join(output,'server-output.log'),logs);throw error;}finally{try{ws?.close();}catch{}for(const child of children.reverse())child.kill();}
