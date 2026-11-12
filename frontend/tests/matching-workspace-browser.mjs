// Exercises the actual production app with isolated API fixtures. No live data.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';

const origin=process.env.MATCHING_TEST_ORIGIN||'http://127.0.0.1:13098';
const artifacts=path.resolve('.test-runtime/matching-workspace');
await fs.mkdir(artifacts,{recursive:true});
const port=24918;
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',[
 '--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking',
 '--no-first-run','--no-default-browser-check',`--remote-debugging-port=${port}`,
 `--user-data-dir=${path.join(artifacts,'chrome-'+process.pid)}`,'about:blank',
],{windowsHide:true,stdio:'ignore'});
let ws;
try{
 let target;
 for(let i=0;i<80;i++){try{target=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(p=>p.type==='page');if(target)break;}catch{}await delay(200);}
 assert.ok(target,'Headless Chrome starts');
 ws=new WebSocket(target.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 let sequence=0;const pending=new Map(),errors=[];
 ws.addEventListener('message',event=>{const data=JSON.parse(event.data);
  if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails.exception?.description||data.params.exceptionDetails.text);
  if(data.method==='Runtime.consoleAPICalled'&&data.params.type==='error')errors.push(data.params.args.map(a=>a.value||a.description).join(' '));
  if(data.id){const call=pending.get(data.id);pending.delete(data.id);data.error?call?.reject(Error(data.error.message)):call?.resolve(data.result);}
 });
 function command(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>reject(Error(method+' timed out')),20000);pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});ws.send(JSON.stringify({id,method,params}));});}
 async function evaluate(expression){const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;}
 async function until(expression){for(let i=0;i<150;i++){if(await evaluate(`Boolean(${expression})`))return;await delay(100);}throw Error('Timed out: '+expression+' '+await evaluate('document.body.innerText.slice(-1800)'));}
 await command('Page.enable');await command('Runtime.enable');await command('Network.enable');
 await command('Page.addScriptToEvaluateOnNewDocument',{source:`(${function(){
  const native=window.fetch.bind(window);
  window.matchingRequests=[];window.matchingWrites=[];window.matchingProgress='invitations_available';
  const nativeInterval=window.setInterval.bind(window);
  window.setInterval=(callback,ms,...args)=>{if(ms===5000)window.matchingPoll=callback;return nativeInterval(callback,ms,...args);};
  const rfqs=Array.from({length:28},(_,i)=>({id:'r'+(i+1),reference:'RFQ-'+String(i+1).padStart(3,'0'),
   title:'Distribution panel procurement',project_name:i===0?'Riyadh Business Park':'Project '+(i+1),city:i===0?'Riyadh':'Jeddah',
   status:'published',buyer_org_id:'buyer',response_deadline:'2027-01-01T00:00:00Z',created_at:'2026-11-01T00:00:00Z'}));
  let favorites=Array.from({length:30},(_,i)=>({supplier_org_id:i===0?'s0':'saved'+i,name:i===0?'Atlas Electrical Solutions':'Saved Supplier '+i}));
  let reminderAvailable=null;
  window.fetch=async(input,options={})=>{
   const url=new URL(String(input),location.href);
   if(!url.pathname.includes('/api/v1/'))return native(input,options);
   const route=url.pathname.split('/api/v1/')[1],method=options.method||'GET';
   window.matchingRequests.push(route+'?'+url.searchParams);
   if(method!=='GET')window.matchingWrites.push({route,method,body:options.body});
   const start=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||25);
   let result=[];
   if(route==='auth/me')result={id:'qa-buyer',username:'Buyer QA',is_admin:false,is_active:true,phone_verified:true,email_verified:true,phone:'',email:null};
   else if(route==='organizations')result=[{id:'buyer',name:'Northline Projects',is_buyer:true,is_supplier:false,is_active:true,verified_status:'verified',membership_role:'owner'}];
   else if(route==='rfqs')result=rfqs.filter(r=>(r.reference+' '+r.project_name).toLowerCase().includes((url.searchParams.get('q')||'').toLowerCase())).slice(start,start+limit);
   else if(/^rfqs\/r\d+$/.test(route)){const rfq=rfqs.find(r=>r.id===route.split('/')[1]);result={rfq,items:[{id:'item',main_category_id:'electrical',subcategory_id:'panels',description:'Distribution panel',quantity:'12',unit:'EA'}],attachments:[]};}
   else if(route==='taxonomy/categories')result=[{id:'electrical',name_en:'Electrical',name_ar:'كهرباء'},{id:'panels',name_en:'Distribution panels',name_ar:'لوحات توزيع',parent_id:'electrical'}];
   else if(route.startsWith('organizations/buyer/favorites')){
    const supplier=route.split('/')[3];
    if(method==='DELETE')favorites=favorites.filter(row=>row.supplier_org_id!==supplier);
    if(method==='PUT'&&!favorites.some(row=>row.supplier_org_id===supplier))favorites.unshift({supplier_org_id:supplier,name:'Saved Supplier'});
    result=method==='GET'?favorites.slice(start,start+limit):{supplier_org_id:supplier,favorite:method==='PUT'};
   }
   else if(route.endsWith('/reminders')){reminderAvailable=new Date(Date.now()+86400000).toISOString();result={created:1};}
   else if(route.startsWith('matching/rfqs/')){
    const first=route.split('/')[2]==='r1';
    const matches=Array.from({length:first?60:0},(_,i)=>({id:'m'+i,supplier_org_id:'s'+i,
     supplier_name:i===0?'Atlas Electrical Solutions':i===1?'Gulf Industrial Supply':i===2?'Al Noor Trading & Contracting':'Evaluated Supplier '+i,
     status:i<3?'eligible':i%2?'location_mismatch':'pending_approval',verification_status:'verified',
     service_coverage_status:i<3?'covered':i%2?'not covered':'unknown',
     match_levels:i===0?['exact material']:i===1?['exact subcategory']:[],
     reasons:i===2?['Approved main category capability in Electrical']:[],matched_categories:['Distribution panels'],
     covered_items:['Distribution panel'],invitation_status:i===0?'accepted':i<3?'invited':null,
    })).slice(start,start+limit);
    const invitations=Array.from({length:first?31:0},(_,i)=>({id:'inv'+i,supplier_org_id:'s'+i,
     supplier_name:i===0?'Atlas Electrical Solutions':'Invited Supplier '+i,status:i===0?'accepted':i===1?'declined':'invited',
     quotation_status:i===0?'submitted':null,reminder_eligible:i>1,invited_at:'2026-11-11T10:00:00Z'})).slice(start,start+limit);
    result={matches,invitations,progress:first?window.matchingProgress:'no_eligible_suppliers',matched_suppliers:first?3:0,
     invited_suppliers:first?31:0,suppliers_evaluated:first?60:0,pending_suppliers:first?29:0,
     pending_invitation_ids:invitations.filter(i=>i.reminder_eligible).map(i=>i.id),reminder_available_at:reminderAvailable,limit,offset:start};
   }
   return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
  };
 }.toString()})();`});
 const geometry=[];
 for(const width of [320,375,430,768,1024,1440,1920]){
  await command('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:width<768});
  for(const lang of ['en','ar']){
   await command('Network.setCookie',{name:'procura.locale',value:lang,url:origin,path:'/'});
   await command('Page.navigate',{url:origin+'/app/buyer/matches?rfq=r1'});
   await until(`document.querySelectorAll('article[data-recommended="true"]').length===3`);
   await delay(200);
   const metrics=await evaluate(`({width:innerWidth,scroll:document.documentElement.scrollWidth,dir:document.documentElement.dir,
    heading:document.querySelector('h1')?.textContent,crumbs:document.querySelectorAll('.crumbs').length,
    summary:document.querySelector('[aria-label="${lang==='ar'?'ملخص الطلب':'RFQ summary'}"]').children.length,
    otherOpen:[...document.querySelectorAll('details')].find(d=>d.querySelector('article[data-recommended="false"]'))?.open,
    stars:document.querySelectorAll('article[data-recommended="true"] button').length})`);
   assert.ok(metrics.scroll<=width+1,JSON.stringify({width,lang,...metrics}));assert.equal(metrics.dir,lang==='ar'?'rtl':'ltr');
   assert.equal(metrics.crumbs,1);assert.equal(metrics.summary,3);assert.equal(metrics.otherOpen,false);assert.equal(metrics.stars,3);
   if(width===1440||width===375){const screenshot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(artifacts,`${width}-${lang}.png`),Buffer.from(screenshot.data,'base64'));}
   geometry.push({width,lang,...metrics});
  }
 }
 // Test interactions in English at desktop width.
 await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
 await command('Network.setCookie',{name:'procura.locale',value:'en',url:origin,path:'/'});
 await command('Page.navigate',{url:origin+'/app/buyer/matches?rfq=r1'});
 await until(`document.querySelectorAll('article[data-recommended="true"]').length===3`);
 const click=label=>evaluate(`(()=>{const node=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)});if(!node)throw Error('Missing button');node.click();})()`);
 await evaluate(`document.querySelector('article button').click()`);
 await until(`window.matchingWrites.some(row=>row.method==='DELETE'&&row.route.endsWith('/s0'))`);
 await until(`!document.querySelector('article button').disabled`);
 await evaluate(`document.querySelector('article button').click()`);
 await until(`window.matchingWrites.some(row=>row.method==='PUT'&&row.route.endsWith('/s0'))`);
 await click('Favorites');await until(`document.querySelector('[aria-label="Favorite supplier pages"]')`);
 await evaluate(`document.querySelector('[aria-label="Favorite supplier pages"] button:last-child').click()`);
 await until(`window.matchingRequests.some(route=>route.includes('favorites?')&&route.includes('offset=25'))`);
 await click('Recommendations');
 await evaluate(`document.querySelector('[aria-label="Evaluated supplier pages"] button:last-child').click()`);
 await until(`window.matchingRequests.some(route=>route.includes('/results?')&&route.includes('offset=25'))`);
 await evaluate(`document.querySelector('[aria-label="Evaluated supplier pages"] button:first-of-type').click()`);
 await until(`document.querySelectorAll('article[data-recommended="true"]').length===3`);
 await evaluate(`(()=>{const d=[...document.querySelectorAll('details')].find(d=>d.querySelector('input[type="checkbox"]'));d.open=true;d.querySelector('input[type="checkbox"]:not(:disabled)').click();})()`);
 await click('Remind selected suppliers');
 await until(`window.matchingWrites.some(row=>row.route==='rfqs/r1/reminders')`);
 await until(`document.body.innerText.includes('Next reminder available')`);
 assert.equal(await evaluate(`document.querySelectorAll('input[type="checkbox"]:not(:disabled)').length`),0);
 assert.equal(await evaluate(`document.querySelector('a[href="/app/buyer/compare?rfq=r1"]')!==null`),true);
 await evaluate(`window.matchingProgress='no_eligible_suppliers'`);await click('Refresh');
 await until(`document.querySelector('[role="alert"]')?.textContent.includes('recorded results disagree')`);
 await evaluate(`window.matchingProgress='matching_in_progress'`);await click('Refresh');
 await until(`document.querySelector('[data-state="processing"]')`);
 await evaluate(`(()=>{const original=Date.now;Date.now=()=>original()+91000;window.matchingPoll();})()`);
 await until(`document.querySelector('[data-state="review"]')&&document.body.innerText.includes('Automatic refresh is paused')`);
 await evaluate(`window.matchingProgress='invitations_available'`);await click('Refresh');
 await until(`document.querySelector('[data-state="completed"]')`);
 await evaluate(`(()=>{const select=document.querySelector('#matching-rfq');select.value='r2';select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
 await until(`location.search.includes('rfq=r2')&&window.matchingRequests.some(r=>r.startsWith('rfqs/r2?'))`);
 assert.equal(await evaluate(`document.querySelectorAll('article[data-recommended="true"]').length`),0);
 assert.deepEqual(errors,[],'No browser runtime or console errors');
 await fs.writeFile(path.join(artifacts,'results.json'),JSON.stringify({geometry,interactions:'RFQ selection, favorites PUT/DELETE/paging, evaluated paging, reminder/cooldown, quotation link, inconsistent/stalled/completed status',errors},null,2));
 console.log(`Passed ${geometry.length} viewport/language combinations and all matching interactions.`);
}finally{ws?.close();chrome.kill();}
