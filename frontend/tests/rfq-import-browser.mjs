// Real built UI -> isolated FastAPI -> durable worker -> local Gemini simulator.
import puppeteer from 'puppeteer-core';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve('..'),origin='http://127.0.0.1:13132',backend='http://127.0.0.1:18142';
const artifacts=path.resolve('.test-runtime/rfq-import');await fs.mkdir(artifacts,{recursive:true});
let output='',browser;
const server=spawn(process.env.PYTHON_BIN||'python',['tests/rfq_import_browser_server.py'],{cwd:path.join(root,'backend'),windowsHide:true,stdio:['ignore','pipe','pipe']});
const frontend=spawn(process.execPath,['node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','13132'],{env:{...process.env,NEXT_BUILD_DIR:'.next-rfq-import'},windowsHide:true,stdio:['ignore','pipe','pipe']});
for(const child of [server,frontend]){child.stdout.on('data',d=>output+=d);child.stderr.on('data',d=>output+=d);}
async function untilReady(url){for(let i=0;i<160;i++){try{if((await fetch(url)).ok)return;}catch{}await delay(250);}throw new Error(output);}
try{
 await Promise.all([untilReady(origin+'/login'),untilReady(backend+'/test/counts')]);
 browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--no-sandbox','--disable-gpu'],userDataDir:path.join(artifacts,'chrome-'+process.pid)});
 const page=await browser.newPage();await page.setViewport({width:1440,height:1000});const errors=[];
 const downloads=await page.createCDPSession();await downloads.send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:artifacts});
 page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
 // Rewrite the URL only: Chrome forwards the real multipart bytes unchanged.
 await page.setRequestInterception(true);page.on('request',request=>{const url=new URL(request.url()),start=url.pathname.indexOf('/api/v1/');if(start>=0)return request.continue({url:backend+url.pathname.slice(start)+url.search});if(url.origin===origin||url.protocol==='data:')return request.continue();return request.abort();});
 const button=async text=>{const handle=await page.waitForFunction(label=>Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===label&&!b.disabled),{timeout:20000},text).catch(async e=>{throw new Error(text+' | '+await page.$eval('body',n=>n.innerText.slice(-3500))+' | '+e.message+' | '+output.slice(-1500));});await handle.asElement().click();await handle.dispose();};
 const replace=async(selector,value)=>{await page.click(selector);await page.keyboard.down('Control');await page.keyboard.press('a');await page.keyboard.up('Control');await page.keyboard.type(value);};
 await page.goto(origin+'/app/buyer/rfqs/new',{waitUntil:'networkidle0'});
 await page.type('input[name=title]','Excel procurement with manual item');await page.type('input[name=city]','Riyadh');
 await page.$eval('input[name=response_deadline]',input=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2028-12-01T12:00');input.dispatchEvent(new Event('input',{bubbles:true}));});
 await button('Continue');await page.select('[data-maincategory]','main');await page.select('[data-subcategory]','pipes');await page.type('[data-description]','Manual pipe support');await page.select('.grouped-material-row select','M');await button('Save Group');
 await page.waitForSelector('.rfq-material-row');await button('Import Materials from Excel');await page.waitForSelector('dialog[open]');
 assert.equal(await page.$eval('.rfq-import-ai input',n=>n.checked),true);
 await page.evaluate(()=>{const original=URL.createObjectURL;URL.createObjectURL=function(blob){window.__rfqDownloadSize=blob.size;return original.call(URL,blob);};});
 await button('Download Excel Template');await page.waitForFunction(()=>window.__rfqDownloadSize>1000);
 const templateBytes=await (await fetch(backend+'/api/v1/rfqs/imports/template?org_id=org')).arrayBuffer();assert.ok(templateBytes.byteLength>1000);

 for(const width of [1440,768,375,320]){await page.setViewport({width,height:900});await delay(250);assert.ok(await page.$eval('dialog[open]',n=>{const r=n.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1;}));await page.screenshot({path:path.join(artifacts,`upload-${width}.png`)});}
 await page.setViewport({width:1440,height:1000});
 const file=path.join(artifacts,'real-client.csv');await fs.writeFile(file,'Description,Unit,Qty\nPIPES,,\nBoosted Cold Water (BCW) Pipe Ø20,m,128.38\nDescription,Unit,Qty\nBCW cold water pipe diameter 21,m,27.17\nPIPE FITTINGS,,\nCustom support bracket Grade 60,m,4.25\nRemove this test item,m,1\n');
 await (await page.$('.rfq-import-dropzone input[type=file]')).uploadFile(file);await button('Upload and match materials');
 await page.waitForSelector('[data-import-row]',{timeout:30000});await page.waitForFunction(()=>document.querySelectorAll('[data-import-row]').length===4);
 const before=await (await fetch(backend+'/test/counts')).json();assert.equal(before.rfqs,0);assert.equal(before.rfq_invitations,0);assert.equal(before.notifications,0);assert.ok(before.gemini_requests>=1);
 const rows=await page.$$('[data-import-row]');const custom=rows[2];const customId=await custom.evaluate(n=>n.getAttribute('data-import-row'));
 await replace(`[data-import-row="${customId}"] textarea`,'Custom support bracket Grade 60 - buyer specification');await replace(`[data-import-row="${customId}"] input[type=number]`,'7.125');
 await rows[3].$eval('input[type=checkbox]',input=>input.click());await button('Remove selected rows');await page.waitForFunction(()=>document.querySelectorAll('[data-import-row]').length===3);
 assert.equal(await page.$eval(`[data-import-row="${customId}"] textarea`,n=>n.value),'Custom support bracket Grade 60 - buyer specification');
 await page.$eval('.rfq-import-preview',n=>n.scrollIntoView({block:'start'}));await delay(350);await page.screenshot({path:path.join(artifacts,'matching-preview.png'),fullPage:false});await button('Accept all high-confidence matches');await page.waitForFunction(()=>document.querySelectorAll('.rfq-match-status.accepted').length===1&&!document.querySelector('[data-import-row] button').disabled);
 await rows[1].$eval('button',button=>button.click());await page.waitForFunction(()=>document.querySelectorAll('.rfq-match-status.accepted').length===2);
 assert.equal(await page.$eval(`[data-import-row="${customId}"] input[type=number]`,n=>n.value),'7.125');
 await custom.$$eval('button',buttons=>buttons.find(b=>b.textContent.trim()==='Keep as custom material').click());await page.waitForFunction(()=>document.querySelectorAll('.rfq-match-status.accepted').length===3);
 const confirm=await page.$('.admin-modal-footer button:last-child');await confirm.evaluate(button=>{button.click();button.click();});await page.waitForFunction(()=>!document.querySelector('dialog[open]'));
 assert.equal(await page.$$('.rfq-material-row').then(rows=>rows.length),4);
 await button('Continue');await page.waitForFunction(()=>document.querySelector('.live-steps [aria-current=step]')?.textContent.includes('Review'));
 const review=await page.$eval('.data-table',n=>n.innerText);assert.ok(review.includes('Manual pipe support')&&review.includes('buyer specification')&&review.includes('7.125'));
 const saved=page.waitForResponse(r=>r.request().method()==='POST'&&/\/rfqs$/.test(new URL(r.url()).pathname)&&r.status()===201);await button('Save draft');await saved;
 await page.waitForFunction(()=>location.pathname.includes('/rfqs/')&&!location.pathname.endsWith('/new'));
 const rfqId=new URL(page.url()).pathname.split('/').at(-1);const created=await (await fetch(backend+'/api/v1/rfqs/'+rfqId)).json();assert.equal(created.items.length,4);assert.equal(created.rfq.title,'Excel procurement with manual item');assert.equal(created.items.filter(i=>i.material_id).length,2);

 const afterSave=await (await fetch(backend+'/test/counts')).json();assert.equal(afterSave.rfq_invitations,0);assert.equal(afterSave.materials,302);
 const publication=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/publish')&&r.status()===200);await button('Publish');await publication;
 const published=await (await fetch(backend+'/test/counts')).json();assert.equal(published.rfq_invitations,1);
 // Arabic dialog in a new local draft; no real suppliers or messages are involved.
 await page.evaluate(()=>localStorage.setItem('procura.locale','ar'));await page.goto(origin+'/app/buyer/rfqs/new',{waitUntil:'networkidle0'});
 await page.type('input[name=title]','طلب مواد تجريبي');await page.type('input[name=city]','Riyadh');await page.$eval('input[name=response_deadline]',input=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2028-12-01T12:00');input.dispatchEvent(new Event('input',{bubbles:true}));});
 const continueButton=await page.$('.form-actions button[type=submit]');await continueButton.click();await page.waitForSelector('.grouped-entry');
 const importButton=await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('استيراد المواد من Excel')));await importButton.asElement().click();await page.waitForSelector('dialog[open][dir=rtl]');await page.setViewport({width:375,height:850});await delay(350);await page.screenshot({path:path.join(artifacts,'arabic-mobile.png')});
 const text=await page.$eval('dialog[open]',n=>n.innerText);assert.ok(text.includes('استيراد مواد طلب الأسعار')&&!text.includes('Upload file'));
 await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('dialog[open]'));assert.deepEqual(errors,[]);
 console.log('PASS: real template/upload/parser/worker/AI simulator/review, manual preservation, duplicate-click guard, Step 3, draft creation, publish and one supplier invitation, desktop/tablet/mobile/Arabic.');
}finally{await browser?.close();server.kill();frontend.kill();}
