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
 vm.runInNewContext(source,{exports,require:name=>dependencies[name]||localRequire(name),URL,URLSearchParams,Date,Intl,Set,Map,Object,console});
 return exports;
}
const model=compile(path.resolve(__dirname,'../features/rfqs/matching-model.ts'),{});
const copy=compile(path.resolve(__dirname,'../features/rfqs/matching-copy.ts'),{});
const fixture={matches:[
 {id:'m1',supplier_org_id:'s1',supplier_name:'Acme Materials',status:'eligible',verification_status:'verified',service_coverage_status:'covered',match_levels:['exact material'],matched_categories:['Cables'],covered_items:['Copper cable'],invitation_status:'accepted'},
 {id:'m2',supplier_org_id:'s2',supplier_name:'Category Supplier',status:'partial',verification_status:'verified',service_coverage_status:'covered',reasons:['Approved main category capability in Electrical'],matched_categories:['Electrical'],covered_items:['Not an exact product'],invitation_status:'invited'},
 {id:'m3',supplier_org_id:'s3',supplier_name:'Outside Coverage',status:'location_mismatch',service_coverage_status:'not covered',reasons:['item:private-uuid'],invitation_review_required:true},
 {id:'m4',supplier_org_id:'s4',supplier_name:'Unverified Company',status:'unverified',verification_status:'unverified'},
 ],invitations:[{id:'i1',supplier_org_id:'s1',supplier_name:'Acme Materials',status:'accepted',quotation_status:'submitted'},
 {id:'i2',supplier_org_id:'s2',supplier_name:'Category Supplier',status:'invited',reminder_eligible:true}],progress:'invitations_available',matched_suppliers:2,invited_suppliers:2,suppliers_evaluated:60,pending_suppliers:1};

function view({data=fixture,arabic=false,cooldown=false,embedded=false,id}={}){
 let cursor=0;const state=[],effects=[],requests=[],writes=[],messages=[];
 const store={favorites:[{supplier_org_id:'s1',name:'Acme Materials'}],data:structuredClone(data)};
 if(cooldown)store.data.reminder_available_at=new Date(Date.now()+86400000).toISOString();
 const rfqs=[{id:'r1',reference:'RFQ-001',title:'First RFQ',project_name:'Project Alpha',city:'Riyadh',status:'published'}, {id:'r2',reference:'RFQ-002',title:'Second RFQ',project_name:'Project Beta',city:'Jeddah',status:'published'}];
 const hooks={...React,useState(initial){const index=cursor++;if(!(index in state))state[index]=typeof initial==='function'?initial():initial;return[state[index],value=>state[index]=typeof value==='function'?value(state[index]):value];},useEffect(fn){effects.push(fn);},useMemo:fn=>fn()};
 const button=({children,...props})=>React.createElement('button',props,children);
 const component=compile(path.resolve(__dirname,'../features/rfqs/matched-suppliers.tsx'),{
 'react':hooks,'next/link':{__esModule:true,default:({children,...props})=>React.createElement('a',props,children)},
 '@/lib/locale':{useLocale:()=>({arabic,t:value=>value})},
 '@/features/auth/session':{useSession:()=>({org:{id:'buyer'},user:{id:'user',is_admin:false}})},
 '@/features/shared/toast':{useToast:()=>({success:message=>messages.push(message),error:message=>messages.push(message)})},
 '@/components/ui/button':{Button:button},
 '@/features/shared/ui':{State:({error,loading})=>error?React.createElement('div',{role:'alert'},error.message):loading?React.createElement('div',{role:'status'},'Loading'):null},
 '@/features/shared/server-list':{useServerList:base=>({rows:base?rfqs:[],controls:React.createElement('div',null,'Server RFQ search and paging'),reload(){},fetching:false})},
 '@/lib/api/client':{json:JSON.stringify,api:async(route,init)=>{writes.push({route,...init});const supplier=route.split('/').at(-1);if(init.method==='PUT')store.favorites.push({supplier_org_id:supplier,name:'Saved'});if(init.method==='DELETE')store.favorites=store.favorites.filter(row=>row.supplier_org_id!==supplier);return{};}},
 '@/lib/api/use-api':{useApi:route=>{requests.push(route);let value;if(route?.startsWith('rfqs/'))value={rfq:rfqs.find(row=>row.id===route.split('/')[1])||rfqs[0],items:[{main_category_id:'c1',quantity:'12',unit:'M'},{main_category_id:'c1',quantity:'3',unit:'M'}]};if(route?.startsWith('matching/'))value=store.data;if(route?.startsWith('taxonomy/'))value=[{id:'c1',name_en:'Electrical',name_ar:'Electrical Arabic'}];if(route?.includes('/favorites?'))value=store.favorites;return{data:value,loading:false,fetching:false,reload(){}};}},
 './matching-model':model,'./matching-copy':copy,'./matched-suppliers.module.css':{__esModule:true,default:new Proxy({},{get:(_,name)=>name})},
 });
 function expand(node){if(Array.isArray(node))return node.map(expand);if(!React.isValidElement(node))return node;if(typeof node.type==='function')return expand(node.type(node.props));return React.cloneElement(node,{},...React.Children.toArray(expand(node.props.children)));}
 const render=()=>{cursor=0;effects.length=0;return expand(component.Matching({id:id||undefined,embedded}));};
 return{render,state,effects,requests,writes,messages,store};
}
function nodes(node,out=[]){if(Array.isArray(node)){node.forEach(child=>nodes(child,out));return out;}if(!React.isValidElement(node))return out;out.push(node);nodes(node.props.children,out);return out;}
const text=node=>React.isValidElement(node)?text(node.props.children):Array.isArray(node)?node.map(text).join(''):String(node??'');
const find=(tree,type,label)=>nodes(tree).find(node=>node.type===type&&(text(node)===label||node.props['aria-label']===label));
const markup=tree=>renderToStaticMarkup(tree);

