// Runs the built app with HTTP fixtures; never connects to a real database.
import puppeteer from 'puppeteer-core';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const origin='http://127.0.0.1:13129';
const artifacts=path.resolve('.test-runtime/grouped-entry');await fs.mkdir(artifacts,{recursive:true});
let output='',browser;
const server=spawn(process.execPath,['node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','13129'],{env:{...process.env,NEXT_BUILD_DIR:'.next-grouped-entry'},windowsHide:true,stdio:['ignore','pipe','pipe']});
server.stdout.on('data',d=>output+=d);server.stderr.on('data',d=>output+=d);
const categories=[{id:'main',name_en:'Plumbing',name_ar:'السباكة',parent_id:null},{id:'pipes',name_en:'Pipes',name_ar:'الأنابيب',parent_id:'main'},{id:'fittings',name_en:'Fittings',name_ar:'الوصلات',parent_id:'main'},{id:'electrical',name_en:'Electrical materials',name_ar:'المواد الكهربائية',parent_id:null}].map(c=>({...c,code:c.id,active:true,sort_order:0}));
try{
 let ready=false;for(let i=0;i<120;i++){try{if((await fetch(origin+'/login')).ok){ready=true;break;}}catch{}await delay(250);}assert.ok(ready,output);
 browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--no-sandbox','--disable-gpu'],userDataDir:path.join(artifacts,'chrome-'+process.pid)});
 const page=await browser.newPage();await page.setViewport({width:1280,height:900});
 const saves=[],errors=[],priceSaves=[];let fail=false,role='admin';
 page.on('pageerror',error=>errors.push(error.message));await page.setRequestInterception(true);
 page.on('request',async request=>{
  const url=new URL(request.url()),index=url.pathname.indexOf('/api/v1/');if(index<0){return url.origin===origin||url.protocol==='data:'?request.continue():request.abort();}
  const route=url.pathname.slice(index+8);let data=[],status=200;
  const headers={'access-control-allow-origin':origin,'access-control-allow-credentials':'true','access-control-allow-methods':'GET,POST,OPTIONS','access-control-allow-headers':'content-type,cache-control'};
  if(request.method()==='OPTIONS')return request.respond({status:204,headers});
  if(route==='auth/me')data={id:'group-'+role,username:role,full_name:'Test User',is_admin:role==='admin',is_active:true,phone:'+966500001001',phone_verified:true,email_verified:true};
  else if(route==='organizations')data=[{id:'test-org',name:'Test Organization',is_buyer:true,is_supplier:true,is_active:true,verified_status:'verified',city:'Riyadh'}];
  else if(route==='auth/workspace')data={role,org_id:'test-org',needs_onboarding:false,destination:`/app/${role}/dashboard`,workspaces:[]};
  else if(route==='taxonomy/categories')data=categories;
  else if(route==='taxonomy/materials')data=[{id:'existing-pipe',name_en:'BCW Pipe diameter 20 mm',unit:'M',main_category_id:'main',subcategory_id:'pipes',category_id:'pipes',code:'PIPE20',active:true}];
  else if(route==='taxonomy/units')data=[{code:'M',name_en:'Meter'},{code:'PCS',name_en:'Pieces'}];
  else if(route==='taxonomy/materials/manage')data={items:[],total:0,offset:0,limit:25};
  else if(route==='taxonomy/materials/group'){
   const body=JSON.parse(request.postData());if(fail){status=503;data={detail:'Simulated interrupted save'};}else{saves.push(body);data={created:body.rows.length,reused:0,materials:[]};}
  }
  else if(route==='pricebooks/test-org/page')data={items:[],total:0,limit:25,offset:0};
  else if(route==='pricebooks/group'){priceSaves.push(JSON.parse(request.postData()));data={created:1,reused:0};}
  return request.respond({status,contentType:'application/json',headers,body:JSON.stringify(data)});
 });
 const button=async text=>{const h=await page.waitForFunction(label=>Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===label&&!b.disabled),{timeout:15000},text).catch(async error=>{throw new Error(text+' | '+await page.$eval('body',n=>n.innerText.slice(-2500))+' | '+error.message);});await h.asElement().click();await h.dispose();};
 await page.goto(origin+'/app/admin/materials',{waitUntil:'networkidle0'});await button('Add material');await page.waitForSelector('.grouped-entry');
 await page.select('.grouped-entry .form-grid select','Mechanical');await page.select('[data-maincategory]','main');
 await page.select('[data-subcategory]','pipes');await button('Paste Multiple Rows');
 await page.type('.grouped-entry textarea',Array.from({length:10},(_,i)=>`BCW Pipe diameter ${20+i} mm\tM`).join('\n'));await button('Add pasted rows');
 assert.equal(await page.$$eval('[data-description]',nodes=>nodes.length),10);
 const descriptions=await page.$$('[data-description]');await descriptions[9].click();await page.keyboard.down('Control');await page.keyboard.press('a');await page.keyboard.up('Control');await page.keyboard.type('BCW Pipe diameter 20 mm');await button('Save Group');await page.waitForSelector('.grouped-entry [role=alert]');assert.equal(saves.length,0);
 await descriptions[9].click();await page.keyboard.down('Control');await page.keyboard.press('a');await page.keyboard.up('Control');await page.keyboard.type('BCW Pipe diameter 29 mm');
 await button('Add Material Row');await page.click('[aria-label="Remove row 11"]');assert.equal(await page.$$eval('[data-description]',nodes=>nodes.length),10);
 await page.screenshot({path:path.join(artifacts,'desktop-ten-materials.png')});
 await button('Save and Next Subcategory');await page.waitForFunction(()=>document.querySelector('[data-subcategory]').value==='');
 assert.equal(saves[0].rows.length,10);assert.equal(saves[0].subcategory_id,'pipes');
 assert.equal(await page.$eval('.grouped-entry .form-grid select:nth-of-type(1)',node=>node.value).catch(()=>''),'Mechanical');
 await page.select('[data-subcategory]','fittings');await page.type('[data-description]','BCW Tee diameter 32 mm');await page.select('.grouped-material-row select','PCS');
 fail=true;await button('Save Group');await page.waitForSelector('.grouped-entry [role=alert]');assert.equal(await page.$eval('[data-description]',n=>n.value),'BCW Tee diameter 32 mm');
 await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('dialog[open]'));await button('Add material');await page.waitForFunction(()=>document.querySelector('[data-description]')?.value==='BCW Tee diameter 32 mm');
 fail=false;await button('Add Another Main Category');await page.waitForFunction(()=>document.querySelector('[data-maincategory]').value==='');assert.equal(saves.length,2);
 for(const width of [375,320]){await page.setViewport({width,height:760});const fits=await page.$eval('dialog[open]',n=>{const r=n.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1;});assert.ok(fits);await page.screenshot({path:path.join(artifacts,`mobile-${width}.png`)});}
 await page.evaluate(()=>localStorage.setItem('procura.locale','ar'));await page.reload({waitUntil:'networkidle0'});
 // Material Master action is translated by the existing locale dictionary.
 const add=await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).find(b=>/إضافة مادة/.test(b.textContent)));await add.asElement().click();
 await page.waitForSelector('.grouped-entry[dir=rtl]');await delay(350);await page.screenshot({path:path.join(artifacts,'arabic-mobile.png')});
 await page.evaluate(()=>localStorage.setItem('procura.locale','en'));role='buyer';await page.setViewport({width:1280,height:900});
 await page.goto(origin+'/app/buyer/rfqs/new',{waitUntil:'networkidle0'});
 await page.type('input[name=title]','Grouped pipe procurement');
 await page.$eval('input[name=response_deadline]',input=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2028-12-01T12:00');input.dispatchEvent(new Event('input',{bubbles:true}));});
 await button('Continue');await page.waitForSelector('.grouped-entry');await page.select('[data-maincategory]','main');await page.select('[data-subcategory]','pipes');
 await page.type('.grouped-entry input[type=search]','BCW');await button('BCW Pipe diameter 20 mm · M');
 const quantity='.grouped-entry input[type=number]';await page.click(quantity);await page.keyboard.down('Control');await page.keyboard.press('a');await page.keyboard.up('Control');await page.keyboard.type('0');await button('Save Group');await page.waitForSelector('.grouped-entry [role=alert]');assert.equal(await page.$$('.rfq-material-row').then(n=>n.length),0);
 await page.click(quantity);await page.keyboard.down('Control');await page.keyboard.press('a');await page.keyboard.up('Control');await page.keyboard.type('2.5');await button('Save Group');await page.waitForSelector('.rfq-material-row');assert.equal(await page.$$('.rfq-row-categories').then(n=>n.length),0);
 await page.reload({waitUntil:'networkidle0'});await button('Continue');await page.waitForSelector('.rfq-material-row');assert.equal(await page.$eval('.rfq-material-row input[type=number]',n=>n.value),'2.5');
 role='supplier';await page.goto(origin+'/app/supplier/price-book',{waitUntil:'networkidle0'});await button('Add materials by category');await page.waitForSelector('.grouped-entry');await page.select('[data-maincategory]','main');await page.select('[data-subcategory]','pipes');await page.type('.grouped-entry input[type=search]','BCW');await button('BCW Pipe diameter 20 mm · M');await page.type('.grouped-entry input[type=number]','25.00');await button('Save Group');await page.waitForFunction(()=>document.querySelector('.grouped-entry [role=status]')?.textContent==='Group saved.');assert.equal(priceSaves[0].rows[0].material_id,'existing-pipe');assert.equal(priceSaves[0].rows[0].org_id,'test-org');
 assert.deepEqual(errors,[]);console.log('PASS: ten rows, edit/remove, duplicate validation, next groups, failure recovery, mobile/RTL, RFQ zero quantity and restored draft, supplier master linkage.');
}finally{await browser?.close();server.kill();}
