import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
export async function browserCheck({origin,frontend,rfqId}){
 const port=20000+Math.floor(Math.random()*10000);
 const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-sandbox','--disable-dev-shm-usage','--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,`--user-data-dir=${path.join(frontend,'.test-runtime','chrome-profile-'+process.pid)}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
 let ws;let screenshot=async()=>{};let evaluate;
 try{
  let target;
  for(let i=0;i<80;i++){try{const r=await fetch(`http://127.0.0.1:${port}/json/list`,{signal:AbortSignal.timeout(2000)});target=(await r.json()).find(p=>p.type==='page');if(target)break;}catch{}await delay(250);}
  if(!target)throw new Error('Headless Chrome debugging interface unavailable');
  ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Chrome WebSocket connection timed out')),5000);ws.addEventListener('open',()=>{clearTimeout(timeout);resolve();},{once:true});ws.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();let intercept;const runtimeErrors=[];const apiFailures=[];
  ws.addEventListener('message',e=>{const data=JSON.parse(e.data);if(data.method==='Fetch.requestPaused')intercept?.(data.params);if(data.method==='Network.responseReceived'){const response=data.params.response;if(response.url.includes(':18081/api/v1/')&&response.status>=400&&!(response.status===401&&response.url.includes('/auth/'))&&!(response.status===503&&response.url.includes('/organizations/')))apiFailures.push({url:response.url,status:response.status});}if(data.method==='Runtime.exceptionThrown')runtimeErrors.push(data.params.exceptionDetails.text);if(data.id){const call=pending.get(data.id);pending.delete(data.id);if(data.error)call?.reject(new Error(data.error.message));else call?.resolve(data.result);}});
  function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;const timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Chrome command timed out: '+method));},20000);pending.set(id,{resolve:value=>{clearTimeout(timeout);resolve(value);},reject:error=>{clearTimeout(timeout);reject(error);}});ws.send(JSON.stringify({id,method,params}));});}
  evaluate=async function(expression){const r=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;}
  async function until(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await delay(100);}await screenshot('browser-failure');throw new Error('Browser condition timed out: '+expression+' | '+await evaluate('document.body.innerText.slice(-2500)'));}
  async function navigate(route){await command('Page.navigate',{url:origin+route});await until('document.readyState === "complete"');await delay(350);}
  const artifacts=path.join(frontend,'.test-runtime','screenshots');await fs.mkdir(artifacts,{recursive:true});
  screenshot=async function(name){await delay(220);const result=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(artifacts,name+'.png'),Buffer.from(result.data,'base64'));}

  await command('Page.enable');await command('Runtime.enable');await command('Network.enable');await navigate('/login');
  async function login(role){await evaluate(`fetch('/api/backend/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:'${({buyer:'+966500009901',supplier:'+966500009902',admin:'+966500009903'})[role]}',password:'SmokeTestPassword-123'})}).then(async r=>{if(!r.ok)throw Error(await r.text());})`);}
  await login('admin');
  const addedMaterial=await evaluate(`fetch('/api/backend/taxonomy/categories').then(r=>r.json()).then(async cats=>{const category=cats.find(c=>c.code==='SMOKE');const r=await fetch('/api/backend/taxonomy/materials',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:'PRICE-BROWSER-2',name_en:'Wire Puller',unit:'PCS',category_id:category.id})});if(!r.ok)throw Error(await r.text());return r.json();})`);
  await login('supplier');await navigate('/app/supplier/price-book');
  await until(`!!document.querySelector('.supplier-prices .data-table tbody tr')`);
  async function field(selector,value){await evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(input instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));})()`);}
  // Edit through the rendered form and verify a backend round trip, not a local success state.
  await evaluate(`document.querySelector('.supplier-prices .data-table tbody button').click()`);
  await until(`!!document.querySelector('dialog[open] input[name=unit_price]')`);
  assert.equal(await evaluate(`document.querySelector('dialog[open] [name=unit]').disabled`),false);
  await field('dialog[open] input[name=unit_price]','12.50');
  await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);
  await until(`!document.querySelector('dialog[open]')`);
  await until(`document.querySelector('.supplier-prices .data-table tbody').innerText.includes('12.50')`);
  const rate=await evaluate(`fetch('/api/backend/organizations').then(r=>r.json()).then(async orgs=>{const p=await fetch('/api/backend/pricebooks/'+orgs[0].id+'/page');return (await p.json()).items[0];})`);
  assert.equal(rate.unit_price,'12.50');
  await evaluate(`document.querySelector('.page-head-action button').click()`);await until(`!!document.querySelector('dialog[open] .searchable-select input')`);
  await evaluate(`document.querySelector('dialog[open] .searchable-select input').click()`);
  await until(`!!document.querySelector('dialog[open] [role=option]')`);
  await evaluate(`Array.from(document.querySelectorAll('dialog[open] [role=option]')).find(o=>o.innerText==='Smoke category').click()`);
  await until(`!!document.querySelector('dialog[open] .price-material-search input:not(:disabled)')`);
  await evaluate(`document.querySelector('dialog[open] .price-material-search input').click()`);
  await until(`Array.from(document.querySelectorAll('dialog[open] .price-material-search [role=option]')).some(o=>o.innerText.includes('Wire Puller'))`);
  await evaluate(`Array.from(document.querySelectorAll('dialog[open] .price-material-search [role=option]')).find(o=>o.innerText.includes('Wire Puller')).click()`);
  assert.ok((await evaluate(`document.querySelector('dialog[open] [name=unit]').value`)).startsWith('PCS'));
  await field('dialog[open] input[name=unit_price]','120.00');
  await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);await until(`!document.querySelector('dialog[open]')`);
  await until(`document.querySelector('.supplier-prices .data-table tbody').innerText.includes('Wire Puller')`);
  // Search applies to the API and updates the visible page after the debounce.
  await field('.price-filters input[type=search]','does-not-exist');
  await until(`!!document.querySelector('.supplier-prices .table-empty')`);
  await field('.price-filters input[type=search]','Valve');
  await until(`!!document.querySelector('.supplier-prices .data-table tbody tr')`);
  // Reproduce the reported case in a separate supplier organization: all 13
  // selected subcategories pending, across three mains, no approved categories.
  const adminSession=await (await fetch('http://127.0.0.1:18081/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:'+966500009903',password:'SmokeTestPassword-123'})})).json();
  async function adminCall(route,body){const r=await fetch('http://127.0.0.1:18081/api/v1/'+route,{method:'POST',headers:{Authorization:'Bearer '+adminSession.access_token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const data=await r.json();assert.ok(r.ok,JSON.stringify(data));return data;}
  const mains=[];for(const [i,name] of ['Civil','Electrical','Mechanical'].entries())mains.push(await adminCall('taxonomy/categories',{code:'DRAFT-MAIN-'+i,name_en:name}));
  const children=[];for(let i=0;i<13;i++)children.push(await adminCall('taxonomy/categories',{code:'DRAFT-SUB-'+i,name_en:i===1?'Tools':'Selected trade '+i,parent_id:mains[i%3].id}));
  const tools=children[1];
  await adminCall('taxonomy/materials',{code:'DRAFT-PVC',name_en:'PVC Pipe Bender 32 Mm',unit:'PCS',category_id:tools.id,main_category_id:mains[1].id,subcategory_id:tools.id});
  const pendingOrg=await evaluate(`fetch('/api/backend/organizations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Pending supplier browser test',is_supplier:true,is_buyer:false,city:'Riyadh'})}).then(r=>r.json())`);
  const selections=await evaluate(`fetch('/api/backend/capabilities/selections',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({org_id:pendingOrg.id,main_category_ids:mains.map(m=>m.id),subcategory_ids:children.map(c=>c.id),city:'Riyadh'})})}).then(r=>r.json())`);
  assert.equal(selections.length,13);assert.equal(selections.filter(c=>c.confirmed).length,0);
  await evaluate(`fetch('/api/backend/auth/me').then(r=>r.json()).then(user=>sessionStorage.setItem('procura.organization:'+user.id+':supplier',${JSON.stringify(pendingOrg.id)}))`);
  await navigate('/app/supplier/price-book');await until(`!!document.querySelector('.supplier-prices .page-head-action button')`);
  await evaluate(`document.querySelector('.page-head-action button').click()`);await until(`!!document.querySelector('dialog[open] [name=main_category_id]:not(:disabled)')`);
  await evaluate(`document.querySelector('dialog[open] [name=main_category_id]').click()`);
  await until(`document.querySelectorAll('dialog[open] .searchable-select:first-child [role=option]').length===3`);
  assert.deepEqual(await evaluate(`Array.from(document.querySelectorAll('dialog[open] .searchable-select:first-child [role=option]')).map(o=>o.innerText).sort()`),['Civil','Electrical','Mechanical']);
  async function pickCategory(name,text){await evaluate(`document.querySelector('dialog[open] [name=${name}]').focus();document.querySelector('dialog[open] [name=${name}]').click()`);await until(`Array.from(document.querySelectorAll('dialog[open] .searchable-select [role=option]')).some(o=>o.innerText.startsWith(${JSON.stringify(text)}))`);await evaluate(`Array.from(document.querySelectorAll('dialog[open] .searchable-select [role=option]')).find(o=>o.innerText.startsWith(${JSON.stringify(text)})).click()`);}
  // Unit works before selecting any description or master record.
  await pickCategory('unit','PCS');
  await pickCategory('main_category_id','Civil');await pickCategory('subcategory_id','Selected trade 0');
  await field('dialog[open] .price-material-search input','Custom Insulation Material 50mm');
  await until(`document.querySelector('dialog[open]').innerText.includes('Use custom description')`);
  await evaluate(`Array.from(document.querySelectorAll('dialog[open] .price-material-search [role=option]')).find(o=>o.innerText==='Use custom description').click()`);
  assert.ok((await evaluate(`document.querySelector('dialog[open] [name=unit]').value`)).startsWith('PCS'));
  assert.equal(await evaluate(`document.querySelector('dialog[open] [name=active]').checked`),true);
  await field('dialog[open] [name=unit_price]','75.00');await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);await until(`!document.querySelector('dialog[open]')`);
  await until(`document.querySelector('.supplier-prices .data-table tbody').innerText.includes('Custom Insulation Material 50mm')`);
  const privateDraft=await evaluate(`fetch('/api/backend/pricebooks/${pendingOrg.id}/page').then(r=>r.json()).then(p=>p.items[0])`);
  assert.equal(privateDraft.material_id,null);assert.equal(privateDraft.supplier_description,'Custom Insulation Material 50mm');assert.equal(privateDraft.unit,'PCS');assert.equal(privateDraft.unit_price,'75.00');assert.equal(privateDraft.active,true);assert.equal(privateDraft.capability_status,'pending');
  assert.equal((await evaluate(`fetch('/api/backend/taxonomy/materials?q=Custom%20Insulation').then(r=>r.json())`)).length,0);
  // Existing master suggestions still prefill units, without requiring approval.
  await evaluate(`document.querySelector('.page-head-action button').click()`);await until(`!!document.querySelector('dialog[open] [name=main_category_id]:not(:disabled)')`);
  await pickCategory('main_category_id','Electrical');await pickCategory('subcategory_id','Tools');
  await field('dialog[open] .price-material-search input','bend');
  await until(`Array.from(document.querySelectorAll('dialog[open] .price-material-search [role=option]')).some(o=>o.innerText.includes('PVC Pipe Bender'))`);
  await evaluate(`Array.from(document.querySelectorAll('dialog[open] .price-material-search [role=option]')).find(o=>o.innerText.includes('PVC Pipe Bender')).click()`);
  assert.ok((await evaluate(`document.querySelector('dialog[open] [name=unit]').value`)).startsWith('PCS'));
  await field('dialog[open] [name=unit_price]','45.00');await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);await until(`!document.querySelector('dialog[open]')`);
  await until(`document.querySelectorAll('.supplier-prices .data-table tbody tr').length===2`);
  await evaluate(`document.querySelector('.supplier-prices .data-table tbody button').click()`);await until(`!!document.querySelector('dialog[open] [name=subcategory_id]')`);
  const capability=selections.find(c=>c.category_id===tools.id);await adminCall('capabilities/'+capability.id+'/confirm');
  await evaluate(`window.dispatchEvent(new Event('focus'))`);await until(`document.querySelector('dialog[open] [name=subcategory_id]').value.includes('Approved')`);
  await evaluate(`document.querySelector('dialog[open] .price-dialog-heading button').click()`);await until(`!document.querySelector('dialog[open]')`);
  assert.equal((await evaluate(`fetch('/api/backend/organizations/${pendingOrg.id}').then(r=>r.json())`)).verified_status,'unverified');
  let checks=0;
  for(const width of [320,375,430,768,1024,1280,1440]){
   await command('Emulation.setDeviceMetricsOverride',{width,height:960,deviceScaleFactor:1,mobile:width<768});
   for(const arabic of [false,true]){
    if(await evaluate('document.documentElement.dir')!==(arabic?'rtl':'ltr')){await evaluate(`document.querySelector('[aria-label="Switch language"]').click()`);await until(`document.documentElement.dir==='${arabic?'rtl':'ltr'}'`);}
    assert.ok(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),`Price book overflow at ${width} ${arabic}`);
    await screenshot(`price-book-table-${width}-${arabic?'ar':'en'}`);
    await evaluate(`document.querySelector('.page-head-action button').click()`);await until(`!!document.querySelector('dialog[open]')`);
    assert.ok(await evaluate(`(()=>{const r=document.querySelector('dialog[open]').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.height<=innerHeight&&document.querySelector('dialog[open]').scrollWidth<=r.width+1;})()`),`Rate form overflow at ${width} ${arabic}`);
    await screenshot(`price-book-${width}-${arabic?'ar':'en'}`);
    await pickCategory('main_category_id','Electrical');
    await evaluate(`document.querySelector('dialog[open] [name=subcategory_id]').focus();document.querySelector('dialog[open] [name=subcategory_id]').click()`);
    await until(`!!document.querySelector('dialog[open] .select-suggestions .price-capability-badge.declared')`);
    assert.ok(await evaluate(`(()=>{const list=document.querySelector('dialog[open] .select-suggestions'),r=list.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&r.height<=innerHeight*.33;})()`),`Category dropdown outside viewport at ${width} ${arabic}`);
    await screenshot(`price-book-categories-${width}-${arabic?'ar':'en'}`);
    await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    await until(`!document.querySelector('dialog[open] [role=listbox]')`);
    assert.ok(await evaluate(`!!document.querySelector('dialog[open]')`),'First Escape closes the dropdown and retains the form');
    await evaluate(`document.querySelector('dialog[open] [name=unit]').focus();document.querySelector('dialog[open] [name=unit]').click()`);
    await until(`!!document.querySelector('dialog[open] .price-unit-field [role=option]')`);
    assert.ok(await evaluate(`(()=>{const r=document.querySelector('dialog[open] .price-unit-field .select-suggestions').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;})()`),`Unit dropdown outside viewport at ${width} ${arabic}`);
    await screenshot(`price-book-units-${width}-${arabic?'ar':'en'}`);
    await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    await until(`!document.querySelector('dialog[open] [role=listbox]')`);
    await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await until(`!document.querySelector('dialog[open]')`);
    checks++;
   }
  }
  assert.deepEqual(runtimeErrors,[]);assert.deepEqual(apiFailures,[]);
  console.log(`Private price book browser checks passed: saved edit, organization switch, 13 self-declared selections, custom description without master ID, selectable unit, active private price, approval audit refresh, material search/unit autofill, ${checks} viewport/language and dropdown keyboard/overflow checks.`);
 }catch(error){try{await screenshot('price-book-browser-failure');console.error(await evaluate('document.body.innerText.slice(-3500)'));console.error(await evaluate(`JSON.stringify({focus:document.activeElement?.outerHTML?.slice(0,250),picker:document.querySelector('.price-material-search')?.innerHTML})`));}catch{}throw error;}finally{ws?.close();chrome.kill();}
}
  async function call(route,body){return evaluate(`fetch('/api/backend/${route}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(body)})}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(JSON.stringify(d));return d;})`);}
