const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const ts=require('typescript');

function compile(filename,dependencies){
 const exports={};const localRequire=createRequire(filename);
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 vm.runInNewContext(source,{exports,require:name=>dependencies[name]||localRequire(name),URL,URLSearchParams,Date,Intl,Set,Map,Object,Number,Math,Array,String,JSON,console,Event:class{constructor(type){this.type=type;}},window:{dispatchEvent(){},location:{search:'',href:'https://app.test/app/buyer/quote/q1'},history:{replaceState(){}},setTimeout(){}}});
 return exports;
}
const dir=path.resolve(__dirname,'../features/quotations');
const model=compile(path.join(dir,'review-model.ts'),{});
const copy=compile(path.join(dir,'review-copy.ts'),{});

const items=[
 {id:'it1',rfq_id:'r1',material_id:null,main_category_id:'c1',subcategory_id:'c2',description:'Copper cable 4mm',specifications:'4mm², 600/1000V, XLPE',unit:'M',quantity:'100.000',discipline:'Electrical',required_brand:'Riyadh Cables',is_active:true,removed_at:null,created_at:'2026-11-01T00:00:00Z'},
 {id:'it2',rfq_id:'r1',material_id:null,main_category_id:'c1',subcategory_id:'c2',description:'Cable tray 300mm',specifications:'Hot-dip galvanized',unit:'M',quantity:'40.000',discipline:'Electrical',required_brand:'',is_active:true,removed_at:null,created_at:'2026-11-01T00:00:00Z'},
];
function quote({status='submitted',review=false,lines=null,source='manual',validity='2026-12-20',attachments=[{id:'f1',filename:'datasheet.pdf',content_type:'application/pdf',size:204800}]}={}){
 const docLines=lines||[{rfq_item_id:'it1',description:'Copper cable 4mm',offered_description:'Cu/XLPE/PVC 4mm² cable',brand:'Riyadh Cables',model:'RC-4X',specification:'4mm², 0.6/1kV',quantity:'100.000',unit:'M',unit_price:'11.50',line_total:'1150.00',subtotal:'1150.00',lead_days:14,vat_rate:'0.1500',compliance:'partial',discount_percent:'10.00',discount_amount:'115.00',net_total:'1035.00'}];
 return {
  quotation:{id:'q1',invitation_id:'i1',rfq_id:'r1',supplier_org_id:'s1',status,price_basis:'supply',currency:'SAR',vat_rate:'0.1500',submitted_at:'2026-11-05T09:00:00Z',revision:1,rfq_revision:0,submission_source:source,review_required:review,review_reasons:[],policy_revision:null,
   commercial_terms:{payment_terms:'30 days from invoice',delivery_terms:'',quotation_reference:'SUP-77',terms_and_conditions:'Prices exclude offloading.\nValid for stated quantities.'},created_at:'2026-11-04T08:00:00Z'},
  lines:docLines.map(line=>({quotation_id:'q1',rfq_item_id:line.rfq_item_id,requested_snapshot:{description:'Copper cable 4mm',specifications:'4mm², 600/1000V, XLPE',quantity:'100.000',unit:'M',main_category_id:'c1',subcategory_id:'c2',required_brand:'Riyadh Cables'},brand:line.brand,model:line.model,offered_specs:line.specification,compliance:line.compliance,quantity:line.quantity,unit_price:line.unit_price,lead_days:line.lead_days,note:'Delivery in two batches.',offered_description:line.offered_description,source_rate_id:null,source_rate_version:null,vat_rate:null,discount_percent:line.discount_percent||'0',warranty_months:12,availability:'available',id:`l-${line.rfq_item_id}`,created_at:'2026-11-04T08:00:00Z'})),
  totals:{subtotal:'1150.00',vat:'155.25',total:'1190.25',currency:'SAR'},supplier_name:'Acme Materials',rfq_reference:'RFQ-001',attachments,
  document:{quotation_reference:'QT-AB12CD34',rfq_reference:'RFQ-001',rfq_title:'Project Alpha',quotation_date:'2026-11-04T08:00:00+00:00',validity_date:validity,currency:'SAR',submission_source:source,status,
   supplier:{name:'Acme Materials'},buyer:{name:'Buyer Co'},lines:docLines,
   totals:{subtotal:'1150.00',discount:'115.00',delivery_charge:'0.00',net_amount:'1035.00',vat:'155.25',total:'1190.25',currency:'SAR'}},
 };
}