test('recommendations contain only current eligible outcomes; ineligible results are collapsed',()=>{
 const v=view({id:'r1'}),tree=v.render(),html=markup(tree);
 const cards=nodes(tree).filter(node=>node.type==='article');
 assert.deepEqual(cards.map(card=>card.props['data-recommended']),[true,true,false,false]);
 const other=nodes(tree).find(node=>node.type==='details'&&node.props.className==='other');
 assert.equal(other.props.open,undefined);assert.match(text(other),/Delivery location not covered/);
 assert.match(html,/Find the right suppliers/);assert.match(html,/Main category capability/);assert.match(html,/Confirm the specific product/);
 assert.doesNotMatch(html,/Not an exact product|item:private-uuid|96%|Dispatched|pm-favorites-section/);
 assert.equal(nodes(tree).filter(node=>node.type==='h1').length,1);
 assert.equal(nodes(tree).filter(node=>node.type==='article')[0].props.children.filter(Boolean).length,3);
 assert.equal(nodes(tree).filter(node=>node.type==='nav'&&node.props['aria-label']==='Breadcrumbs').length,0);
});

test('RFQ selection preserves URL-independent state and resets both page offsets',()=>{
 const v=view();v.state[0]='r1';v.state[1]={rfq:'r1',offset:25};v.state[2]={rfq:'r1',offset:25};
 let tree=v.render();nodes(tree).find(node=>node.type==='select').props.onChange({target:{value:'r2'}});tree=v.render();
 assert.ok(v.requests.includes('rfqs/r2'));assert.ok(v.requests.includes('matching/rfqs/r2/results?limit=25&offset=0'));
 assert.match(markup(tree),/Project Beta/);assert.match(markup(tree),/Jeddah/);assert.match(markup(tree),/15 M/);
 assert.match(markup(tree),/Server RFQ search and paging/);
});

test('evaluated pagination uses all 60 evaluated suppliers rather than only 2 eligible',()=>{
 const v=view({id:'r1'});let tree=v.render();const pager=find(tree,'nav','Evaluated supplier pages');
 const next=find(pager,'button','Next');assert.equal(next.props.disabled,false);next.props.onClick();v.render();
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=25&offset=25'));
 // Invitation page remains at zero, with one extra bounded request only.
 assert.ok(v.requests.includes('matching/rfqs/r1/results?limit=25&offset=0'));
});

