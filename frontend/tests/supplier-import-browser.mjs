// Run only with a separately seeded local API/database and a synthetic workbook.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';

const origin=process.env.BULK_BROWSER_ORIGIN||'http://127.0.0.1:3000';
const api=process.env.BULK_BROWSER_API_BASE||'http://127.0.0.1:8000/api/v1';
for(const target of [origin,api])assert.ok(['127.0.0.1','localhost'].includes(new URL(target).hostname),'Only loopback test servers are allowed');
assert.ok(process.env.BULK_BROWSER_WORKBOOK,'Set BULK_BROWSER_WORKBOOK to a synthetic local workbook');
assert.ok(process.env.BULK_BROWSER_IDENTIFIER&&process.env.BULK_BROWSER_PASSWORD,'Set credentials for a synthetic local test administrator');
const directory=path.resolve('.test-runtime/supplier-import-browser');
await fs.mkdir(directory,{recursive:true});
const port=23000+Math.floor(Math.random()*5000);
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
 '--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking',
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
 let sequence=0;const pending=new Map();
 ws.addEventListener('message',event=>{
  const result=JSON.parse(event.data);
  if(result.id){const call=pending.get(result.id);pending.delete(result.id);if(result.error)call?.reject(new Error(result.error.message));else call?.resolve(result.result);}
 });
 function command(method,params={}){return new Promise((resolve,reject)=>{
  const id=++sequence;
  const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Browser command timed out: '+method));},45000);
  pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
  ws.send(JSON.stringify({id,method,params}));
 });}
 async function evaluate(expression){const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result.value;}
 async function until(expression,label){for(let i=0;i<180;i++){if(await evaluate(expression))return;await delay(400);}throw new Error('Timed out: '+label);}
 await command('Page.enable');await command('Network.enable');await command('DOM.enable');
 await command('Page.navigate',{url:origin+'/login'});
 await until('document.readyState === "complete"','login page');
 const login=await evaluate(`fetch(${JSON.stringify(api+'/auth/browser/login')},{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({identifier:process.env.BULK_BROWSER_IDENTIFIER,password:process.env.BULK_BROWSER_PASSWORD})})}).then(r=>r.status)`);
 assert.equal(login,200,'Synthetic administrator signs in with real HttpOnly session cookies');
 await evaluate("localStorage.removeItem('procura:supplier-import')");
 await command('Page.navigate',{url:origin+'/app/admin/suppliers'});
 const open="Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Bulk Upload Suppliers'))";
 await until(`!!(${open})`,'supplier workspace');
 await evaluate(`(${open}).click()`);
 await until('!!document.querySelector("dialog[open] input[type=file]")','upload dialog');
 const root=await command('DOM.getDocument',{depth:-1});
 const input=await command('DOM.querySelector',{nodeId:root.root.nodeId,selector:'dialog[open] input[type=file]'});
 await command('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[path.resolve(process.env.BULK_BROWSER_WORKBOOK)]});
 await evaluate("Array.from(document.querySelectorAll('dialog[open] button')).find(b=>b.textContent.trim()==='Upload').click()");
 await until("Number(document.querySelector('dialog[open] progress')?.value)>0 && Number(document.querySelector('dialog[open] progress')?.value)<100",'first committed progress');
 const before=await evaluate("Number(document.querySelector('dialog[open] progress').value)");
 const jobId=await evaluate("localStorage.getItem('procura:supplier-import')");
 assert.ok(jobId,'Job ID persists before connectivity is lost');
 await command('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:-1,uploadThroughput:-1});
 await delay(3500);
 assert.equal(await evaluate("Number(document.querySelector('dialog[open] progress').value)"),before,'Network interruption does not reset committed progress');
 await command('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});
 await command('Page.reload',{ignoreCache:true});
 await until(`!!(${open})`,'workspace after reload');
 await evaluate(`(${open}).click()`);
 await until("Number(document.querySelector('dialog[open] progress')?.value)===100",'persisted job completion after reload');
 assert.equal(await evaluate("localStorage.getItem('procura:supplier-import')"),jobId,'Reload recovers the same job');
 const result=await evaluate(`fetch(${JSON.stringify(api+'/imports/bulk/suppliers/jobs/')}+${JSON.stringify(jobId)},{credentials:'include'}).then(r=>r.json())`);
 assert.equal(result.status,'completed');
 assert.equal(result.total_supplier_records_identified,28);
 assert.equal(result.new_suppliers_saved,28);
 assert.equal(result.failed,0);
 console.log(JSON.stringify({result:'passed',job_id:jobId,progress_before_disconnect:before,progress_after_reload:100,new_suppliers:result.new_suppliers_saved}));
}finally{
 ws?.close();chrome.kill();
}
