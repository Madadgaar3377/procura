const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const ts=require('typescript');

function compile(filename, dependencies){
 const exports={}; const localRequire=createRequire(filename);
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 vm.runInNewContext(source,{exports,require:name=>dependencies[name]||localRequire(name),URL,URLSearchParams,Date,Intl,Set,Map,Object,Number,Math,Array,String,console});
 return exports;
}
const rfqsDir=path.resolve(__dirname,'../features/rfqs');
const model=compile(path.join(rfqsDir,'matching-model.ts'),{});
const copy=compile(path.join(rfqsDir,'matching-copy.ts'),{});
const fixture={matches:[
 {id:'m1',supplier_org_id:'s1',supplier_name:'Acme Materials',status:'eligible',verification_status:'verified',service_coverage_status:'covered',match_strength:'exact',match_levels:['exact material'],matched_categories:['Cables'],covered_items:['Copper cable'],matched_item_count:2,
  matched_items:[{item_id:'it1',description:'Copper cable 4mm',match_level:'exact material',discipline:'Electrical',main_category:'Electrical',subcategory:'Cables'},{item_id:'it2',description:'Cable tray',match_level:'product family',discipline:'Electrical',main_category:'Electrical',subcategory:'Trays'}],
  invitation_status:'accepted',quotation_status:'submitted',profile_url:'/suppliers/s1',saved_price_available:true,email_status:'delivered',sms_status:'accepted'},
 {id:'m2',supplier_org_id:'s2',supplier_name:'Category Supplier',status:'partial',verification_status:'verified',service_coverage_status:'covered',reasons:['Approved main category capability in Electrical'],matched_categories:['Electrical'],covered_items:['Not an exact product'],invitation_status:'invited'},
 ],invitations:[{id:'i1',supplier_org_id:'s1',supplier_name:'Acme Materials',status:'accepted',quotation_status:'submitted',invited_at:'2026-11-01T10:00:00Z',created_at:'2026-11-01T10:00:00Z',email_status:'delivered',sms_status:'not_queued'},
 {id:'i2',supplier_org_id:'s2',supplier_name:'Category Supplier',status:'invited',reminder_eligible:true,invited_at:'2026-11-01T10:00:00Z',created_at:'2026-11-01T10:00:00Z',email_status:'accepted',sms_status:'failed'}],
 progress:'invitations_available',matching_status:'completed',matched_suppliers:94,total_candidates:130,invited_suppliers:2,invitation_total:2,quotations_received:1,suppliers_evaluated:600,pending_suppliers:1,page_total:94,
 filter_options:{disciplines:['Electrical'],main_categories:[{id:'c1',name:'Electrical'}],subcategories:[{id:'c2',name:'Cables',parent_id:'c1'}]}};
const quotations={rfq_id:'r1',automatic_quotations:[{id:'q-auto-1',reference:'QT-AUTO01',supplier_org_id:'s1',supplier_name:'Acme Materials',status:'submitted',submission_source:'automatic',submitted_at:'2026-11-02T08:00:00Z',currency:'SAR',price_basis:'supply',covered_items:2,total_items:3,totals:{subtotal:'1000.00',vat:'150.00',total:'1150.00',currency:'SAR'}}],
 manual_quotations:[],preparing_automatic_count:0,preparing_manual_count:2,manual_pricing_required_count:1,total_submitted:1};
const notifications={rfq_id:'r1',total:2,limit:10,offset:0,counts:{queued:0,accepted:1,delivered:0,failed:1,bounced:0,skipped:0},records:[
 {id:'n1',supplier_org_id:'s1',supplier_name:'Acme Materials',contact_name:'Sara',destination:'s***@acme.test',channel:'email',template:'rfq_invitation',status:'accepted',status_group:'accepted',failure_reason:null,created_at:'2026-11-01T10:00:00Z',sent_at:'2026-11-01T10:01:00Z',invitation_id:'i1',retryable:false},
 {id:'n2',supplier_org_id:'s2',supplier_name:'Category Supplier',contact_name:null,destination:'+966•••123',channel:'sms',template:'rfq_invitation',status:'failed',status_group:'failed',failure_reason:'provider_error',created_at:'2026-11-01T10:00:00Z',sent_at:null,invitation_id:'i2',retryable:true}]};

