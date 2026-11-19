// Browser contract test against the real built UI with deterministic HTTP fixtures.
// No real account, database, supplier or storage is accessed.
import puppeteer from 'puppeteer-core';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const origin='http://127.0.0.1:13127';
const artifacts=path.resolve('.test-runtime/material-bulk');await fs.mkdir(artifacts,{recursive:true});
let output='',browser;
const server=spawn(process.execPath,['node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','13127'],{cwd:process.cwd(),windowsHide:true,stdio:['ignore','pipe','pipe']});
server.stdout.on('data',d=>output+=d);server.stderr.on('data',d=>output+=d);
try{
 let ready=false;for(let i=0;i<80;i++){try{if((await fetch(origin+'/login')).ok){ready=true;break;}}catch{}await delay(250);}assert.ok(ready,output);
 browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--no-sandbox','--disable-gpu','--disable-background-networking'],userDataDir:path.join(artifacts,'chrome-'+process.pid)});
 const page=await browser.newPage();await page.setViewport({width:1280,height:900});
 let job,uploads=0,confirms=0,complete=false;const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.setRequestInterception(true);
 page.on('request',async request=>{
  const url=new URL(request.url()),apiIndex=url.pathname.indexOf('/api/v1/');
  if(apiIndex<0){if(url.origin===origin||url.protocol==='data:')await request.continue();else await request.abort();return;}
  const route=url.pathname.slice(apiIndex+8);let data=[],status=200;
  const headers={'access-control-allow-origin':origin,'access-control-allow-credentials':'true','access-control-allow-methods':'GET,POST,OPTIONS','access-control-allow-headers':'content-type,cache-control'};
  if(request.method()==='OPTIONS'){await request.respond({status:204,headers});return;}
  if(route==='auth/me')data={id:'admin-browser',username:'Admin',full_name:'Test Admin',is_admin:true,is_active:true,phone:'+966500001001',phone_verified:true,email_verified:true};
  else if(route==='taxonomy/categories/manage')data=[{id:'main',code:'MAIN',name_en:'Electrical',name_ar:'',parent_id:null,active:true,materials_count:0,sort_order:0}];
  else if(route==='taxonomy/units')data=[{code:'PCS',name_en:'Pieces'}];
  else if(route==='imports/materials/recent')data=job?[job]:[];
  else if(route==='imports/materials/preview'){
   uploads++;status=202;
   job={batch_id:'browser-job',status:'preview',stage:'preview',rows:1310,valid:1310,create:1310,errors:0,completed_rows:0,progress_percent:0,current_job:1,jobs_total:5,current_batch:1,batches_in_job:10,new_categories:[{path:'Electrical / Accessories',depth:2}],existing_categories_reused:1,sample:[{row:2,category:'Electrical',subcategory:'Accessories',name:'Fixture material',unit:'PCS',status:'create'}]};
   data={...job,status:'uploaded',stage:'parse'};
  }else if(route==='imports/materials/browser-job/confirm'){
   confirms++;job={...job,status:'queued',stage:'apply',completed_rows:390,current_job:2,current_batch:4,progress_percent:29,created:390};data=job;
  }else if(route==='imports/materials/browser-job'){
   if(complete&&job?.stage==='apply')job={...job,status:'completed',stage:'complete',completed_rows:1310,created:1310,progress_percent:100,current_job:5,current_batch:4,batches_in_job:4};data=job;
  }
  await request.respond({status,contentType:'application/json',headers,body:JSON.stringify(data)});
 });
 const button=async text=>{
  const handle=await page.waitForFunction(label=>Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===label&&!b.disabled),{timeout:20000},text);
  await handle.asElement().click();await handle.dispose();
 };
 await page.goto(origin+'/app/admin/categories',{waitUntil:'networkidle0'});
 await button('Bulk Upload');await page.waitForSelector('dialog[open] input[type=file]');
 const file=path.join(artifacts,'fixture.xlsx');await fs.writeFile(file,'browser upload fixture (backend parsing tested separately)');
 await (await page.$('dialog[open] input[type=file]')).uploadFile(file);
 await button('Preview Import');await button('Import Materials');
 await page.waitForFunction(()=>document.querySelector('dialog[open]')?.textContent.includes('390 / 1310'));
 assert.equal(uploads,1);assert.equal(confirms,1);
 await page.screenshot({path:path.join(artifacts,'desktop-progress.png')});
 await button('Close');await page.waitForFunction(()=>!document.querySelector('dialog[open]'));
 await page.reload({waitUntil:'networkidle0'});await button('Bulk Upload');
 await page.waitForFunction(()=>document.querySelector('dialog[open]')?.textContent.includes('390 / 1310'));
 assert.equal(uploads,1,'refresh recovers the saved job without another upload');
 complete=true;
 await page.waitForFunction(()=>document.querySelector('dialog[open]')?.textContent.includes('1310 / 1310'),{timeout:10000});
 for(const width of [375,320]){
  await page.setViewport({width,height:760});await delay(150);
  const dimensions=await page.evaluate(()=>{const d=document.querySelector('dialog[open]'),r=d.getBoundingClientRect();return {left:r.left,right:r.right,width:innerWidth,footer:d.querySelector('.admin-modal-footer').getBoundingClientRect().width,scroll:d.querySelector('.admin-modal-body').scrollHeight};});
  assert.ok(dimensions.left>=-1&&dimensions.right<=dimensions.width+1,JSON.stringify(dimensions));
  assert.ok(dimensions.footer<=width);await page.screenshot({path:path.join(artifacts,`mobile-${width}.png`)});
 }
 await page.evaluate(()=>localStorage.setItem('procura.locale','ar'));
 await page.reload({waitUntil:'networkidle0'});
 const label=String.fromCodePoint(0x631,0x641,0x639,0x20,0x62c,0x645,0x627,0x639,0x64a);
 await button(label);await page.waitForSelector('dialog[open]');
 assert.equal(await page.$eval('dialog[open]',d=>d.dir),'rtl');
 const arabicText=await page.$eval('dialog[open]',d=>d.textContent);assert.ok(!arabicText.includes('Choose Excel File'));assert.ok(!arabicText.includes('Current job'));
 await page.screenshot({path:path.join(artifacts,'arabic-mobile.png')});
 await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('dialog[open]'));
 assert.deepEqual(errors,[]);
 console.log('PASS: upload once, preview/confirm, saved progress after close/refresh, mobile 320/375, Arabic RTL, Escape dismissal; real UI with HTTP fixtures.');
}finally{await browser?.close();server.kill();}