function view({data=quote(),arabic=false,role='buyer',rfqRevision=0}={}){
 let cursor=0;const state=[],requests=[],writes=[],messages=[];
 const hooks={...React,useState(initial){const index=cursor++;if(!(index in state))state[index]=typeof initial==='function'?initial():initial;return[state[index],value=>state[index]=typeof value==='function'?value(state[index]):value];},useEffect(){},useMemo:fn=>fn()};
 const pass=({children})=>React.createElement(React.Fragment,null,children);
 const shared={
  'react':hooks,'next/link':{__esModule:true,default:({children,...props})=>React.createElement('a',props,children)},
  '@radix-ui/react-dialog':{Root:({open,children})=>open?React.createElement('div',{role:'dialog'},children):null,Portal:pass,Overlay:()=>null,Content:({children,...props})=>React.createElement('div',props,children),Title:({children})=>React.createElement('h2',null,children),Description:({children})=>React.createElement('p',null,children)},
  '@/lib/locale':{useLocale:()=>({arabic,t:value=>value})},
  '@/features/shared/toast':{useToast:()=>({success:message=>messages.push(message),error:message=>messages.push(message)})},
  '@/components/ui/button':{Button:({children,...props})=>React.createElement('button',props,children)},
  '@/features/shared/ui':{State:({error})=>error?React.createElement('div',{role:'alert'},error.message):null},
  '@/lib/api/client':{json:JSON.stringify,apiUrl:route=>`https://api.test/${route}`,api:async(route,init)=>{writes.push({route,...init});return{};}},
  '@/lib/api/use-api':{useApi:route=>{requests.push(route);let value;
   if(route==='quotations/q1')value=data;
   else if(route?.startsWith('rfqs/'))value={rfq:{id:'r1',reference:'RFQ-001',title:'Alpha',project_name:'Project Alpha',revision:rfqRevision,status:'published'},items};
   else if(route==='taxonomy/categories')value=[{id:'c1',name_en:'Electrical',name_ar:'كهرباء'},{id:'c2',name_en:'Cables',name_ar:'كابلات'}];
   else if(route?.endsWith('/history'))value=[{action:'quotation.submitted',created_at:'2026-11-05T09:00:00Z',details:{source:'manual'}},{action:'quotation.rejected',created_at:'2026-11-06T09:00:00Z',details:{before:'submitted',after:'rejected',reason:'Price above budget'}}];
   else if(route?.endsWith('/revisions'))value=[];
   return{data:value,loading:false,fetching:false,reload(){}};}},
  './review-model':model,'./review-copy':copy,'./buyer-review.module.css':{__esModule:true,default:new Proxy({},{get:(_,name)=>name})},
 };
 const component=compile(path.join(dir,'buyer-review.tsx'),shared);
 function expand(node){if(Array.isArray(node))return node.map(expand);if(!React.isValidElement(node))return node;if(typeof node.type==='function')return expand(node.type(node.props));return React.cloneElement(node,{},...React.Children.toArray(expand(node.props.children)));}
 const render=()=>{cursor=0;return expand(component.BuyerQuotationReview({id:'q1',role}));};
 return{render,state,requests,writes,messages};
}
function nodes(node,out=[]){if(Array.isArray(node)){node.forEach(child=>nodes(child,out));return out;}if(!React.isValidElement(node))return out;out.push(node);nodes(node.props.children,out);return out;}
const text=node=>React.isValidElement(node)?text(node.props.children):Array.isArray(node)?node.map(text).join(''):String(node??'');
const find=(tree,type,label)=>nodes(tree).find(node=>node.type===type&&(text(node).includes(label)||node.props['aria-label']===label));
const tab=(tree,label)=>nodes(tree).find(node=>node.props.role==='tab'&&text(node).startsWith(label));
const screen=tree=>nodes(tree).find(node=>node.props.className==='screenOnly');
const html=tree=>renderToStaticMarkup(tree);