function view({data=fixture,arabic=false,cooldown=false,embedded=false,id,admin=false}={}){
 let cursor=0;const state=[],requests=[],writes=[],messages=[];
 const store={favorites:[{supplier_org_id:'s1',name:'Acme Materials'}],data:structuredClone(data)};
 if(cooldown)store.data.invitations[1].next_reminder_at=new Date(Date.now()+86400000).toISOString();
 const rfqs=[{id:'r1',reference:'RFQ-001',title:'First RFQ',project_name:'Project Alpha',city:'Riyadh',status:'published'}, {id:'r2',reference:'RFQ-002',title:'Second RFQ',project_name:'Project Beta',city:'Jeddah',status:'published'}];
 const hooks={...React,useState(initial){const index=cursor++;if(!(index in state))state[index]=typeof initial==='function'?initial():initial;return[state[index],value=>state[index]=typeof value==='function'?value(state[index]):value];},useEffect(){},useRef(initial){const index=cursor++;if(!(index in state))state[index]={current:initial};return state[index];},useMemo:fn=>fn()};
 const button=({children,...props})=>React.createElement('button',props,children);
 const shared={
 'react':hooks,'next/link':{__esModule:true,default:({children,...props})=>React.createElement('a',props,children)},
 '@/lib/locale':{useLocale:()=>({arabic,t:value=>value})},
 '@/features/auth/session':{useSession:()=>({org:{id:'buyer'},user:{id:'user',is_admin:admin}})},
 '@/features/shared/toast':{useToast:()=>({success:message=>messages.push(message),error:message=>messages.push(message)})},
 '@/components/ui/button':{Button:button},
 '@/features/shared/ui':{State:({error,loading})=>error?React.createElement('div',{role:'alert'},error.message):loading?React.createElement('div',{role:'status'},'Loading'):null},
 '@/features/shared/server-list':{useServerList:base=>({rows:base?rfqs:[],controls:React.createElement('div',null,'Server RFQ search and paging'),reload(){},fetching:false})},
 '@/lib/api/client':{json:JSON.stringify,apiUrl:route=>`https://api.test/${route}`,api:async(route,init)=>{writes.push({route,...init});if(route.endsWith('/reminders'))return{queued:1,message:'Queued'};const supplier=route.split('/').at(-1);if(init.method==='PUT')store.favorites.push({supplier_org_id:supplier,name:'Saved'});if(init.method==='DELETE')store.favorites=store.favorites.filter(row=>row.supplier_org_id!==supplier);return{};}},
 '@/lib/api/use-api':{useApi:route=>{requests.push(route);let value;
  if(route?.startsWith('rfqs/'))value={rfq:rfqs.find(row=>row.id===route.split('/')[1])||rfqs[0],items:[{main_category_id:'c1',quantity:'12',unit:'M'},{main_category_id:'c1',quantity:'3',unit:'M'}]};
  else if(route?.includes('/quotations-summary'))value=quotations;
  else if(route?.includes('/notifications?'))value=notifications;
  else if(route?.startsWith('matching/'))value=store.data;
  if(route?.startsWith('taxonomy/'))value=[{id:'c1',name_en:'Electrical',name_ar:'Electrical Arabic'}];
  if(route?.includes('/favorites?'))value=store.favorites;
  return{data:value,loading:false,fetching:false,reload(){}};}},
 './matching-model':model,'./matching-copy':copy,'./admin-matching-diagnostics':{AdminMatchingDiagnostics:()=>null},'./matched-suppliers.module.css':{__esModule:true,default:new Proxy({},{get:(_,name)=>name})},
 };
 const panels=compile(path.join(rfqsDir,'matching-panels.tsx'),shared);
 const component=compile(path.join(rfqsDir,'matched-suppliers.tsx'),{...shared,'./matching-panels':panels});
 function expand(node){if(Array.isArray(node))return node.map(expand);if(!React.isValidElement(node))return node;if(typeof node.type==='function')return expand(node.type(node.props));return React.cloneElement(node,{},...React.Children.toArray(expand(node.props.children)));}
 const render=()=>{cursor=0;return expand(component.Matching({id:id||undefined,embedded}));};
 return{render,state,requests,writes,messages,store};
}
function nodes(node,out=[]){if(Array.isArray(node)){node.forEach(child=>nodes(child,out));return out;}if(!React.isValidElement(node))return out;out.push(node);nodes(node.props.children,out);return out;}
const text=node=>React.isValidElement(node)?text(node.props.children):Array.isArray(node)?node.map(text).join(''):String(node??'');
const find=(tree,type,label)=>nodes(tree).find(node=>node.type===type&&(text(node)===label||node.props['aria-label']===label));
const tab=(tree,label)=>nodes(tree).find(node=>node.props.role==='tab'&&text(node)===label);
const markup=tree=>renderToStaticMarkup(tree);
const results=(requests,fragment)=>requests.filter(route=>route?.startsWith('matching/')&&route.includes(fragment));