test('one favorite toggle per supplier uses existing PUT/DELETE and the favorites tab',async()=>{
 const v=view({id:'r1'});let tree=v.render();
 const stars=nodes(tree).filter(node=>node.type==='button'&&/^(Save as favorite|Remove from favorites):/.test(node.props['aria-label']||''));
 assert.equal(stars.length,4);await stars[0].props.onClick();tree=v.render();
 assert.equal(v.writes[0].method,'DELETE');assert.equal(v.writes[0].route,'organizations/buyer/favorites/s1');
 const save=nodes(tree).find(node=>node.type==='button'&&node.props['aria-label']==='Save as favorite: Acme Materials');
 await save.props.onClick();assert.equal(v.writes[1].method,'PUT');
 tree=v.render();find(tree,'button','Favorites').props.onClick();tree=v.render();
 assert.match(markup(tree),/Saved suppliers/);assert.match(markup(tree),/Favorite supplier pages/);
});

test('invitation progress is honest, quotation navigation scoped, reminders bounded by cooldown',async()=>{
 const v=view({id:'r1'});let tree=v.render();const html=markup(tree);
 assert.match(html,/recorded invitation does not confirm email or SMS delivery/);
 assert.match(html,/app\/buyer\/compare\?rfq=r1/);
 const checkbox=nodes(tree).find(node=>node.type==='input'&&node.props.type==='checkbox'&&!node.props.disabled);
 checkbox.props.onChange({target:{checked:true}});tree=v.render();
 await find(tree,'button','Remind selected suppliers').props.onClick();
 assert.deepEqual(JSON.parse(v.writes[0].body),{invitation_ids:['i2']});assert.equal(v.writes[0].route,'rfqs/r1/reminders');
 const cooled=view({id:'r1',cooldown:true}).render();
 assert.ok(nodes(cooled).filter(node=>node.type==='input'&&node.props.type==='checkbox').every(node=>node.props.disabled));
 assert.equal(find(cooled,'button','Remind selected suppliers').props.disabled,true);
});

test('quotation counts on incomplete pages are marked as page counts',()=>{
 const data=structuredClone(fixture);data.invited_suppliers=40;
 const html=markup(view({id:'r1',data}).render());assert.match(html,/On this invitation page/);assert.match(html,/Response counts are for this invitation page/);
});

test('completed, failed, draft, queued and stalled statuses follow evidence without inventing completion',()=>{
 assert.equal(model.matchingState(fixture).label,'completed');
 assert.equal(model.matchingState({...fixture,progress:'matching_failed'}).label,'failed');
 assert.equal(model.matchingState({...fixture,progress:'draft'}).label,'draft');
 assert.equal(model.matchingState({...fixture,progress:'matching_queued'}).label,'queued');
 assert.equal(model.matchingState({...fixture,progress:'matching_in_progress'},true).label,'review');
 assert.equal(model.matchingState({...fixture,progress:'no_eligible_suppliers'}).inconsistent,true);
 assert.equal(model.matchingState({...fixture,progress:'matching_in_progress'}).inconsistent,false,'Old results can legitimately coexist with a queued rerun');
 const html=markup(view({id:'r1',data:{...fixture,progress:'no_eligible_suppliers'}}).render());assert.match(html,/recorded results disagree/);
});

test('Arabic uses translated labels, original supplier names and RTL; embedded view omits header and selector',()=>{
 const html=markup(view({id:'r1',arabic:true}).render());
 assert.match(html,/dir="rtl"/);assert.ok(html.includes(copy.matchingArabic['Find the right suppliers.']));assert.match(html,/Acme Materials/);assert.doesNotMatch(html,/\?{3}/);
 const embedded=markup(view({id:'r1',embedded:true}).render());assert.doesNotMatch(embedded,/Find the right suppliers|matching-rfq/);
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
