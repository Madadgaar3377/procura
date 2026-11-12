// Run against an isolated loopback frontend and the admin-modal API fixture.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
const origin=process.env.ADMIN_MODAL_TEST_ORIGIN||'http://127.0.0.1:13097';
const fixture=process.env.ADMIN_MODAL_FIXTURE_ORIGIN||'http://127.0.0.1:18084';
for(const url of [origin,fixture])assert.ok(['127.0.0.1','localhost'].includes(new URL(url).hostname),'Local test servers only');
const directory=path.resolve('.test-runtime/admin-modals');await fs.mkdir(directory,{recursive:true});
const port=24500+Math.floor(Math.random()*2000);
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,`--user-data-dir=${path.join(directory,'chrome-'+process.pid)}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
let ws;
try{
 let target;for(let i=0;i<100;i++){try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page');if(target)break;}catch{}await delay(150);}
 assert.ok(target,'Chrome starts');ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 let sequence=0;const waiting=new Map(),errors=[],checks=[];
 ws.addEventListener('message',event=>{const result=JSON.parse(event.data);if(result.method==='Runtime.exceptionThrown')errors.push(result.params.exceptionDetails.exception?.description||result.params.exceptionDetails.text);if(result.method==='Runtime.consoleAPICalled'&&result.params.type==='error')errors.push(result.params.args.map(x=>x.value||x.description||'').join(' '));if(result.id){const task=waiting.get(result.id);waiting.delete(result.id);if(result.error)task?.reject(new Error(result.error.message));else task?.resolve(result.result);}});
 function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{waiting.delete(id);reject(new Error('Timed out: '+method));},60000);waiting.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id,method,params}));});}
 async function evaluate(expression){const r=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
 async function until(expression){for(let i=0;i<180;i++){if(await evaluate(expression))return;await delay(100);}throw new Error('Condition timed out: '+expression+' | '+await evaluate('document.body.innerText.slice(-1800)'));}
 async function click(label){await evaluate(`(()=>{const scope=[...document.querySelectorAll('dialog[open]')].at(-1)||document;const button=[...scope.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)});if(!button)throw Error('Button not found: '+${JSON.stringify(label)});button.click();})()`);}
 async function navigate(route){await evaluate('window.__previousAdminPage=true');await command('Page.navigate',{url:origin+route});await until(`!window.__previousAdminPage&&document.readyState==='complete'&&!!document.querySelector('.app-content')`);await delay(200);}
 async function screenshot(name){const r=await command('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(directory,name+'.png'),Buffer.from(r.data,'base64'));}
 async function state(mode){await fetch(`${fixture}/api/v1/__test/state?mode=${mode}`);}
 async function requests(){return await(await fetch(`${fixture}/api/v1/__test/requests`)).json();}
 async function validateModal(name){
  await until(`!!document.querySelector('dialog.admin-modal[open]')`);await delay(220);
  const value=await evaluate(`(()=>{const d=[...document.querySelectorAll('dialog[open]')].at(-1),r=d.getBoundingClientRect(),h=d.querySelector('.admin-modal-header'),f=d.querySelector('.admin-modal-footer'),body=d.querySelector('.admin-modal-body');return {label:document.getElementById(d.getAttribute('aria-labelledby'))?.textContent,modal:d.matches(':modal'),left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:innerHeight,width:innerWidth,header:!!h,footer:!!f,body:!!body,focus:d.contains(document.activeElement),overflow:d.scrollWidth>d.clientWidth+1};})()`);
  assert.ok(value.label&&value.modal&&value.header&&value.footer&&value.body&&value.focus,`${name}: accessible native modal with shared sections: ${JSON.stringify(value)}`);
  assert.ok(value.left>=0&&value.right<=value.width+1&&value.top>=0&&value.bottom<=value.height+1&&!value.overflow,`${name}: fits viewport`);checks.push({name,...value});
 }
 async function escape(){await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});}
 await command('Page.enable');await command('Runtime.enable');await command('Network.enable');await command('Emulation.setFocusEmulationEnabled',{enabled:true});
 await command('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:directory});
 await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await fetch(`${fixture}/api/v1/__test/state?mode=processing&reset=1`);await navigate('/app/admin/suppliers');
 await evaluate(`localStorage.removeItem('procura:supplier-import');[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bulk Upload Suppliers')).click()`);
 await validateModal('supplier bulk upload initial');await screenshot('bulk-initial-1440-en');
 // Real browser file selection validates the existing Excel formats before upload.
 const good=path.join(directory,'suppliers.xlsx'),bad=path.join(directory,'invalid.csv');await fs.writeFile(good,'test Excel fixture');await fs.writeFile(bad,'wrong format');
 const dom=await command('DOM.getDocument');const input=await command('DOM.querySelector',{nodeId:dom.root.nodeId,selector:'dialog[open] input[type=file]'});
 await command('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[bad]});await until(`document.querySelector('[role=alert]')?.textContent.includes('.xlsx')`);
 assert.equal((await requests()).filter(r=>r.method==='POST'&&r.path==='imports/bulk/suppliers/jobs').length,0);
 await command('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[good]});await until(`document.querySelector('.admin-selected-file')?.textContent.includes('suppliers.xlsx')`);
 await evaluate(`document.querySelectorAll('.admin-download')[0].click()`);await until(`document.querySelectorAll('.admin-download')[0].disabled`);await until(`!document.querySelectorAll('.admin-download')[0].disabled`);
 await evaluate(`document.querySelectorAll('.admin-download')[1].click()`);await until(`document.querySelectorAll('.admin-download')[1].disabled`);await until(`!document.querySelectorAll('.admin-download')[1].disabled`);
 await evaluate(`const target=document.querySelector('.admin-upload-zone'),data=new DataTransfer();data.items.add(new File(['legacy source'],'suppliers-part-2.xls',{type:'application/vnd.ms-excel'}));target.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:data}));`);
 await until(`document.querySelector('.admin-selected-file')?.textContent.includes('suppliers-part-2.xls')`);
 await evaluate(`(()=>{const b=[...document.querySelectorAll('dialog[open] button')].find(b=>b.textContent.trim()==='Start Import');b.click();b.click();})()`);
 await until(`document.querySelector('.admin-progress-panel progress')?.value===35.71`);
 assert.equal((await requests()).filter(r=>r.method==='POST'&&r.path==='imports/bulk/suppliers/jobs').length,1,'Duplicate clicks produce one upload');
 await validateModal('processing supplier import');await screenshot('bulk-processing-1440-en');
 assert.equal(await evaluate(`document.querySelector('.admin-issues').open`),false,'Warnings collapsed by default');
 assert.equal(await evaluate(`document.querySelector('.admin-progress-panel').textContent.includes('?')`),false);
 await escape();assert.equal(await evaluate(`document.querySelector('.bulk-upload-dialog').open`),true,'Active work cannot be dismissed accidentally');
 await click('Close');await until(`!document.querySelector('dialog[open]')`);const stopped=(await requests()).filter(r=>r.path==='imports/bulk/suppliers/jobs/fixture-batch').length;await delay(3000);assert.equal((await requests()).filter(r=>r.path==='imports/bulk/suppliers/jobs/fixture-batch').length,stopped,'Polling stops while closed');
 await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bulk Upload Suppliers')).click()`);await until(`document.querySelector('.admin-progress-panel progress')?.value===35.71`);
 assert.equal((await requests()).filter(r=>r.method==='POST'&&r.path==='imports/bulk/suppliers/jobs').length,1,'Reopening recovers the same job');
 await state('unknown');await click('Refresh progress');await until(`!!document.querySelector('.admin-progress-panel progress')&&!document.querySelector('.admin-progress-panel progress').hasAttribute('value')`);
 await state('failed');await click('Refresh progress');await until(`!![...document.querySelectorAll('dialog[open] button')].find(b=>b.textContent.includes('Retry failed rows'))`);await screenshot('bulk-failed-1440-en');
 await evaluate(`(()=>{const b=[...document.querySelectorAll('dialog[open] button')].find(b=>b.textContent.includes('Retry failed rows'));b.click();b.click();})()`);await until(`document.querySelector('.admin-progress-panel progress')?.value===35.71`);
 assert.equal((await requests()).filter(r=>r.method==='POST'&&r.path==='imports/bulk/suppliers/jobs/fixture-batch/retry').length,1);
 await state('completed_with_errors');await click('Refresh progress');await until(`!!document.querySelector('.admin-completion.needs-review')`);await screenshot('bulk-review-1440-en');
 await evaluate(`document.querySelector('.admin-issues summary').click()`);await until(`document.querySelector('.admin-issues').open`);await click('Download warnings');await until(`![...document.querySelectorAll('.admin-issues .admin-download')].some(b=>b.disabled)`);
 await state('completed');await click('Refresh progress');await until(`!!document.querySelector('.admin-completion:not(.needs-review)')`);
 const terminal=(await requests()).filter(r=>r.path==='imports/bulk/suppliers/jobs/fixture-batch').length;await delay(3000);assert.equal((await requests()).filter(r=>r.path==='imports/bulk/suppliers/jobs/fixture-batch').length,terminal,'Polling stops at completion');
 await click('Close');await until(`!document.querySelector('dialog[open]')`);
 // Shared form framing preserves existing fields and native submission.
 await click('Add supplier');await validateModal('add supplier');assert.ok(await evaluate(`!!document.querySelector('.admin-modal-footer button[form]')`),'Create action remains associated with its form in fixed footer');await screenshot('supplier-add-1440-en');
 await evaluate(`(()=>{const input=document.querySelector('dialog[open] form input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'UI fixture company');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);await until(`![...document.querySelectorAll('dialog[open] button')].find(b=>b.textContent.trim()==='Create supplier').disabled`);await click('Create supplier');await until(`!document.querySelector('dialog[open]')`);assert.equal((await requests()).filter(r=>r.method==='POST'&&r.path==='organizations/admin/suppliers').length,1,'Fixed footer submits the existing supplier form exactly once');
 await click('View details');await validateModal('supplier details and edit forms');await screenshot('supplier-details-1440-en');await escape();await until(`!document.querySelector('dialog[open]')`);
 for(const [route,button,name] of [['/app/admin/materials','Add material','material editor'],['/app/admin/categories','Add main category','category editor'],['/app/admin/users','Add user','user editor'],['/app/admin/membership','Create plan','plan editor']]){
  await navigate(route);await click(button);await validateModal(name);await screenshot(name.replaceAll(' ','-')+'-1440-en');await escape();await until(`!document.querySelector('dialog[open]')`);
 }
 await navigate('/app/admin/materials');await click('Import materials');await validateModal('material import preview');await screenshot('material-import-1440-en');await escape();await until(`!document.querySelector('dialog[open]')`);
 await click('Add category');await validateModal('material category editor');await escape();await until(`!document.querySelector('dialog[open]')`);
 await navigate('/app/admin/bulk-import');await click('Import suppliers');await validateModal('supplier import preview');await screenshot('supplier-import-preview-1440-en');await escape();await until(`!document.querySelector('dialog[open]')`);
 await navigate('/app/admin/suppliers');await click('Reprocess Supplier Categories');await validateModal('supplier reprocessing preview');await escape();await until(`!document.querySelector('dialog[open]')`);
 await navigate('/app/admin/suppliers');await click('Delete supplier');await validateModal('supplier delete confirmation');await screenshot('supplier-delete-1440-en');await click('Cancel');await until(`!document.querySelector('dialog[open]')`);
 assert.equal((await requests()).filter(r=>r.method==='POST'&&r.path==='organizations/admin/suppliers/supplier-test/archive').length,0,'Cancelling confirmation preserves suppliers');
 // Responsive RTL state uses the same persisted backend job and real supplied counts.
 for(const width of [320,375,430,768,1024,1920]){
  await command('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<768});
  await command('Network.setCookie',{name:'procura.locale',value:'ar',url:origin,path:'/'});
  await navigate('/app/admin/suppliers?lang=ar');await evaluate(`window.__adminReload=true;localStorage.setItem('procura.locale','ar');localStorage.setItem('procura:supplier-import','fixture-batch');location.reload()`);
  await until(`!window.__adminReload&&document.documentElement?.dir==='rtl'&&!!document.querySelector('.app-content')`);
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bulk Upload Suppliers')).click()`);
  await until(`!!document.querySelector('.admin-completion')`);await validateModal(`bulk RTL ${width}`);await screenshot(`bulk-${width}-ar`);
  const geometry=await evaluate(`(()=>{const d=document.querySelector('dialog[open]'),body=d.querySelector('.admin-modal-body'),h=d.querySelector('.admin-modal-header').getBoundingClientRect(),f=d.querySelector('.admin-modal-footer').getBoundingClientRect();body.scrollTop=body.scrollHeight;return {h:h.top,f:f.bottom,nextH:d.querySelector('.admin-modal-header').getBoundingClientRect().top,nextF:d.querySelector('.admin-modal-footer').getBoundingClientRect().bottom};})()`);
  assert.equal(geometry.h,geometry.nextH);assert.equal(geometry.f,geometry.nextF);
  if(width===320||width===375){await click('اختيار ملف آخر');await validateModal(`upload dropzone RTL ${width}`);await screenshot(`bulk-upload-${width}-ar`);}
  await escape();await until(`!document.querySelector('dialog[open]')`);
 }
 await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bulk Upload Suppliers')).click()`);await validateModal('reduced motion and keyboard trap');
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('dialog[open]')).animationName`),'none');
 await evaluate(`(()=>{const buttons=[...document.querySelectorAll('dialog[open] button')].filter(b=>!b.disabled&&b.getClientRects().length);buttons.at(-1).focus();})()`);
 await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
 assert.ok(await evaluate(`document.querySelector('dialog[open]').contains(document.activeElement)`),'Dialog traps forward keyboard focus');
 await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:8});await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:8});
 assert.ok(await evaluate(`document.querySelector('dialog[open]').contains(document.activeElement)`),'Dialog traps backward keyboard focus');await escape();await until(`!document.querySelector('dialog[open]')`);
 await fs.writeFile(path.join(directory,'results.json'),JSON.stringify({checks,errors,requests:await requests()},null,2));
 assert.deepEqual(errors,[],'No console or hydration errors');console.log('Admin modal checks passed:',checks.length);
}finally{ws?.close();chrome.kill();}