test('default view: compact header, five tabs, filtered table only, 10 per page with accurate range',()=>{
 const v=view({id:'r1'}),tree=v.render(),html=markup(tree);
 assert.equal(nodes(tree).filter(node=>node.type==='h1').length,1);assert.match(html,/Matched suppliers/);
 assert.deepEqual(nodes(tree).filter(node=>node.props.role==='tab').map(text),['Filtered Suppliers','Total Matches','Invitations','Quotations','Notifications']);
 assert.equal(tab(tree,'Filtered Suppliers').props['aria-selected'],true);
 // Summary statistics use backend counts.
 const stats=nodes(tree).filter(node=>node.type==='dd').map(text);assert.deepEqual(stats,['130','94','2','1']);
 // Only the active tab is fetched and rendered.
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=10&offset=0'));
 assert.equal(v.requests.filter(route=>route&&/quotations-summary|notifications|view=total|invitation_limit/.test(route)).length,0);
 assert.equal(nodes(tree).filter(node=>node.type==='table').length,1);
 assert.match(html,/Showing 1–10 of 94 suppliers/);
 assert.doesNotMatch(html,/Not an exact product|item:private-uuid|Automatic quotations|Notification records/);
 assert.equal(nodes(tree).filter(node=>node.type==='article').length,0,'oversized cards are gone');
});

test('server-side pagination, page size, search, filters and sort; matching engine is not limited',()=>{
 const v=view({id:'r1'});let tree=v.render();
 find(tree,'button','Next page').props.onClick();tree=v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=10&offset=10'));
 const size=nodes(find(tree,'nav','Matched supplier pages')).find(node=>node.type==='select');
 assert.deepEqual(nodes(size).filter(node=>node.type==='option').map(node=>node.props.value),[10,20,50]);
 size.props.onChange({target:{value:'20'}});tree=v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=20&offset=0'));
 find(tree,'button','Next page').props.onClick();tree=v.render();
 const search=nodes(tree).find(node=>node.type==='input'&&node.props.type==='search');
 search.props.onChange({target:{value:'Acme'}});tree=v.render();
 nodes(tree).find(node=>node.type==='input'&&node.props.type==='search').props.onKeyDown({key:'Enter'});tree=v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=20&offset=0&q=Acme'),'search resets to page one');
 const matchType=nodes(tree).find(node=>node.type==='select'&&nodes(node).some(option=>option.props.value==='exact'));
 matchType.props.onChange({target:{value:'exact'}});tree=v.render();
 nodes(tree).find(node=>node.type==='button'&&text(node).startsWith('More filters')).props.onClick();tree=v.render();
 const discipline=nodes(tree).find(node=>node.type==='select'&&nodes(node).some(option=>text(option)==='All disciplines'));
 discipline.props.onChange({target:{value:'Electrical'}});tree=v.render();
 const sort=nodes(tree).find(node=>node.type==='select'&&nodes(node).some(option=>option.props.value==='name_desc'));
 sort.props.onChange({target:{value:'name'}});tree=v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=20&offset=0&q=Acme&discipline=Electrical&match_type=exact&sort=name'));
 assert.match(markup(tree),/Clear/);
 for(const route of results(v.requests,'results?'))assert.match(route,/limit=(10|20|50)&/);
});

test('tabs fetch lazily and preserve each tab state when switching',()=>{
 const v=view({id:'r1'});let tree=v.render();
 find(tree,'button','Next page').props.onClick();tree=v.render();
 tab(tree,'Total Matches').props.onClick();tree=v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=10&offset=0&view=total'));
 tab(tree,'Invitations').props.onClick();tree=v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=1&offset=0&invitation_limit=10&invitation_offset=0'));
 assert.equal(nodes(tree).filter(node=>node.props.role==='tabpanel').length,1);
 tab(tree,'Filtered Suppliers').props.onClick();tree=v.render();
 const before=v.requests.length;tree=v.render();
 assert.equal(v.requests.slice(before).filter(route=>route?.includes('/results?')).at(0),'matching/rfqs/r1/results?limit=10&offset=10','filtered page preserved');
 assert.ok(!v.requests.slice(before).some(route=>route&&/view=total|invitation_limit/.test(route)),'inactive tabs are not fetched');
});

