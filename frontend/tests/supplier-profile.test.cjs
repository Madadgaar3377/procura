const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
class ApiError extends Error{constructor(status){super('Unavailable');this.status=status;}}
const supplier={id:'supplier-id',name:'Original Company Name',city:'Dammam',verified:true,
 profile:{description:'Original company introduction',business_phone:'+966500001234',business_email:'sales@example.test',website:'https://supplier.example.test',company_type:'both'},
 capability_groups:[{id:'electrical',name_en:'Electrical',name_ar:'الكهرباء',subcategories:[{id:'tools',name_en:'Tools',name_ar:'الأدوات'}]}],service_cities:['Riyadh','Jeddah']};
function render({data=supplier,arabic=false,error,loading=false,related=[],promotions={premium:false,featured:false}}={}){
 const filename=path.resolve(__dirname,'../features/suppliers/profile.tsx');const localRequire=createRequire(filename);const exported={};
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const dependencies=name=>name==='@/lib/locale'?{useLocale:()=>({arabic,t:value=>value})}:name==='@/lib/api/use-api'?{useApi:route=>route.startsWith('memberships/')?{data:promotions}:route.includes('/related')?{data:related,loading:false,reload(){}}:{data,error,loading,reload(){}}}:name==='@/lib/api/errors'?{ApiError}:name==='next/link'?{default:({children,...props})=>React.createElement('a',props,children),__esModule:true}:name==='@/features/marketing/chrome'?{MarketingCTA:({role,children})=>React.createElement('a',{href:role==='buyer'?'/register?role=buyer':'/register?role=supplier'},children)}:name==='./promotion-badges'?loadBadges():localRequire(name);
 function loadBadges(){const output={};const badgeSource=ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../features/suppliers/promotion-badges.tsx'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;vm.runInNewContext(badgeSource,{exports:output,require:dependencies});return output;}
 vm.runInNewContext(source,{exports:exported,require:dependencies});
 return renderToStaticMarkup(React.createElement(exported.default,{id:'supplier-id'}));
}
test('supplier profile renders authorized contact actions, grouped services and distinct office/coverage',()=>{
 const html=render();
 assert.match(html,/href="tel:\+966500001234"/);
 assert.match(html,/href="mailto:sales%40example.test\?subject=Procurement%20Enquiry%20via%20(Procumaterial|Procura)"/);
 assert.match(html,/href="https:\/\/supplier.example.test" target="_blank" rel="noopener noreferrer"/);
 assert.match(html,/Original company introduction/);assert.match(html,/Original Company Name/);
 assert.match(html,/Office City:/);assert.match(html,/Dammam/);assert.match(html,/Riyadh/);assert.match(html,/Jeddah/);
 assert.match(html,/main_category_id=electrical&amp;subcategory_id=tools/);
 assert.match(html,/href="\/register\?role=buyer"/);
 assert.ok(!html.includes('unit_price')&&!html.includes('cr_number'));
});
test('unpublished contacts do not create empty call/email/website actions',()=>{
 const html=render({data:{...supplier,profile:{}}});
 assert.ok(!html.includes('href="tel:')&&!html.includes('href="mailto:')&&!html.includes('supplier.example.test'));
 assert.match(html,/Information not provided/);assert.match(html,/has not published a business phone or email/);
});

test('contact actions are not duplicated and small capability lists need no search',()=>{
 const html=render();
 assert.equal((html.match(/href="tel:/g)||[]).length,1);
 assert.equal((html.match(/href="mailto:/g)||[]).length,1);
 assert.equal((html.match(/href="\/register\?role=buyer"/g)||[]).length,2);
 assert.ok(!html.includes('type="search"'));
 assert.match(html,/1 subcategories/);
});

test('large capability lists support search without publishing pending selections',()=>{
 const html=render({data:{...supplier,capability_groups:[{...supplier.capability_groups[0],subcategories:Array.from({length:13},(_,i)=>({id:String(i),name_en:'Service '+i}))}]}});
 assert.match(html,/type="search"/);
 const empty=render({data:{...supplier,capability_groups:[]}});
 assert.match(empty,/Verified Company/);
 assert.match(empty,/No approved capabilities published/);
 assert.ok(!empty.includes('Tools'));
});
test('Arabic profile preserves source company text and uses translated RTL sections',()=>{
 const html=render({arabic:true});assert.match(html,/dir="rtl"/);assert.match(html,/Original Company Name/);
 assert.match(html,/Original company introduction/);assert.match(html,/الأدوات/);assert.match(html,/الاتصال بالمورد/);assert.match(html,/موردون ذوو صلة/);
});
test('loading and 404/error states keep a meaningful page instead of blank content',()=>{
 const loading=render({data:null,loading:true});assert.match(loading,/role="status"/);assert.match(loading,/sp-skeleton/);assert.match(loading,/Supplier Directory/);
 const missing=render({data:null,error:new ApiError(404)});assert.match(missing,/Company unavailable/);assert.match(missing,/Back to directory/);
 const failed=render({data:null,error:new ApiError(503)});assert.match(failed,/Unable to load supplier/);assert.match(failed,/>Retry<\/button>/);
});
test('related supplier cards show actual returned suppliers and shared subcategory links',()=>{
 const html=render({related:[{id:'related-id',name:'Actual Related Company',city:'Abha',verified:true,service_cities:['Riyadh'],shared_subcategories:[{id:'tools',name_en:'Tools',name_ar:'الأدوات'}]}]});
 assert.match(html,/Actual Related Company/);assert.match(html,/href="\/suppliers\/related-id\?lang=en"/);assert.match(html,/Tools/);
 assert.match(render(),/No related suppliers are available/);
});

 test('profile displays Premium and sponsored labels only for returned active entitlements',()=>{
 const active=render({promotions:{premium:true,featured:true}});
 assert.match(active,/Premium/);assert.match(active,/sponsored placement/);
 const absent=render();assert.ok(!absent.includes('Premium')&&!absent.includes('sponsored placement'));
 });
