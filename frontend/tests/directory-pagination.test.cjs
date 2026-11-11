const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const ts=require('typescript');

function directory({total=120,page=1,filters={},arabic=false}={}){
 const filename=path.resolve(__dirname,'../features/suppliers/directory.tsx');
 const localRequire=createRequire(filename),exports={};
 const state=[{q:'',main:'',sub:'',city:'',page,...filters},true,filters.q||''];
 const requests=[];let cursor=0;
 const dependencies=name=>name==='react'?{...React,useEffect(){},useState(initial){const index=cursor++;return [index<state.length?state[index]:initial,value=>{state[index]=typeof value==='function'?value(state[index]):value;}];}}:
  name==='@/lib/locale'?{useLocale:()=>({arabic})}:
  name==='@/lib/api/use-api'?{useApi:route=>{
   if(!route)return {fetching:false,reload(){}};
   if(!route.startsWith('directory/suppliers?'))return {data:[],fetching:false,reload(){}};
   requests.push(route);const params=new URLSearchParams(route.split('?')[1]);
   const current=Number(params.get('page')),limit=Number(params.get('limit'));
   const count=Math.min(limit,Math.max(0,total-(current-1)*limit));
   return {data:{items:Array.from({length:count},(_,i)=>({id:`supplier-${(current-1)*limit+i}`,name:`Supplier ${(current-1)*limit+i}`,city:'Riyadh',verified:true})),total,page:current,limit},fetching:false,reload(){}};
  }}:
  name==='next/link'?{__esModule:true,default:({children,...props})=>React.createElement('a',props,children)}:
  name==='@/features/marketing/chrome'?{MarketingCTA:({children})=>React.createElement('a',null,children)}:localRequire(name);
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 vm.runInNewContext(source,{exports,require:dependencies,URLSearchParams});
 const render=()=>{cursor=0;return exports.default({});};
 return {render,state,requests};
}
function elements(tree,result=[]){
 if(Array.isArray(tree)){for(const child of tree)elements(child,result);return result;}
 if(!React.isValidElement(tree))return result;
 result.push(tree);elements(tree.props.children,result);return result;
}
function button(tree,label){return elements(tree).find(node=>node.type==='button'&&node.props.children===label);}

test('directory requests only 50 records, displays totals and moves between server pages',()=>{
 const view=directory();let tree=view.render();
 assert.match(renderToStaticMarkup(tree),/Showing 1–50 of 120 suppliers/);
 assert.equal(elements(tree).filter(node=>node.props.className==='directory-card').length,50);
 assert.equal(button(tree,'Previous').props.disabled,true);
 assert.equal(button(tree,'Next').props.disabled,false);
 let params=new URLSearchParams(view.requests.at(-1).split('?')[1]);
 assert.equal(params.get('limit'),'50');assert.equal(params.get('paginated'),'true');
 button(tree,'Next').props.onClick();tree=view.render();
 assert.match(renderToStaticMarkup(tree),/Showing 51–100 of 120 suppliers/);
 params=new URLSearchParams(view.requests.at(-1).split('?')[1]);assert.equal(params.get('page'),'2');
 button(tree,'Previous').props.onClick();view.render();assert.equal(view.state[0].page,1);
});

test('Next is disabled on an exact multiple of 50 and on an empty result',()=>{
 const last=directory({total:100,page:2});const tree=last.render();
 assert.match(renderToStaticMarkup(tree),/Showing 51–100 of 100 suppliers/);
 assert.equal(button(tree,'Next').props.disabled,true);
 const empty=directory({total:0});const zero=empty.render();
 assert.match(renderToStaticMarkup(zero),/Showing 0–0 of 0 suppliers/);
 assert.equal(button(zero,'Next').props.disabled,true);
});

test('all filters reach the server and each change resets the page',()=>{
 const view=directory({page:2,filters:{q:'Panels',main:'electrical',sub:'panels',city:'Riyadh'}});
 let tree=view.render();const params=new URLSearchParams(view.requests.at(-1).split('?')[1]);
 assert.equal(params.get('q'),'Panels');assert.equal(params.get('main_category_id'),'electrical');
 assert.equal(params.get('subcategory_id'),'panels');assert.equal(params.get('service_city'),'Riyadh');
 for(const key of ['search','main','sub','city']){
  view.state[0]={...view.state[0],page:2};tree=view.render();const nodes=elements(tree);
  if(key==='search')nodes.find(node=>node.type==='input'&&node.props.type==='search').props.onChange({target:{value:'Pumps'}});
  if(key==='main')nodes.find(node=>node.props.label==='Main Category').props.onChange('mechanical');
  if(key==='sub')nodes.find(node=>node.props.label==='Subcategory').props.onChange('pumps');
  if(key==='city')nodes.find(node=>node.type==='input'&&node.props.placeholder==='Enter saved service city').props.onChange({target:{value:'Jeddah'}});
  assert.equal(view.state[0].page,1,key);
 }
});

test('Arabic directory shows the same accurate page range',()=>{
 const view=directory({page:3,arabic:true});const html=renderToStaticMarkup(view.render());
 assert.match(html,/عرض 101–120 من 120 مورد/);assert.match(html,/dir="rtl"/);
});