test('supplier row is compact with expandable matching reasons, profile link and favorite',async()=>{
 const v=view({id:'r1'});let tree=v.render();
 const rows=nodes(tree).filter(node=>node.type==='tr'&&node.props['data-recommended']!==undefined);
 assert.equal(rows.length,2);
 let html=markup(tree);assert.match(html,/Exact material/);assert.match(html,/Copper cable 4mm/);assert.match(html,/\+1 more/);assert.match(html,/Riyadh/);
 assert.match(html,/Quotation received/);assert.match(html,/href="\/suppliers\/s1"/);
 assert.doesNotMatch(html,/Why this supplier matched/);
 find(tree,'button','Matching details: Acme Materials').props.onClick();tree=v.render();html=markup(tree);
 assert.match(html,/Why this supplier matched/);assert.match(html,/Cable tray/);assert.match(html,/Same product family/);assert.match(html,/Saved price available/);
 const stars=nodes(tree).filter(node=>node.type==='button'&&/^(Save as favorite|Remove from favorites):/.test(node.props['aria-label']||''));
 assert.equal(stars.length,2);await stars[0].props.onClick();tree=v.render();
 assert.equal(v.writes[0].method,'DELETE');assert.equal(v.writes[0].route,'organizations/buyer/favorites/s1');
 await find(tree,'button','Save as favorite: Acme Materials').props.onClick();assert.equal(v.writes[1].method,'PUT');
});

test('invitations tab: honest copy, paginated table, individual and selected bulk reminders, cooldown',async()=>{
 const v=view({id:'r1'});let tree=v.render();tab(tree,'Invitations').props.onClick();tree=v.render();const html=markup(tree);
 assert.match(html,/recorded invitation does not confirm email or SMS delivery/);
 assert.match(html,/app\/buyer\/compare\?rfq=r1/);assert.match(html,/Showing 1–2 of 2 invitations/);
 assert.match(html,/SMS: Failed/);assert.match(html,/Email: Accepted by provider/);
 const checkbox=nodes(tree).find(node=>node.type==='input'&&node.props.type==='checkbox'&&!node.props.disabled&&node.props['aria-label']==='Select Category Supplier');
 checkbox.props.onChange({target:{checked:true}});tree=v.render();
 const remind=find(tree,'button','Remind selected (1)');await remind.props.onClick();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(JSON.parse(v.writes[0].body),{invitation_ids:['i2']});assert.equal(v.writes[0].route,'rfqs/r1/reminders');
 tree=v.render();await find(tree,'button','Send reminder').props.onClick();
 assert.deepEqual(JSON.parse(v.writes[1].body),{invitation_ids:['i2']});
 const cooled=view({id:'r1',cooldown:true});let ctree=cooled.render();tab(ctree,'Invitations').props.onClick();ctree=cooled.render();
 assert.ok(nodes(ctree).filter(node=>node.type==='input'&&node.props.type==='checkbox').every(node=>node.props.disabled));
 assert.equal(find(ctree,'button','Remind selected').props.disabled,true);
});

test('quotations tab: automatic and manual sub-tabs with reference, totals, view, PDF and compare',()=>{
 const v=view({id:'r1'});let tree=v.render();tab(tree,'Quotations').props.onClick();tree=v.render();let html=markup(tree);
 assert.ok(v.requests.includes('matching/rfqs/r1/quotations-summary'));
 assert.match(html,/QT-AUTO01/);assert.match(html,/1,150.00 SAR/);assert.match(html,/2\/3 items/);
 assert.match(html,/href="\/app\/buyer\/quote\/q-auto-1"/);assert.match(html,/https:\/\/api.test\/quotations\/q-auto-1\/export.pdf\?lang=en/);
 assert.match(html,/href="\/app\/buyer\/compare\?rfq=r1"/);
 tab(tree,'Manual quotations0').props.onClick();tree=v.render();html=markup(tree);
 assert.match(html,/No manual quotations submitted yet/);assert.match(html,/Invited suppliers are preparing quotations/);
});

test('notifications tab separates delivery groups and never treats invitations as delivered',async()=>{
 const v=view({id:'r1'});let tree=v.render();tab(tree,'Notifications').props.onClick();tree=v.render();const html=markup(tree);
 assert.ok(v.requests.includes('matching/rfqs/r1/notifications?limit=10&offset=0'));
 for(const label of ['Queued','Provider accepted','Delivered','Failed','Bounced','Skipped'])assert.ok(find(tree,'button',`${label}${label==='Provider accepted'||label==='Failed'?1:0}`),label);
 assert.match(html,/Provider accepted is not the same as delivered/);assert.match(html,/Provider error/);assert.match(html,/s\*\*\*@acme.test/);
 find(tree,'button','Failed1').props.onClick();tree=v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/notifications?limit=10&offset=0&group=failed'));
 const resend=find(tree,'button','Resend');assert.ok(resend,'buyers resend through the reminder endpoint');
 await resend.props.onClick();assert.equal(v.writes[0].route,'rfqs/r1/reminders');assert.deepEqual(JSON.parse(v.writes[0].body),{invitation_ids:['i2']});
 const admin=view({id:'r1',admin:true});let atree=admin.render();tab(atree,'Notifications').props.onClick();atree=admin.render();
 await find(atree,'button','Retry').props.onClick();assert.equal(admin.writes[0].route,'notifications/admin/outbox/n2/retry');
});