test('header, summary cards and a single backend financial summary; no editable supplier fields',()=>{
 const v=view(),tree=v.render(),page=screen(tree),out=html(page);
 assert.equal(text(nodes(page).find(node=>node.type==='h1')).trim(),'Quotation QT-AB12CD34');
 for(const value of ['RFQ-001','Project Alpha','Acme Materials','Buyer Co','Submitted','Manual quotation','Back to Quotations'])assert.match(out,new RegExp(value));
 assert.equal(find(page,'a','Back to Quotations').props.href,'/app/buyer/quotations');
 for(const label of ['PDF (English)','PDF (Arabic)','Print'])assert.ok(find(page,'button',label),label);
 // Read-only: no supplier-editable inputs on the buyer view.
 assert.equal(nodes(page).filter(node=>['input','textarea'].includes(node.type)).length,0);
 assert.equal(nodes(page).filter(node=>node.type==='select').length,0,'pager only appears for large quotations');
 const cards=nodes(page).find(node=>node.props['aria-label']==='Quotation summary');
 const values=nodes(cards).filter(node=>node.type==='dd').map(text);
 assert.equal(values[0].replace(/\s+/g,' ').trim(),'1,190.25 SAR');
 assert.equal(values[1],'1 / 2');assert.equal(values[2],'14 days');assert.match(values[3],/20 Dec 2026/);
 assert.match(out,/1 not priced/);
 // Exactly one financial summary, using authoritative backend values only.
 assert.equal(nodes(page).filter(node=>node.type==='h2'&&text(node)==='Financial summary').length,1);
 const summary=nodes(page).find(node=>node.props['aria-labelledby']==='quote-financial-summary');
 const rows=Object.fromEntries(nodes(summary).filter(node=>node.type==='div'&&node.props.children?.length===2).map(node=>[text(node.props.children[0]),text(node.props.children[1]).replace(/\s+/g,' ').trim()]));
 assert.deepEqual(rows,{'Subtotal':'1,150.00 SAR','Discount':'−115.00 SAR','Net amount before VAT':'1,035.00 SAR','VAT amount (15%)':'155.25 SAR','Grand total':'1,190.25 SAR','Currency':'SAR'});
 assert.doesNotMatch(out,/Backend calculated totals|Save draft|Remove offer line|Add missing requested item/);
});

test('quoted materials is the default tab; secondary tabs load lazily',()=>{
 const v=view(),tree=v.render(),page=screen(tree);
 assert.deepEqual(nodes(page).filter(node=>node.props.role==='tab').map(node=>text(node).replace(/\d+$/,'')),['Quoted Materials','Technical Comparison','Commercial Terms','Attachments','Activity']);
 assert.equal(tab(page,'Quoted Materials').props['aria-selected'],true);
 assert.equal(v.requests.filter(route=>route&&/history|revisions/.test(route)).length,0);
 const head=nodes(page).filter(node=>node.type==='th').map(text);
 for(const column of ['Main Category','Sub Category','Material description','Brand','Model','Unit','Requested qty','Offered qty','Unit price','Discount','Line total','Lead time','Compliance'])assert.ok(head.includes(column),column);
 const cells=nodes(page).filter(node=>node.type==='td').map(text).map(value=>value.replace(/\s+/g,' ').trim());
 for(const value of ['Electrical','Cables','Riyadh Cables','RC-4X','100','11.50 SAR','1,150.00 SAR','14 days','Partially compliant'])assert.ok(cells.includes(value),value);
 assert.ok(cells.includes('10%−115.00'));
 // Expandable details with specifications, offered description and remarks.
 nodes(page).find(node=>node.type==='button'&&node.props['aria-expanded']===false).props.onClick();
 const out=html(screen(v.render()));
 for(const value of ['Requested specification','4mm², 600/1000V, XLPE','Cu/XLPE/PVC 4mm² cable','Delivery in two batches.','Net after line discount','1,035.00','12 months'])assert.match(out,new RegExp(value.replace(/[()]/g,'\\$&')),value);
 // Clicking the Activity tab only then requests history and revisions.
 tab(page,'Activity').props.onClick();
 const activity=html(screen(v.render()));
 assert.ok(v.requests.includes('quotations/q1/history')&&v.requests.includes('quotations/q1/revisions'));
 assert.match(activity,/Quotation submitted/);assert.match(activity,/Rejected by buyer/);assert.match(activity,/Reason: Price above budget/);
});

test('large quotations paginate 10 materials per page with 10/20/50 options',()=>{
 const lines=Array.from({length:25},(_,index)=>({rfq_item_id:`x${index}`,description:`Item ${index}`,quantity:'1.000',unit:'EA',unit_price:'1.00',line_total:'1.00',lead_days:3,compliance:'compliant'}));
 const v=view({data:quote({lines})}),page=screen(v.render());
 assert.match(html(page),/Showing 1–10 of 25 materials/);
 assert.equal(nodes(page).filter(node=>node.type==='tr'&&node.props.className==='lineRow').length,10);
 assert.deepEqual(nodes(page).filter(node=>node.type==='option').map(text),['10','20','50']);
 find(page,'button','Next page').props.onClick();
 assert.match(html(screen(v.render())),/Showing 11–20 of 25 materials/);
});

