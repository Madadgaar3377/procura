const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');const {createRequire}=require('node:module');
const ts=require('typescript');const React=require('react');const {renderToStaticMarkup}=require('react-dom/server');
const plan={id:'isolated',name:'Actual API plan',slug:'actual-api-plan',description:'Configured description',monthly_price:'123.45',yearly_price:'1200.00',billing_intervals:['monthly','yearly'],features:['Included API benefit'],feature_details:[{label:'Included API benefit',included:true},{label:'Excluded API benefit',included:false}],recommended:true,available_for_subscription:true};
function render({plans=[plan],locale='en',error,loading=false,member=false}={}){
 const filename=path.resolve(__dirname,'../features/marketing/live-pricing.tsx');const realRequire=createRequire(filename);const exported={};
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 vm.runInNewContext(source,{exports:exported,require:name=>name==='@/lib/api/use-api'?{useApi:route=>({data:route==='memberships/plans'?plans:{free_launch_mode:true,paid_memberships_enabled:false},error,loading,reload(){}})}:name==='next/link'?{__esModule:true,default:({children,...props})=>React.createElement('a',props,children)}:realRequire(name)});
 return renderToStaticMarkup(React.createElement(exported.default,{locale,member}));
}
test('live pricing renders API amounts, recommendation and included/excluded configured features without paid checkout',()=>{
 const html=render();assert.match(html,/123\.45/);assert.match(html,/Actual API plan/);assert.match(html,/Recommended/);assert.match(html,/Included API benefit/);assert.match(html,/Excluded API benefit/);assert.match(html,/Not included/);assert.match(html,/disabled=""/);assert.ok(!html.includes('checkout'));
});
test('empty, loading and failure states never substitute fabricated plans',()=>{
 assert.match(render({plans:[]}),/Membership plans are coming soon/);
 assert.match(render({loading:true}),/Loading plans/);
 const failure=render({error:new Error('test')});assert.match(failure,/Unable to load/);assert.ok(!failure.includes('Actual API plan')&&!failure.includes('Starter'));
});
test('free available plans offer registration while unavailable plans do not',()=>{
 assert.match(render({plans:[{...plan,monthly_price:'0.00'}]}),/register\?role=supplier/);
 assert.ok(!render({plans:[{...plan,monthly_price:'0.00',available_for_subscription:false}]}).includes('register?role=supplier'));
});
test('Arabic pricing uses RTL and translated billing/coming-soon controls',()=>{
 const html=render({locale:'ar'});assert.match(html,/dir="rtl"/);assert.match(html,/شهري/);assert.match(html,/قريباً/);assert.match(html,/Actual API plan/);
});

test('existing suppliers request membership changes without creating another account',()=>{
 const html=render({member:true,plans:[{...plan,monthly_price:'0.00'}]});assert.match(html,/href="\/contact"/);assert.match(html,/Request membership change/);assert.ok(!html.includes('register?role=supplier'));
});
