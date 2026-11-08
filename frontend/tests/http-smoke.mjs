import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const frontend=fileURLToPath(new URL('../',import.meta.url));
const backend=fileURLToPath(new URL('../../backend/',import.meta.url));
const webPort=process.env.SMOKE_FRONTEND_PORT||'13081';
const apiPort=process.env.SMOKE_BACKEND_PORT||'18081';
const origin=`http://127.0.0.1:${webPort}`;
const backendOrigin=`http://127.0.0.1:${apiPort}`;
const env={...process.env,CORS_ORIGINS:origin,NEXT_BUILD_DIR:process.env.NEXT_BUILD_DIR||'.next-integration',BACKEND_URL:backendOrigin,NEXT_TELEMETRY_DISABLED:'1',AUTH_DELIVERY_AUTO_DISPATCH:'false'};
const children=[];
function start(command,args,cwd){const p=spawn(command,args,{cwd,env,stdio:['ignore','pipe','pipe'],windowsHide:true});let output='';p.stdout.on('data',d=>output+=d);p.stderr.on('data',d=>output+=d);p.on('error',e=>console.error(e.message));children.push(p);return ()=>output;}
async function ready(url){for(let i=0;i<120;i++){try{if((await fetch(url,{signal:AbortSignal.timeout(3000)})).ok)return;}catch{}await delay(250);}throw new Error('Local test server did not start: '+url);}
let apiOutput,webOutput;
try{
 apiOutput=start('python',['tests/local_http_server.py'],backend);
 webOutput=start(process.execPath,['node_modules/next/dist/bin/next',process.env.SMOKE_NEXT_DEV==='true'?'dev':'start','--hostname','127.0.0.1','--port',webPort],frontend);
 await Promise.all([ready(backendOrigin+'/health/live'),ready(origin+'/login')]);
 async function call(route,{cookie='',body,method=body?'POST':'GET',requestOrigin=origin}={}){
  const r=await fetch(origin+'/api/backend/'+route,{method,signal:AbortSignal.timeout(20000),headers:{origin:requestOrigin,...(cookie?{cookie}:{}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  const data=await r.json();return {r,data};
 }
 const sessions={};
 for(const role of ['buyer','supplier','admin']){
  const {r,data}=await call('auth/login',{body:{identifier:({buyer:'+966500009901',supplier:'+966500009902',admin:'+966500009903'})[role],password:'SmokeTestPassword-123'}});assert.equal(r.status,200,JSON.stringify(data));assert.deepEqual(data,{authenticated:true});
  const values=r.headers.getSetCookie();assert.ok(values.every(c=>c.includes('HttpOnly')));sessions[role]=values.map(c=>c.split(';')[0]).join('; ');
 }
 const b=options=>({...options,cookie:sessions.buyer}),s=options=>({...options,cookie:sessions.supplier}),a=options=>({...options,cookie:sessions.admin});
 assert.equal((await call('admin/dashboard',b({}))).r.status,403);
 assert.equal((await call('organizations',b({body:{name:'Smoke Buyer',city:'Riyadh',is_buyer:true,is_supplier:false}}))).r.status,201);
 const supplier=(await call('organizations',s({body:{name:'Smoke Supplier',city:'Riyadh',is_buyer:false,is_supplier:true}}))).data;
 const buyer=(await call('organizations',b({}))).data[0];
 assert.equal((await call('organizations/'+supplier.id,b({}))).r.status,404);
 const category=(await call('taxonomy/categories',a({body:{code:'SMOKE','name_en':'Smoke category'}}))).data;
 const material=(await call('taxonomy/materials',a({body:{code:'SMOKE-1',category_id:category.id,name_en:'Valve',unit:'No.'}}))).data;
 const verification=(await call(`organizations/${supplier.id}/verification`,s({body:{cr_number:'1000099991'}}))).data;
 const approval=await call(`organizations/admin/review/verification/${verification.id}`,a({body:{approve:true,notes:'Isolated smoke test manual attestation'}}));assert.equal(approval.r.status,200,JSON.stringify(approval.data));
 const date=new Date(Date.now()+86400000*10).toISOString();
 const rate=(await call('pricebooks',s({body:{org_id:supplier.id,material_id:material.id,brand:'Test',model:'Test',offered_specs:'DN100',unit_price:'10.00',lead_days:7,valid_until:date}})));
 assert.equal(rate.r.status,201);assert.equal(rate.data.capability_status,'pending');
 assert.equal((await call(`pricebooks/${supplier.id}`,b({}))).r.status,404);
 const pendingCapabilities=(await call('capabilities/admin/pending',a({}))).data;
 const capability=pendingCapabilities.find(row=>row.org_id===supplier.id&&row.category_id===category.id);
 assert.ok(capability,'Saving a private rate creates a pending capability');assert.deepEqual(capability.related_materials,['Valve']);
 assert.equal((await call(`directory/suppliers/${supplier.id}?include_profile=true`,{})).data.capability_groups.length,0);
 assert.equal((await call(`capabilities/admin/${capability.id}/review`,a({body:{approve:true}}))).r.status,200);
 const publishedProfile=(await call(`directory/suppliers/${supplier.id}?include_profile=true`,{})).data;
 assert.equal(publishedProfile.capability_groups[0].id,category.id);
 assert.equal((await call(`directory/suppliers?main_category_id=${category.id}`,{})).data.some(row=>row.id===supplier.id),true);
 assert.equal((await call(`pricebooks/${supplier.id}`,b({}))).r.status,404);
 const rfq=(await call('rfqs',b({body:{buyer_org_id:buyer.id,title:'HTTP smoke RFQ',city:'Riyadh',response_deadline:date,items:[{material_id:material.id,description:'Valve DN100',specifications:'DN100',quantity:'2',unit:'No.'}]}}))).data;
 assert.equal((await call(`rfqs/${rfq.rfq.id}/publish`,b({method:'POST'}))).r.status,200);
 // Publish once: the isolated server runs the real outbox worker automatically.
 let matching;
 for(let attempt=0;attempt<80;attempt++){
  matching=(await call(`matching/rfqs/${rfq.rfq.id}/results`,b({}))).data;
  if(matching.invited_suppliers===1)break;
  await delay(100);
 }
 assert.equal(matching.invited_suppliers,1,'Publish alone must result in one supplier invitation');
 const invitation=matching.invitations[0];
 assert.equal((await call(`matching/invitations/${invitation.id}/accept`,s({method:'POST'}))).r.status,200);
 const draft=(await call(`quotations/invitations/${invitation.id}/auto-draft`,s({method:'POST'}))).data;
 assert.equal((await call(`quotations/${draft.quotation.id}`,b({}))).r.status,404);
 assert.equal((await call(`quotations/${draft.quotation.id}/submit`,s({method:'POST'}))).r.status,200);
 const comparison=(await call(`quotations/rfqs/${rfq.rfq.id}/comparison`,b({}))).data;
 assert.equal(comparison.quotations[0].totals.total,'23.00');
 const rejectedCategory=(await call('taxonomy/categories',a({body:{code:'SMOKE-REJECTED',name_en:'Rejected category'}}))).data;
 const rejectedMaterial=(await call('taxonomy/materials',a({body:{code:'SMOKE-REJECTED-1',category_id:rejectedCategory.id,name_en:'Private rejected material',unit:'No.'}}))).data;
 const rejectedRate=await call('pricebooks',s({body:{org_id:supplier.id,material_id:rejectedMaterial.id,unit_price:'20.00',lead_days:7,valid_until:date}}));assert.equal(rejectedRate.r.status,201);
 const rejectedCapability=(await call('capabilities/admin/pending',a({}))).data.find(row=>row.org_id===supplier.id&&row.category_id===rejectedCategory.id);
 assert.ok(rejectedCapability);
 assert.equal((await call(`capabilities/admin/${rejectedCapability.id}/review`,a({body:{approve:false,rejection_reason:'Insufficient category evidence'}}))).r.status,200);
 const supplierRates=(await call(`pricebooks/${supplier.id}`,s({}))).data;assert.equal(supplierRates.length,2,'Rejected capabilities retain their private price book items');
 const afterRejection=(await call(`directory/suppliers/${supplier.id}?include_profile=true`,{})).data;
 assert.equal(afterRejection.capability_groups.some(group=>group.id===rejectedCategory.id),false);
 const buyerReport=(await call('reports/summary?workspace=buyer',b({}))).data;
 assert.equal(buyerReport.rfqs_created,1);assert.equal(buyerReport.invitations,1);assert.equal(buyerReport.quotations_submitted,1);assert.ok(buyerReport.activity.length>=5);
 const selectedBuyerReport=(await call(`reports/summary?workspace=buyer&org_id=${buyer.id}`,b({}))).data;
 assert.equal(selectedBuyerReport.rfqs_created,1);
 assert.equal((await call(`reports/summary?workspace=buyer&org_id=${buyer.id}`,s({}))).r.status,404);
 const supplierReport=(await call('reports/summary?workspace=supplier',s({}))).data;
 assert.equal(supplierReport.invitations,1);assert.equal(supplierReport.quotations_submitted,1);
 assert.equal((await call('reports/summary?workspace=admin',b({}))).r.status,403);
 const adminReport=(await call('reports/summary?workspace=admin',a({}))).data;assert.equal(adminReport.rfqs_created,1);
 const enquiry=(await call('contact',{body:{name:'Smoke Contact',email:'contact@example.sa',company:'Smoke Company',topic:'buyer',message:'Please tell us how to prepare a procurement workspace.',consent:true}}));
 assert.equal(enquiry.r.status,201);assert.equal(Object.keys(enquiry.data).sort().join(','),'message,reference');
 assert.equal((await call('contact/admin/inquiries',b({}))).r.status,403);
 const contactQueue=(await call('contact/admin/inquiries',a({}))).data;assert.equal(contactQueue.length,1);assert.equal(contactQueue[0].id,enquiry.data.reference);
 assert.equal((await call(`contact/admin/inquiries/${enquiry.data.reference}`,a({method:'PATCH',body:{status:'reviewed'}}))).r.status,200);
 assert.equal((await call('auth/logout',b({method:'POST',requestOrigin:'https://foreign.example'}))).r.status,403);
 console.log('HTTP procurement assertions passed.');
 if(process.argv.includes('--rtl-browser')||process.argv.includes('--rtl-before')){const {browserCheck}=await import('./rtl-browser-check.mjs');await browserCheck({origin,frontend,rfqId:rfq.rfq.id,quoteId:draft.quotation.id,before:process.argv.includes('--rtl-before')});}
 if(process.argv.includes('--price-book-browser')){const {browserCheck}=await import('./price-book-browser-check.mjs');await browserCheck({origin,frontend,rfqId:rfq.rfq.id,buyerOnly:process.argv.includes('--buyer-browser')});}
 if(process.argv.includes('--vat-browser')){const {browserCheck}=await import('./vat-browser-check.mjs');await browserCheck({origin,frontend});}
 if(process.argv.includes('--profile-browser')){const {browserCheck}=await import('./profile-browser-check.mjs');await browserCheck({origin,frontend,rfqId:rfq.rfq.id,buyerOnly:process.argv.includes('--buyer-browser')});}
 if(process.argv.includes('--browser')||process.argv.includes('--buyer-browser')){const {browserCheck}=await import('./browser-check.mjs');await browserCheck({origin,frontend,rfqId:rfq.rfq.id,buyerOnly:process.argv.includes('--buyer-browser')});}
 assert.equal((await call('auth/logout',b({method:'POST'}))).r.status,200);
 for(const route of ['/','/login','/register','/contact','/suppliers','/pricing','/app/buyer/dashboard','/app/buyer/reports','/app/supplier/reports','/app/admin/reports','/app/admin/contact-inquiries','/app/admin/materials'])assert.equal((await fetch(origin+route)).status,200);
 console.log('HTTP smoke passed: real Next.js proxy -> isolated FastAPI -> disposable SQLite; buyer/supplier/admin, RFQ-to-comparison, tenant denials, cookie redaction and CSRF. No external delivery.');
}catch(e){console.error(e);if(apiOutput)console.error(apiOutput());if(webOutput)console.error(webOutput());process.exitCode=1;}
finally{for(const p of children)p.kill();}