test('technical comparison shows requested vs offered and never upgrades compliance',()=>{
 const lines=[{rfq_item_id:'it1',description:'Copper cable 4mm',specification:'0.6/1kV',brand:'RC',quantity:'100',unit:'M',unit_price:'1.00',line_total:'100.00',lead_days:1,compliance:'compliant'},
  {rfq_item_id:'it2',description:'Cable tray 300mm',specification:'',quantity:'40',unit:'M',unit_price:'1.00',line_total:'40.00',lead_days:1,compliance:'matched'}];
 const v=view({data:quote({lines})});tab(screen(v.render()),'Technical Comparison').props.onClick();
 const page=screen(v.render()),out=html(page);
 assert.match(out,/Requested specification/);assert.match(out,/Offered specification/);
 assert.match(out,/Supplier matching does not confirm technical compliance/);
 const tags=nodes(page).filter(node=>node.props.className==='compliance').map(node=>node.props['data-value']);
 assert.deepEqual(tags,['compliant','unconfirmed'],'an unknown compliance value is shown as unconfirmed');
 const chips=nodes(page).filter(node=>node.type==='button'&&'aria-pressed' in node.props).map(text);
 assert.deepEqual(chips,['All2','Compliant1','Partially compliant0','Non-compliant0','Unconfirmed1']);
});

test('commercial terms are read-only blocks; missing values are not invented',()=>{
 const v=view({data:quote({validity:null})});tab(screen(v.render()),'Commercial Terms').props.onClick();
 const page=screen(v.render()),out=html(page);
 const blocks=Object.fromEntries(nodes(page).filter(node=>node.type==='dl'&&node.props.className==='termGrid').flatMap(dl=>nodes(dl).filter(node=>node.type==='div').map(div=>[text(div.props.children[0]),text(div.props.children[1])])));
 assert.deepEqual(blocks,{'Payment terms':'30 days from invoice','Delivery terms':'Not provided','Quotation validity':'Not provided','VAT percentage':'15%','Supplier reference':'SUP-77','Price basis':'Supply only','Currency':'SAR'});
 assert.match(out,/Prices exclude offloading/);
 assert.equal(nodes(page).filter(node=>['input','textarea','select'].includes(node.type)).length,0);
 assert.match(html(nodes(page).find(node=>node.props['aria-label']==='Quotation summary')),/Not specified/);
});

test('attachments offer preview for PDFs and download',()=>{
 const v=view();tab(screen(v.render()),'Attachments').props.onClick();
 const page=screen(v.render());
 assert.match(html(page),/datasheet\.pdf/);assert.ok(find(page,'button','Preview'));assert.ok(find(page,'button','Download'));
 const empty=view({data:quote({attachments:[]})});tab(screen(empty.render()),'Attachments').props.onClick();
 assert.match(html(screen(empty.render())),/did not attach technical documents/);
});

test('decisions: shortlist directly, award and reject through confirmation with an optional reason',async()=>{
 const v=view();let page=screen(v.render());
 for(const label of ['Shortlist quotation','Award quotation','Reject quotation'])assert.ok(find(page,'button',label),label);
 assert.equal(find(page,'a','Compare quotations').props.href,'/app/buyer/compare?rfq=r1');
 find(page,'button','Shortlist quotation').props.onClick();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(v.writes.at(-1),{route:'quotations/q1/decision/shortlisted',method:'POST'});
 assert.ok(v.messages.includes('Quotation shortlisted'));
 // Award asks for confirmation before posting.
 find(screen(v.render()),'button','Award quotation').props.onClick();
 page=screen(v.render());const dialog=nodes(page).find(node=>node.props.role==='dialog');
 assert.match(html(dialog),/Award this quotation\?/);assert.match(html(dialog),/draft purchase order/);assert.match(html(dialog),/1,190.25/);
 assert.equal(v.writes.length,1);
 await find(dialog,'button','Confirm award').props.onClick();
 assert.deepEqual(v.writes.at(-1),{route:'quotations/q1/decision/awarded',method:'POST'});
 // Reject with a reason.
 const r=view();find(screen(r.render()),'button','Reject quotation').props.onClick();
 let rejectDialog=nodes(screen(r.render())).find(node=>node.props.role==='dialog');
 nodes(rejectDialog).find(node=>node.type==='textarea').props.onChange({target:{value:' Over budget '}});
 rejectDialog=nodes(screen(r.render())).find(node=>node.props.role==='dialog');
 await find(rejectDialog,'button','Confirm rejection').props.onClick();
 assert.deepEqual(r.writes.at(-1),{route:'quotations/q1/decision/rejected',method:'POST',body:JSON.stringify({reason:'Over budget'})});
});