test('RFQ selection resets every tab to its first page',()=>{
 const v=view();let tree=v.render();nodes(tree).find(node=>node.type==='select'&&node.props.id==='matching-rfq').props.onChange({target:{value:'r1'}});tree=v.render();
 find(tree,'button','Next page').props.onClick();tree=v.render();
 nodes(tree).find(node=>node.type==='select'&&node.props.id==='matching-rfq').props.onChange({target:{value:'r2'}});tree=v.render();
 assert.ok(v.requests.includes('rfqs/r2'));assert.ok(v.requests.includes('matching/rfqs/r2/results?limit=10&offset=0'));
 const html=markup(tree);assert.match(html,/Project Beta/);assert.match(html,/Jeddah/);assert.match(html,/15 M/);assert.match(html,/Server RFQ search and paging/);
});

test('statuses follow evidence; inconsistent results are flagged',()=>{
 assert.equal(model.matchingState(fixture).label,'completed');
 assert.equal(model.matchingState({...fixture,progress:'matching_failed'}).label,'failed');
 assert.equal(model.matchingState({...fixture,progress:'draft'}).label,'draft');
 assert.equal(model.matchingState({...fixture,progress:'matching_queued'}).label,'queued');
 assert.equal(model.matchingState({...fixture,progress:'matching_in_progress'},true).label,'review');
 assert.equal(model.matchingState({...fixture,progress:'no_eligible_suppliers'}).inconsistent,true);
 assert.equal(model.matchingState({...fixture,progress:'matching_in_progress'}).inconsistent,false,'Old results can legitimately coexist with a queued rerun');
 const html=markup(view({id:'r1',data:{...fixture,progress:'no_eligible_suppliers'}}).render());assert.match(html,/recorded results disagree/);
});

test('Arabic uses translated labels, original supplier names and RTL; embedded view omits the selector',()=>{
 const html=markup(view({id:'r1',arabic:true}).render());
 assert.match(html,/dir="rtl"/);assert.ok(html.includes(copy.matchingArabic['Matched suppliers']));assert.ok(html.includes(copy.matchingArabic['Total Matches']));
 assert.match(html,/Acme Materials/);assert.doesNotMatch(html,/\?{3}/);
 const embedded=view({id:'r1',embedded:true}).render();const markupEmbedded=markup(embedded);
 assert.doesNotMatch(markupEmbedded,/matching-rfq|Find another RFQ/);assert.equal(nodes(embedded).filter(node=>node.type==='h1').length,0);
});

test('model: query paths send only active filters and page ranges are exact',()=>{
 const query={...model.emptySupplierQuery(),q:' steel ',brand:'Hilti',offset:20,limit:10};
 assert.equal(model.supplierResultsPath('r1','total',query),'matching/rfqs/r1/results?limit=10&offset=20&view=total&q=steel&brand=Hilti');
 assert.equal(model.activeFilterCount(query),1);
 assert.deepEqual({...model.pageRange(0,10,94)},{from:1,to:10,total:94,page:1,pages:10});
 assert.deepEqual({...model.pageRange(90,10,94)},{from:91,to:94,total:94,page:10,pages:10});
 assert.equal(model.pageRange(0,10,0).total,0);
 assert.equal(model.notificationsPath('r1',{limit:10,offset:0,channel:'sms',group:''}),'matching/rfqs/r1/notifications?limit=10&offset=0&channel=sms');
});

test('broad categories never become exact material evidence, and unsupported reason data stays hidden',()=>{
 assert.equal(model.matchEvidence(fixture.matches[1]),'category');
 assert.equal(model.matchEvidence({...fixture.matches[1],reasons:['item:uuid','coverage:100%'],match_levels:[]}),null);
 assert.equal(model.isRecommended({...fixture.matches[0],service_coverage_status:'not covered'}),false);
 assert.equal(model.eligibilityReason({status:'no_capability'}),'capability');
 assert.equal(model.eligibilityReason({status:'ineligible',reasons:['Missing required project qualifications.']}),'qualifications');
 assert.equal(model.eligibilityReason({status:'coverage_unknown'}),'coverage');
 assert.ok(model.isSubmitted('awarded'));assert.equal(model.isSubmitted('draft'),false);
});
