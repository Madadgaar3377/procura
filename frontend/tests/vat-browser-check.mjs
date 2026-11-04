import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
export async function browserCheck({origin,frontend,rfqId}){
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

  await command('Page.enable');await command('Runtime.enable');await navigate('/login');
  await evaluate(`fetch('/api/backend/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:'+966500009902',password:'SmokeTestPassword-123'})}).then(async r=>{if(!r.ok)throw Error(await r.text());})`);
  await navigate('/app/supplier/company');await until(`!!document.querySelector('input[name=vat_number]')`);
  await evaluate(`window.__vatWrites=0;window.__originalFetch=window.fetch;window.fetch=function(...args){if(String(args[0]).includes('/organizations/')&&args[1]?.method==='PATCH')window.__vatWrites++;return window.__originalFetch.apply(this,args);}`);
  async function submitVat(value){await evaluate(`(()=>{const input=document.querySelector('input[name=vat_number]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true}));input.closest('form').requestSubmit();})()`);}
  await submitVat('1500006541651');await until(`document.querySelector('input[name=vat_number]').getAttribute('aria-invalid')==='true'`);
  assert.equal(await evaluate(`document.querySelector('#field-error-vat_number').textContent`),'VAT number must contain exactly 15 digits, or be left blank if not VAT registered.');
  assert.equal(await evaluate(`window.__vatWrites`),0,'Invalid VAT must be caught before an API write');
  assert.equal(await evaluate(`document.querySelector('input[name=vat_number]').value`),'1500006541651');
  await screenshot('vat-readable-inline-error');
  await evaluate(`document.querySelector('[aria-label="Switch language"]').click()`);await until(`document.documentElement.lang==='ar'`);
  assert.equal(await evaluate(`document.querySelector('#field-error-vat_number').textContent`),'يجب أن يتكون الرقم الضريبي من 15 رقماً بالضبط، أو يُترك فارغاً إذا لم تكن مسجلاً في ضريبة القيمة المضافة.');
  await evaluate(`document.querySelector('[aria-label="Switch language"]').click()`);await until(`document.documentElement.lang==='en'`);
  await submitVat('300 000 000 000 003');await until(`document.body.innerText.includes('Changes saved.')`);
  assert.equal(await evaluate(`window.__vatWrites`),1);
  await navigate('/app/supplier/company');await until(`document.querySelector('input[name=vat_number]')?.value==='300000000000003'`);
  await submitVat('');await until(`document.body.innerText.includes('Changes saved.')`);
  await navigate('/app/supplier/company');await until(`!!document.querySelector('input[name=vat_number]')`);
  assert.equal(await evaluate(`document.querySelector('input[name=vat_number]').value`),'');
  assert.deepEqual(runtimeErrors,[]);console.log('VAT browser checks passed: 13-digit input rejected before API writes, translated inline error, formatted value saved/reloaded, optional VAT cleared/reloaded.');
 }finally{ws?.close();chrome.kill();}
}