test('decision actions follow status, revision and role rules',()=>{
 const shortlisted=screen(view({data:quote({status:'shortlisted'})}).render());
 assert.equal(find(shortlisted,'button','Shortlist quotation'),undefined);assert.ok(find(shortlisted,'button','Award quotation'));
 const awarded=screen(view({data:quote({status:'awarded'})}).render());
 assert.equal(nodes(awarded).filter(node=>node.type==='button'&&/Award quotation|Reject quotation|Shortlist/.test(text(node))).length,0);
 assert.match(html(awarded),/This quotation has been awarded/);
 const stale=screen(view({data:quote({review:true})}).render());
 assert.equal(find(stale,'button','Award quotation').props.disabled,true);
 assert.match(html(stale),/supplier must revise it before it can be awarded/);
 assert.equal(find(screen(view({rfqRevision:2}).render()),'button','Award quotation').props.disabled,true,'an older RFQ revision cannot be awarded');
 const admin=view({role:'admin'}).render();
 assert.equal(find(screen(admin),'h2','Buyer decision'),undefined,'admins review read-only');
 assert.equal(find(screen(admin),'a','Back to Quotations').props.href,'/app/admin/quotations');
});

test('print sheet holds every line, the terms and the same totals',()=>{
 const lines=Array.from({length:25},(_,index)=>({rfq_item_id:`x${index}`,description:`Item ${index}`,quantity:'1.000',unit:'EA',unit_price:'1.00',line_total:'1.00',lead_days:3,compliance:'compliant'}));
 const tree=view({data:quote({lines})}).render();
 const print=nodes(tree).filter(node=>node.props.className==='printOnly');
 const sheet=print.find(node=>nodes(node).some(child=>child.type==='table'));
 assert.equal(nodes(sheet).filter(node=>node.type==='tbody').flatMap(body=>nodes(body).filter(node=>node.type==='tr')).length,25);
 assert.match(html(sheet),/30 days from invoice/);assert.match(html(sheet),/1,190.25/);
});

test('Arabic: translated labels, RTL, supplier text untouched',()=>{
 const tree=view({arabic:true}).render(),out=html(tree);
 assert.equal(tree.props.dir,'rtl');
 for(const value of ['العودة إلى عروض الأسعار','المواد المسعّرة','الملخص المالي','قرار المشتري','ترسية عرض السعر','كهرباء'])assert.match(out,new RegExp(value),value);
 assert.match(out,/Acme Materials/);assert.match(out,/Cu\/XLPE\/PVC 4mm² cable/);
});

test('model: exact amounts, percentages, validity and transitions',()=>{
 assert.equal(model.formatAmount('1234567.5'),'1,234,567.50');assert.equal(model.formatAmount('0.00'),'0.00');assert.equal(model.formatAmount(null),null);
 assert.equal(model.formatQuantity('12.000'),'12');assert.equal(model.formatQuantity('2.500'),'2.5');
 assert.equal(model.percent('0.1500'),'15%');assert.equal(model.percent('0.0750'),'7.5%');assert.equal(model.percent('0'),'0%');assert.equal(model.percent(null),null);
 assert.equal(model.isPositive('0.00'),false);assert.equal(model.isPositive('0.01'),true);
 const now=Date.parse('2026-11-28T12:00:00Z');
 assert.equal(model.validity(null,now),null);
 assert.equal(model.validity('2026-11-20',now).expired,true);
 assert.equal(model.validity('2026-12-05',now).daysLeft,8);
 assert.deepEqual([...model.allowedDecisions(quote(),0).decisions],['shortlisted','awarded','rejected']);
 assert.deepEqual([...model.allowedDecisions(quote({status:'rejected'}),0).decisions],[]);
 assert.equal(model.allowedDecisions(quote({review:true}),0).awardBlocked,'revision');
 assert.equal(model.normalizeCompliance('compliant_by_match'),'unconfirmed');
 assert.equal(model.sourceKind('automatic_draft'),'automatic');assert.equal(model.sourceKind('magic_link'),'manual');
});
