import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
export async function browserCheck({origin,frontend,rfqId,buyerOnly=false}){
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
  await navigate('/login');await until('!!document.querySelector("input[name=identifier]")');
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('form input')).length`),2,'Login must contain identifier and password only');
  assert.equal(await evaluate(`document.querySelector('form').textContent.includes('Mobile number or username')`),false);

  await evaluate(`document.querySelector('input[name=identifier]').value='+966500009901';document.querySelector('input[name=password]').value='SmokeTestPassword-123';document.querySelector('button[type=submit]').click();`);
  await until('location.pathname === "/organizations" || location.pathname === "/app/buyer/dashboard"');
  await navigate('/app/buyer/dashboard');
  await until(`!!document.querySelector('.dashboard-rfq-name')&&document.body.innerText.includes('HTTP smoke RFQ')`);
  assert.ok(await evaluate(`document.querySelectorAll('.dashboard-metric').length>=6`),'Show the required buyer metrics');
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.dashboard-rfq-name')).color`),'rgb(17, 17, 17)');
  assert.equal(await evaluate(`getComputedStyle(document.body).fontSize`),'16px');
  assert.equal(await evaluate(`document.querySelectorAll('.dashboard-badge').length>=2`),true);
  await screenshot('buyer-dashboard-desktop');
  // Exercise the existing UI against controlled latency and HTTP failures.
  let mode='very-slow';
  intercept=async({requestId})=>{
    try{
      if(mode==='slow'||mode==='very-slow'){await delay(mode==='very-slow'?5000:1800);await command('Fetch.continueRequest',{requestId});}
      else if(mode==='offline')await command('Fetch.failRequest',{requestId,errorReason:'InternetDisconnected'});
      else await command('Fetch.fulfillRequest',{requestId,responseCode:Number(mode),responseHeaders:[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:origin},{name:'Access-Control-Allow-Credentials',value:'true'}],body:Buffer.from(JSON.stringify({detail:'Injected test failure'})).toString('base64')});
    }catch(error){runtimeErrors.push(error.message);}
  };
  await command('Fetch.enable',{patterns:[{urlPattern:'*reports/summary*',requestStage:'Request'}]});
  await evaluate(`window.__procuraHeader=document.querySelector('.app-topbar');Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Refresh')).click()`);
  await until(`!!document.querySelector('.updating-state')`);
  assert.equal(await evaluate(`!!document.querySelector('.side-nav')&&document.querySelectorAll('.dashboard-metric').length>=6`),true);
  await screenshot('buyer-background-refresh-slow');
  await until(`!document.querySelector('.updating-state')`);
  for(const failure of ['500','403','offline']){
    mode=failure;
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Refresh')).click()`);
    await until(`!!document.querySelector('.app-content [role=alert]')`);
    assert.equal(await evaluate(`window.__procuraHeader===document.querySelector('.app-topbar')&&document.querySelectorAll('.dashboard-metric').length>=6`),true);
    await screenshot('buyer-refresh-'+failure);
    mode='slow';
    await evaluate(`Array.from(document.querySelectorAll('.app-content button')).find(b=>b.textContent.includes('Try again')).click()`);
    await until(`!document.querySelector('.app-content [role=alert]')&&!document.querySelector('.updating-state')`);
  }
  await command('Fetch.disable');
  await evaluate(`document.querySelector('.side-nav a[href="/app/buyer/rfqs"]').click()`);
  await until(`location.pathname==='/app/buyer/rfqs'&&document.body.innerText.includes('HTTP smoke RFQ')`);
  assert.equal(await evaluate(`window.__procuraHeader===document.querySelector('.app-topbar')`),true,'Header DOM must persist across client navigation');
  await evaluate(`document.querySelector('.side-nav a[href="/app/buyer/dashboard"]').click()`);
  await until(`location.pathname==='/app/buyer/dashboard'&&!!document.querySelector('.dashboard-rfq-name')`);
  await command('Fetch.enable',{patterns:[{urlPattern:'*reports/summary*',requestStage:'Request'}]});
  await navigate('/app/buyer/dashboard');
  await until(`!!document.querySelector('.app-content .page-loading-state')||!!document.querySelector('.app-content .skeleton-metric')`);
  assert.equal(await evaluate(`!!document.querySelector('.app-topbar')&&!!document.querySelector('.side-nav')&&!!document.querySelector('.crumbs')`),true);
  await screenshot('buyer-dashboard-initial-skeleton');
  await until(`!!document.querySelector('.dashboard-rfq-name')`);
  await command('Fetch.disable');
  await evaluate(`fetch('/api/backend/organizations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Isolated buyer workspace',city:'Riyadh',is_buyer:true,is_supplier:false})}).then(async r=>{if(!r.ok)throw Error(await r.text());})`);
  await navigate('/app/buyer/dashboard');
  await until(`Array.from(document.querySelectorAll('[aria-label="Current organization"] option')).some(o=>o.textContent==='Isolated buyer workspace')`);
  await evaluate(`(()=>{const select=document.querySelector('[aria-label="Current organization"]');select.value=Array.from(select.options).find(o=>o.textContent==='Isolated buyer workspace').value;select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`document.querySelector('.dashboard-company strong')?.textContent==='Isolated buyer workspace'`);
  assert.equal(await evaluate(`!document.body.innerText.includes('HTTP smoke RFQ')`),true,'Organization switch must immediately remove previous records');
  await until(`document.body.innerText.includes('No RFQs yet')`);
  await screenshot('buyer-organization-isolation');
  await evaluate(`(()=>{const select=document.querySelector('[aria-label="Current organization"]');select.value=Array.from(select.options).find(o=>o.textContent==='Smoke Buyer').value;select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`!!document.querySelector('.dashboard-rfq-name')`);


  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await screenshot('buyer-dashboard-mobile');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'Buyer dashboard must fit mobile viewport');
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});

  await navigate('/app/buyer/rfqs');await until('document.body.innerText.includes("HTTP smoke RFQ")');await screenshot('buyer-rfqs-desktop');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,'Desktop page should not overflow horizontally');
  await navigate(`/app/buyer/compare?rfq=${rfqId}`);await until('document.body.innerText.includes("23.00 SAR")');await screenshot('buyer-comparison-desktop');
  await navigate('/app/buyer/rfqs/new');await until('!!document.querySelector("input[type=datetime-local]")');await screenshot('buyer-rfq-wizard');
  await evaluate(`document.querySelector('form').requestSubmit()`);
  await until(`!!document.querySelector('#rfq-title-error')`);
  await screenshot('rfq-field-validation');

  await evaluate(`(() => {
    const inputs=Array.from(document.querySelectorAll('form input'));
    const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    set.call(document.querySelector('[name=project_name]'),'Browser project P-123');document.querySelector('[name=project_name]').dispatchEvent(new Event('input',{bubbles:true}));
    set.call(document.querySelector('[name=required_delivery_date]'),new Date(Date.now()+864000000*2).toISOString().slice(0,10));document.querySelector('[name=required_delivery_date]').dispatchEvent(new Event('input',{bubbles:true}));
    for(const [input,value] of [[inputs[0],'Material selection check'],[inputs[1],'Riyadh'],[inputs[2],new Date(Date.now()+864000000).toISOString().slice(0,16)]]){
      set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));
    }
  })()`);
  await evaluate(`document.querySelector('form button[type=submit]').click()`);
  await until(`!!document.querySelector('.rfq-material-row')`);
  await evaluate(`window.dispatchEvent(new Event('procura:data-changed'))`);
  await until(`!!document.querySelector('.searchable-select input')&&!document.querySelector('.app-content .page-loading-state')`);

  await evaluate(`document.querySelector('.searchable-select input').focus();document.querySelector('.searchable-select input').click()`);
  await until(`!!document.querySelector('.searchable-select [role=option]')`);
  await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await evaluate(`(() => {const input=document.querySelector('.material-autocomplete input');input.focus();Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'va');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await until(`!!document.querySelector('.material-autocomplete [role=option]')`);
  await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  assert.equal(await evaluate(`document.querySelector('.material-autocomplete input').value`),'Valve');
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('.rfq-material-row label')).find(l=>l.firstChild.textContent==='Unit').querySelector('select').value`),'NO.');
  await evaluate(`(() => {const input=document.querySelector('.material-autocomplete input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Valve DN100 Heavy Duty - buyer custom description');input.dispatchEvent(new Event('input',{bubbles:true}));const quantity=document.querySelector('.rfq-material-row input[type=number]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(quantity,'25');quantity.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await evaluate(`window.dispatchEvent(new Event('procura:data-changed'))`);
  await delay(500);
  assert.equal(await evaluate(`document.querySelector('.material-autocomplete input').value`),'Valve DN100 Heavy Duty - buyer custom description','Lookup refetch must preserve unsaved description');
  assert.equal(await evaluate(`document.querySelector('.rfq-material-row input[type=number]').value`),'25');
  await evaluate(`document.querySelector('form button[type=submit]').click()`);
  await until(`document.body.innerText.includes('Confirm and publish')`);
  await evaluate(`document.querySelector('form button[type=submit]').click()`);
  await until(`location.pathname.includes('/app/buyer/rfqs/')&&!location.pathname.endsWith('/new')`);
  await until(`document.body.innerText.includes('Valve DN100 Heavy Duty - buyer custom description')`);
  assert.equal(await evaluate(`fetch('/api/backend/taxonomy/materials?q=Valve').then(r=>r.json()).then(rows=>rows.find(m=>m.code==='SMOKE-1').name_en)`),'Valve');
  await screenshot('buyer-material-autofill');
  const browserRfqId=await evaluate(`location.pathname.split('/').pop()`);
  assert.equal(await evaluate(`fetch('/api/backend/rfqs/${browserRfqId}').then(r=>r.json()).then(data=>data.rfq.project_name)`),'Browser project P-123');
  await until(`document.body.innerText.includes('Supplier invitations available')`);

  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await navigate('/app/buyer/rfqs');await until('document.body.innerText.includes("HTTP smoke RFQ")');await screenshot('buyer-rfqs-mobile');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,'Mobile page should not overflow horizontally');
  await evaluate('document.querySelector("[aria-label=\\"Switch language\\"]").click()');await until('document.documentElement.dir === "rtl"');await screenshot('buyer-rfqs-mobile-rtl');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,'RTL page should not overflow horizontally');
  if(buyerOnly){for(const width of [320,768,1024,1440]){
    await command('Emulation.setDeviceMetricsOverride',{width,height:960,deviceScaleFactor:1,mobile:width<768});
    await navigate('/app/buyer/dashboard');await until(`!!document.querySelector('.dashboard-metric-number')`);
    assert.equal(await evaluate('document.documentElement.dir'), 'rtl');
    assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,`Buyer RTL overflow at ${width}px`);
    await screenshot('buyer-current-rtl-'+width);
  }}
  await navigate('/suppliers');await until('document.body.innerText.includes("Smoke Supplier")');await screenshot('supplier-directory-mobile');
  await evaluate(`fetch('/api/backend/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:'+966500009902',password:'SmokeTestPassword-123'})}).then(r=>{if(!r.ok)throw Error('Supplier login failed')})`);
  await evaluate(`localStorage.setItem('procura.locale','en')`);
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await navigate('/app/supplier/dashboard');
  await until(`!!document.querySelector('.supplier-activity')&&document.body.innerText.includes('HTTP smoke RFQ')`);
  await screenshot('supplier-dashboard-current');
  assert.equal(await evaluate(`document.body.innerText.includes('Total Enquiries')&&document.body.innerText.includes('Recent RFQ Invitations')&&document.body.innerText.toLowerCase().includes('quotations submitted')`),true,await evaluate('document.body.innerText'));
  await screenshot('supplier-dashboard-desktop');
  if(!buyerOnly){
  await navigate('/app/supplier/capabilities');
  await until(`!!document.querySelector('.searchable-select input')`);
  await evaluate(`document.querySelector('.searchable-select input').click()`);
  await until(`Array.from(document.querySelectorAll('[role=option]')).some(o=>o.textContent==='Smoke category')`);
  await evaluate(`Array.from(document.querySelectorAll('[role=option]')).find(o=>o.textContent==='Smoke category').click();const input=Array.from(document.querySelectorAll('label')).find(l=>l.textContent.includes('Service city')).querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Jeddah');input.dispatchEvent(new Event('input',{bubbles:true}));`);
  await evaluate(`document.querySelector('form').requestSubmit()`);
  await until(`document.querySelector('tbody').textContent.includes('Jeddah')`);
  await screenshot('supplier-capability-saved');
  }
  await navigate('/app/supplier/inbox');
  await until(`Array.from(document.querySelectorAll('tbody tr')).some(r=>r.textContent.includes('Material selection check'))`);
  await evaluate(`Array.from(document.querySelectorAll('tbody tr')).find(r=>r.textContent.includes('Material selection check')).querySelector('button').click()`);
  await until(`Array.from(document.querySelectorAll('tbody tr')).find(r=>r.textContent.includes('Material selection check'))?.textContent.includes('Prepare draft')`);
  await evaluate(`Array.from(document.querySelectorAll('tbody tr')).find(r=>r.textContent.includes('Material selection check')).querySelector('button').click()`);
  await until(`location.pathname.includes('/app/supplier/quote/')&&!!document.querySelector('fieldset textarea')`);
  await evaluate(`(()=>{const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;const price=Array.from(document.querySelectorAll('fieldset label')).find(l=>l.textContent.includes('Unit price')).querySelector('input');set.call(price,'12.00');price.dispatchEvent(new Event('input',{bubbles:true}));const spec=document.querySelector('fieldset textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(spec,'DN100 heavy duty');spec.dispatchEvent(new Event('input',{bubbles:true}));const compliance=document.querySelector('fieldset select');compliance.value='compliant';compliance.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await evaluate(`document.querySelector('form').requestSubmit()`);
  await until(`document.body.innerText.includes('345.00 SAR')`);
  await screenshot('supplier-manual-quotation-review');
  await evaluate(`window.confirm=()=>true;Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Confirm and submit quotation')).click()`);
  await until(`document.body.innerText.includes('Status: submitted')`);
  await screenshot('supplier-manual-quotation-submitted');
  await navigate('/app/supplier/dashboard');
  await until(`!!document.querySelector('.supplier-activity')`);

  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await screenshot('supplier-dashboard-mobile');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'Supplier dashboard must fit mobile viewport');
  await evaluate(`document.querySelector('[aria-label="Switch language"]').click()`);
  await until(`document.documentElement.dir==='rtl'`);
  await screenshot('supplier-dashboard-mobile-rtl');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'Supplier RTL dashboard must fit mobile viewport');

  if(buyerOnly){
    mode='401';
    await command('Fetch.enable',{patterns:[{urlPattern:'*auth/me',requestStage:'Request'}]});
    await navigate('/app/buyer/dashboard');
    await until(`location.pathname==='/session-expired'`);
    assert.equal(await evaluate(`!document.querySelector('.app-topbar')`),true);
    await screenshot('buyer-expired-session');
    await command('Fetch.disable');
    assert.deepEqual(runtimeErrors,[],'No uncaught runtime errors during buyer/supplier workflows');
    console.log('Buyer/supplier browser checks passed: publish-once worker invitations, metadata and custom descriptions, manual quotation, tenant switching, slow/offline/403/500 recovery, 401 expiry, desktop/mobile and Arabic RTL.');
    return;
  }

  await evaluate(`fetch('/api/backend/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:'+966500009903',password:'SmokeTestPassword-123'})}).then(r=>{if(!r.ok)throw Error('Admin login failed')})`);
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate(`localStorage.setItem('procura.locale','en')`);
  await navigate('/app/admin/categories');
  await until(`document.body.innerText.includes('Materials count')`);
  assert.equal(await evaluate(`document.body.innerText.includes('Search loaded records')`),false);
  await screenshot('admin-simple-categories');
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Add main category')).click()`);
  await until(`!!document.querySelector('dialog[open] input')`);
  await screenshot('admin-add-category-popup');
  await evaluate(`(() => {const input=document.querySelector('dialog[open] input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Browser Electrical');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);
  await until(`!document.querySelector('dialog[open]')&&document.body.innerText.includes('Browser Electrical')`);
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Add subcategory')).click()`);
  await until(`!!document.querySelector('dialog[open] .searchable-select input')`);
  await evaluate(`document.querySelector('dialog[open] .searchable-select input').focus();document.querySelector('dialog[open] .searchable-select input').click()`);
  await until(`Array.from(document.querySelectorAll('dialog[open] [role=option]')).some(o=>o.textContent==='Browser Electrical')`);
  await evaluate(`Array.from(document.querySelectorAll('dialog[open] [role=option]')).find(o=>o.textContent==='Browser Electrical').click()`);
  await evaluate(`(() => {const input=Array.from(document.querySelectorAll('dialog[open] label')).find(l=>l.firstChild.textContent==='Subcategory').querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Browser Tools');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await evaluate(`(() => {const input=Array.from(document.querySelectorAll('dialog[open] label')).find(l=>l.firstChild.textContent==='Material Description').querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Browser Pvc Pipe Bender 32 Mm');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await evaluate(`(() => {const select=document.querySelector('dialog[open] .subcategory-first-material select');select.value='PCS';select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await screenshot('admin-subcategory-with-material');
  await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);
  await until(`!document.querySelector('dialog[open]')&&document.body.innerText.includes('Browser Tools')`);
  assert.equal(await evaluate(`fetch('/api/backend/taxonomy/materials?q=Browser%20Pvc').then(r=>r.json()).then(rows=>rows[0].unit)`),'PCS');
  async function openToolsForm(subcategory,description){
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Add subcategory')).click()`);
    await until(`!!document.querySelector('dialog[open] .searchable-select input')`);
    await evaluate(`document.querySelector('dialog[open] .searchable-select input').focus();document.querySelector('dialog[open] .searchable-select input').click()`);
    await until(`Array.from(document.querySelectorAll('dialog[open] [role=option]')).some(o=>o.textContent==='Browser Electrical')`);
    await evaluate(`Array.from(document.querySelectorAll('dialog[open] [role=option]')).find(o=>o.textContent==='Browser Electrical').click()`);
    await evaluate(`(() => {const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;for(const [label,value] of [['Subcategory',${JSON.stringify(subcategory)}],['Material Description',${JSON.stringify(description)}]]){const input=Array.from(document.querySelectorAll('dialog[open] label')).find(l=>l.firstChild.textContent===label).querySelector('input');set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));}})()`);
  }
  await openToolsForm('Browser Tools','Browser Wire Puller');
  await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);
  await until(`document.querySelector('dialog[open] [role=alert]')?.textContent.includes('Provide both')`);
  await evaluate(`(() => {const select=document.querySelector('dialog[open] .subcategory-first-material select');select.value='KG';select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);
  await until(`!document.querySelector('dialog[open]')`);
  assert.equal(await evaluate(`fetch('/api/backend/taxonomy/categories/manage').then(r=>r.json()).then(rows=>rows.find(c=>c.name_en==='Browser Tools').materials_count)`),2);
  assert.equal(await evaluate(`fetch('/api/backend/taxonomy/categories/manage').then(r=>r.json()).then(rows=>rows.filter(c=>c.name_en==='Browser Tools').length)`),1);
  await openToolsForm('Browser Empty Tools','');
  await evaluate(`document.querySelector('dialog[open] form').requestSubmit()`);
  await until(`!document.querySelector('dialog[open]')&&document.body.innerText.includes('Browser Empty Tools')`);
  await evaluate(`(() => {const select=document.querySelector('select[aria-label="Filter by main category"]');select.value=Array.from(select.options).find(o=>o.text==='Browser Electrical').value;select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await evaluate(`(() => {const select=document.querySelector('select[aria-label="Filter by subcategory"]');select.value=Array.from(select.options).find(o=>o.text==='Browser Tools').value;select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`document.querySelectorAll('.category-table-panel tbody tr').length===1`);
  assert.equal(await evaluate(`document.querySelector('.category-table-panel tbody tr td:nth-child(3)').textContent`),'2');
  await screenshot('admin-categories-filtered-count');
  await evaluate(`(() => {const input=document.querySelector('input[placeholder="Search by category name"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'No category matches this');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await until(`document.body.innerText.includes('No matching categories')`);
  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await navigate('/app/admin/categories');await until(`document.body.innerText.includes('Browser Tools')`);
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,'Category page must not overflow on mobile');
  await screenshot('admin-categories-mobile-readable');
  await evaluate(`localStorage.setItem('procura.locale','ar')`);
  await navigate('/app/admin/categories');await until(`document.documentElement.dir==='rtl'&&document.body.innerText.includes('Browser Tools')`);
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,'Category page must not overflow in RTL');
  await screenshot('admin-categories-mobile-rtl-readable');
  await evaluate(`localStorage.setItem('procura.locale','en')`);
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});

  await navigate('/app/admin/materials');
  await until(`document.body.innerText.includes('Browser Pvc Pipe Bender 32 Mm')&&document.body.innerText.includes('Browser Wire Puller')`);
  await until(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Add material')`);
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Add material').click()`);
  await until(`!!document.querySelector('dialog[open]')`);
  assert.deepEqual(await evaluate(`Array.from(document.querySelectorAll('dialog[open] label.field')).map(l=>l.firstChild.textContent)`),['Main category','Subcategory','Description','Unit']);
  await screenshot('admin-four-field-material-editor');
  await navigate('/app/admin/categories');
  await until('document.body.innerText.includes("Material categories")');
  const deletionFixture=await evaluate(`(async()=>{const call=async(path,body)=>{const r=await fetch('/api/backend/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error(await r.text());return r.json();};const first=await call('taxonomy/categories',{name_en:'Browser source root'});const moved=await call('taxonomy/categories',{name_en:'Browser target root'});const pair=await call('taxonomy/categories/with-material',{name_en:'Browser edit tools',parent_id:first.id,material_description:'Browser old tool',unit:'PCS'});const user=await call('admin/users',{username:'browser_delete_user',phone:'+966500009905',password:'BrowserTestPassword-123'});return {first,moved,pair,user};})()`);
  await navigate('/app/admin/categories');
  await until('document.body.innerText.includes("Browser edit tools")');
  await evaluate(`window.deleteFixture=${JSON.stringify(deletionFixture)}`);
  await evaluate(`Array.from(document.querySelectorAll('tbody tr')).find(r=>r.textContent.includes('Browser edit tools')).querySelector('button').click()`);
  await until(`document.querySelector('dialog[open] input[placeholder="e.g. Pvc Pipe Bender 32 Mm"]')?.value==='Browser old tool'`);
  assert.equal(await evaluate(`!!document.querySelector('dialog[open] input[role=combobox]')&&document.querySelector('dialog[open]').textContent.includes('Subcategory')&&document.querySelector('dialog[open]').textContent.includes('Material Description')&&document.querySelector('dialog[open]').textContent.includes('Unit')`),true);
  await screenshot('admin-complete-category-edit-popup');
  await evaluate(`(()=>{const input=document.querySelector('dialog[open] input[placeholder="e.g. Pvc Pipe Bender 32 Mm"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Browser updated tool');input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('dialog[open] input[role=combobox]').click();})()`);
  await until(`Array.from(document.querySelectorAll('dialog[open] [role=option]')).some(o=>o.textContent==='Browser target root')`);
  await evaluate(`Array.from(document.querySelectorAll('dialog[open] [role=option]')).find(o=>o.textContent==='Browser target root').click()`);
  await evaluate(`(()=>{const select=Array.from(document.querySelectorAll('dialog[open] select')).find(s=>Array.from(s.options).some(o=>o.value==='KG'));select.value='KG';select.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('dialog[open] form').requestSubmit();})()`);
  await until(`!document.querySelector('dialog[open]')`);
  assert.equal(await evaluate(`(async()=>{const f=window.deleteFixture;const r=await fetch('/api/backend/taxonomy/materials/'+f.pair.material.id);const m=await r.json();return m.name_en==='Browser updated tool'&&m.unit==='KG'&&m.main_category_id===f.moved.id;})()`),true);
  await screenshot('admin-complete-category-edit');
  await evaluate(`window.confirm=()=>true;Array.from(document.querySelectorAll('tbody tr')).find(r=>r.textContent.includes('Browser target root')&&!r.textContent.includes('Browser edit tools')).querySelectorAll('button')[2].click()`);
  await until(`!Array.from(document.querySelectorAll('tbody tr')).some(r=>r.textContent.includes('Browser target root'))`);
  assert.equal(await evaluate(`(async()=>{const f=window.deleteFixture;return (await fetch('/api/backend/taxonomy/materials/'+f.pair.material.id)).status===404;})()`),true);
  const deletedUserId=await evaluate('window.deleteFixture.user.id');
  await navigate('/app/admin/users');
  await until('document.body.innerText.includes("browser_delete_user")');
  assert.equal(await evaluate(`performance.getEntriesByType('resource').some(r=>r.name.startsWith('http://127.0.0.1:18081/api/v1/admin/users'))`),true,'User listing should call FastAPI directly');
  await evaluate(`window.confirm=()=>true;Array.from(document.querySelectorAll('tbody tr')).find(r=>r.textContent.includes('browser_delete_user')).querySelectorAll('button')[1].click()`);
  await until('!document.body.innerText.includes("browser_delete_user")');
  assert.equal(await evaluate(`(async()=> (await fetch('/api/backend/admin/users/${deletedUserId}')).status)()`),404);
  await screenshot('admin-user-permanently-deleted');

  await navigate('/register?role=supplier');
  await until(`!!document.querySelector('input[name=confirm_password]')`);
  await evaluate(`document.querySelector('form').requestSubmit()`);
  await until(`!!document.querySelector('.field-error')`);
  assert.equal(await evaluate(`document.querySelectorAll('[aria-invalid="true"]').length>0`),true);
  await evaluate(`(()=>{const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;for(const [name,value] of Object.entries({username:'browser_signup',phone:'+966501111223',email:'browser-signup@example.test',password:'123456',confirm_password:'DifferentPassword-456'})){const input=document.querySelector('input[name='+name+']');set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));}})()`);
  await evaluate(`document.querySelector('form').requestSubmit()`);
  await until(`document.querySelector('#auth-confirm_password-error')?.textContent.includes('do not match')`);
  await screenshot('registration-field-validation');

  await until(`document.querySelector('[aria-label="Account type"] button[aria-pressed="true"]')?.textContent.includes('Supplier account')`);
  await screenshot('register-supplier-account');
  await evaluate(`Array.from(document.querySelectorAll('[aria-label="Account type"] button')).find(b=>b.textContent.includes('Buyer account')).click()`);
  await until(`document.querySelector('[aria-label="Account type"] button[aria-pressed="true"]')?.textContent.includes('Buyer account')`);
  await evaluate(`(()=>{const input=document.querySelector('input[name=confirm_password]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'123456');input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await evaluate(`document.querySelector('form').requestSubmit()`);
  await until(`location.pathname==='/verify-mobile'&&!!document.querySelector('input[name=code]')`);
  await screenshot('registration-awaiting-verification');

  await navigate('/login');
  assert.equal(await evaluate(`!!document.querySelector('a[href="/register?role=buyer"]')&&!!document.querySelector('a[href="/register?role=supplier"]')`),true);
  await navigate('/');await screenshot('public-home-readable-desktop');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'Home page must fit desktop viewport');
  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await screenshot('public-home-readable-mobile');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'Home page must fit mobile viewport');

  mode='500';
  await command('Fetch.enable',{patterns:[{urlPattern:'*auth/me',requestStage:'Request'}]});
  await navigate('/app/admin/users');
  await until(`document.body.innerText.includes('Sign in to continue')&&!!document.querySelector('[role=alert]')`);
  assert.equal(await evaluate(`!document.querySelector('.app-topbar')`),true,'Unverified sessions must not expose protected workspace');
  await screenshot('auth-verification-error-retry');
  mode='slow';
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Try again')).click()`);
  await until(`!!document.querySelector('.route-loading-mark')`);
  await screenshot('auth-branded-verification');
  await until(`!!document.querySelector('.app-topbar')`);
  await command('Fetch.disable');
  mode='401';
  await command('Fetch.enable',{patterns:[{urlPattern:'*auth/me',requestStage:'Request'}]});
  await navigate('/app/admin/users');
  await until(`location.pathname==='/session-expired'`);
  assert.equal(await evaluate(`!document.querySelector('.app-topbar')`),true);
  await screenshot('auth-expired-session');
  await command('Fetch.disable');
  assert.deepEqual(runtimeErrors,[],'No uncaught runtime errors during browser workflows');
  console.log('Chrome browser checks passed: real login, buyer/supplier dashboards and readable mobile/RTL text, RFQ list, comparison, creation form, material autofill, four-field admin editor, complete category edit and move, linked-category deletion, permanent user deletion, 390px mobile, RTL shell, directory; screenshots saved locally.');
 }finally{ws?.close();chrome.kill();}
}
