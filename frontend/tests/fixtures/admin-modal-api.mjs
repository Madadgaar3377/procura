import http from 'node:http';
const requests=[];let jobMode='processing',polls=0;
const user={id:'test-admin',username:'admin',phone:'+966501234567',phone_e164:'+966501234567',email:'admin@example.test',is_admin:true,is_active:true,phone_verified:true,email_verified:true};
const company={id:'supplier-test',name:'Fixture Supplier',city:'Riyadh',country:'Saudi Arabia',cr_number:'TEST-CR',source:'imported',is_active:true,is_supplier:true,is_buyer:false,verified_status:'verified',profile_status:'complete'};
const main={id:'cat-electrical',code:'ELEC',name_en:'Electrical',name_ar:'الكهرباء',parent_id:null,active:true,normalized_name:'electrical',material_count:1,subcategory_count:1};
const sub={...main,id:'cat-panels',code:'PANELS',name_en:'Panels',parent_id:main.id};
const material={id:'material-test',code:'MAT-TEST',name_en:'Panel Board',unit:'No.',category_id:sub.id,main_category_id:main.id,subcategory_id:sub.id,active:true};
const progress=()=>({batch_id:'fixture-batch',status:jobMode,stage:jobMode==='processing'?'processing_suppliers':'complete',total_rows:50,total_supplier_records_identified:28,batch_size:10,batches_total:3,completed_batches:jobMode==='processing'?1:3,progress_percent:jobMode==='processing'?35.71:100,new_suppliers_saved:17,existing_suppliers_reused:11,contacts_created:46,contacts_updated:2,category_assignments_created:43,capabilities_saved:26,capabilities_reused:5,pending_category_mappings:jobMode==='completed'?0:3,pending_material_mappings:jobMode==='completed'?0:7,capabilities_skipped:jobMode==='completed'?0:3,supplier_failures:jobMode==='failed'?2:0,failed:jobMode==='failed'?2:0,failed_rows:jobMode==='failed'?4:0,imported:17,skipped:0,materials_created:9,categories_created:1,responsibilities_created:47,message:jobMode==='failed'?'Database temporarily unavailable. Saved progress is preserved.':null,warnings:jobMode==='completed'?[]:Array.from({length:32},(_,i)=>({row:i+2,reason:'Optional supplier field needs review '+i})),issues:jobMode==='failed'?[{row:14,reason:'Supplier could not be saved.'}]:[]});
http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://127.0.0.1'),p=url.pathname.replace('/api/v1/','');
 res.setHeader('Access-Control-Allow-Origin',req.headers.origin||'http://127.0.0.1:13097');res.setHeader('Access-Control-Allow-Credentials','true');res.setHeader('Access-Control-Allow-Headers','content-type,cache-control');res.setHeader('Access-Control-Allow-Methods','GET,POST,PUT,PATCH,DELETE,OPTIONS');
 if(req.method==='OPTIONS'){res.writeHead(200);res.end();return;}
 requests.push({method:req.method,path:p});
 if(p==='__test/state'){jobMode=url.searchParams.get('mode')||'processing';polls=0;if(url.searchParams.has('reset'))requests.length=0;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({requests}));return;}
 if(p==='__test/requests'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(requests));return;}
 let data=[];
 if(p==='auth/me')data=user;
 else if(p==='organizations')data=[];
 else if(p==='organizations/admin/suppliers')data=[company];
 else if(p==='organizations/admin/suppliers/supplier-test')data={company,contacts:[],status:{profile_status:'complete',verification_status:'verified',source:'imported',onboarding_status:'complete',last_updated_at:'2026-11-11'},capabilities:[],pending_category_mappings:[],documents:[]};
 else if(p==='imports/bulk/suppliers/jobs'&&req.method==='POST'){polls=0;data=progress();data.status='queued';data.progress_percent=0;res.statusCode=202;}
 else if(p==='imports/bulk/suppliers/jobs/fixture-batch/retry'){jobMode='processing';data=progress();res.statusCode=202;}
 else if(p==='imports/bulk/suppliers/jobs/fixture-batch'){polls++;data=progress();if(jobMode==='unknown'){data.status='processing';delete data.progress_percent;}}
 else if(p==='taxonomy/categories/manage')data=[main,sub].map(c=>({...c,materials_count:1,subcategories_count:1}));
 else if(p==='taxonomy/categories')data=url.searchParams.has('parent_id')?[sub]:[main,sub];
 else if(p==='taxonomy/units')data=[{code:'No.',name_en:'Number'}];
 else if(p==='taxonomy/materials/manage')data={items:[material],total:1};
 else if(p==='taxonomy/materials')data=[material];
 else if(p==='admin/users')data={items:[user],total:1};
 else if(p==='memberships/admin/plan-page')data={items:[],total:0};
 else if(p==='capabilities/admin/pending')data=[];
 else if(/template$|export$|warnings$|errors$/.test(p)){res.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');await new Promise(r=>setTimeout(r,350));res.end('fixture-excel-download');return;}
 else if(req.method!=='GET')data={saved:true};
 res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
}).listen(18084,'127.0.0.1',()=>console.log('Read/write-free admin UI fixture on loopback 18084'));
