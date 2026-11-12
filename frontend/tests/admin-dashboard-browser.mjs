// Dashboard QA uses an ignored, read-only PostgreSQL snapshot, never synthetic metrics.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
const origin=process.env.DASHBOARD_TEST_ORIGIN||'http://127.0.0.1:13097';
const artifacts=path.resolve('.test-runtime/admin-dashboard');
const snapshot=JSON.parse(await fs.readFile(path.join(artifacts,'snapshot.json'),'utf8'));
const port=24917;
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,`--user-data-dir=${path.join(artifacts,'chrome-'+process.pid)}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
let ws;
try{
 let target;
 for(let i=0;i<80;i++){try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(p=>p.type==='page');if(target)break;}catch{}await delay(200);}
 assert.ok(target,'Headless Chrome starts');
 ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 let sequence=0;const pending=new Map();const errors=[];
 ws.addEventListener('message',e=>{const data=JSON.parse(e.data);if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails.text);if(data.method==='Runtime.consoleAPICalled'&&data.params.type==='error')errors.push(data.params.args.map(a=>a.value||a.description).join(' '));if(data.id){const call=pending.get(data.id);pending.delete(data.id);data.error?call?.reject(Error(data.error.message)):call?.resolve(data.result);}});
 function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>reject(Error(method+' timed out')),15000);pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id,method,params}));});}
 async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
 async function until(expression){for(let i=0;i<120;i++){if(await evaluate(expression))return;await delay(100);}throw Error('Timed out: '+expression+' '+await evaluate('document.body.innerText.slice(0,700)'));}
 await command('Page.enable');await command('Runtime.enable');
 await command('Page.addScriptToEvaluateOnNewDocument',{source:`(()=>{const snapshot=${JSON.stringify(snapshot)};const native=window.fetch.bind(window);window.dashboardRequests=[];window.dashboardFail=false;window.fetch=async(input,options)=>{const url=String(input);if(!url.includes('/api/v1/')&&!url.includes('/api/backend/'))return native(input,options);const pathname=new URL(url,location.href).pathname;let result=[];if(pathname.endsWith('/auth/me'))result={id:'browser-admin',username:'Browser QA',is_admin:true,is_active:true,phone_verified:true,email_verified:true,phone:'',email:null};if(pathname.endsWith('/admin/dashboard/analytics')){const days=new URL(url,location.href).searchParams.get('days')||'30';window.dashboardRequests.push(days);if(window.dashboardFail)return new Response(JSON.stringify({detail:'QA unavailable'}),{status:503});result=snapshot[days];}return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});};})();`});
 const results=[];
 for(const width of [320,375,430,768,1024,1440,1920]){
  await command('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:width<768});
  for(const lang of ['en','ar']){
   await command('Network.setCookie',{name:'procura.locale',value:lang,url:origin,path:'/'});
   await command('Page.navigate',{url:origin+'/app/admin/dashboard'});
   await until(`document.querySelector('h1')?.textContent.includes(${JSON.stringify(lang==='ar'?'نظرة عامة':'Platform Overview')})&&!!document.querySelector('select[aria-label]')&&document.querySelectorAll('strong').length>=8`);
   await delay(300);
   const geometry=await evaluate(`({width:innerWidth,scroll:document.documentElement.scrollWidth,dir:document.documentElement.dir,cards:[...document.querySelectorAll('a[class*="_kpi_"]')].length})`);
   assert.ok(geometry.scroll<=width+1,JSON.stringify({width,lang,...geometry}));assert.equal(geometry.dir,lang==='ar'?'rtl':'ltr');assert.equal(geometry.cards,8);
   if(width===1440||width===375){const screenshot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(artifacts,`${width}-${lang}.png`),Buffer.from(screenshot.data,'base64'));}
   results.push({width,lang,...geometry});
  }
 }
 for(const days of [7,30,90]){
  await evaluate(`(()=>{const select=document.querySelector('select[aria-label]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,'${days}');select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`window.dashboardRequests.includes('${days}')`);
 }
 const previous=await evaluate('window.dashboardRequests.length');
 await evaluate(`document.querySelector('button:has(svg.tabler-icon-refresh)').click()`);
 await until(`window.dashboardRequests.length>${previous}`);
 await until(`!document.querySelector('button:has(svg.tabler-icon-refresh)').disabled`);
 await evaluate(`window.dashboardFail=true;document.querySelector('button:has(svg.tabler-icon-refresh)').click()`);
 await until(`!!document.querySelector('[role="alert"]')`);
 await evaluate(`window.dashboardFail=false;document.querySelector('[role="alert"] button').click()`);
 await until(`!document.querySelector('[role="alert"]')`);
 assert.deepEqual(errors,[],'Browser console and runtime errors');
 await fs.writeFile(path.join(artifacts,'results.json'),JSON.stringify(results,null,2));
 console.log(`Passed: ${results.length} viewport/language checks, all ranges, refresh, error/retry; no console errors.`);
}finally{ws?.close();chrome.kill();}
