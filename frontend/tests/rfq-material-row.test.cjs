const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const ts=require('typescript');
const React=require('react');

function harness(arabic=false, overrides={}){
 const states=[],timers=new Map();let slot=0,sequence=0,tree,paths=[];
 let line={key:'row',main_category_id:'main',subcategory_id:'steel',material_id:null,description:'',unit:'',quantity:'1',...overrides};
 let records=Array.from({length:20},(_,i)=>({id:'mat-'+i,name_en:'Rebar '+i,name_ar:'',code:'CODE-'+i,unit:'M',main_category_id:'main',subcategory_id:'steel'}));
 const empty=()=>null;
 const dependencies={
  react:{...React,useId:()=> 'combo',useEffect:effect=>effect(),useRef:initial=>{const i=slot++;if(!(i in states))states[i]={current:initial};return states[i];},useState:initial=>{const i=slot++;if(!(i in states))states[i]=initial;return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];}},
  '@/lib/api/client':{api:async()=>({main_category_id:'main',subcategory_id:'steel',suggested_unit:'M'}),json:value=>JSON.stringify(value)},
  '@/lib/api/use-api':{useApi:p=>{paths.push(p);return {data:p?.startsWith('taxonomy/materials?')?records:p?.startsWith('taxonomy/materials/')?records[0]:undefined,loading:false,reload:empty};}},
  '@/lib/locale':{useLocale:()=>({t:s=>s,arabic})},
  '@/features/shared/searchable-select':{SearchableSelect:'category-select'},
  '@/features/shared/field-error':{FieldError:empty},
  '@/features/shared/loading':{RetryButton:empty},
  '@/components/ui/button':{Button:'button'},
  // Pure helper, transpiled for real (not stubbed): buyer wording is preserved on selection.
  './buyer-description':(()=>{const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../features/rfqs/buyer-description.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports});return exports;})(),
 };
 const filename=path.resolve(__dirname,'../features/rfqs/material-row.tsx');const localRequire=createRequire(filename);const exports={};
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 vm.runInNewContext(source,{exports,require:name=>dependencies[name]||localRequire(name),URLSearchParams,setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id),document:{getElementById:()=>null}});
 function render(){slot=0;paths=[];tree=exports.MaterialRow({line,index:0,categories:[{id:'main',name_en:'Main'},{id:'other',name_en:'Other'},{id:'steel',parent_id:'main',name_en:'Steel'}],units:[{code:'M'}],onChange:next=>{line=next;},onRemove:empty});return tree;}
 function nodes(node,out=[]){if(!node||typeof node!=='object')return out;if(Array.isArray(node)){node.forEach(n=>nodes(n,out));return out;}out.push(node);nodes(node.props?.children,out);return out;}
 render();
 return {render,nodes,get line(){return line;},get paths(){return paths;},flush(){const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());},find:predicate=>nodes(tree).find(predicate),shortPage(){records=records.slice(0,8);}};
}

test('autocomplete searches within the selected canonical category',()=>{
 const h=harness(false,{description:'Rebar'});assert.equal(h.paths[0],null);
 h.find(n=>n.props?.role==='combobox').props.onFocus();h.flush();h.render();
 assert.match(h.paths[0],/subcategory_id=steel/);
 assert.match(h.paths[0],/main_category_id=main/);
});

test('Create RFQ searches Material Master before any category is selected',()=>{
 const h=harness(false,{main_category_id:null,subcategory_id:null,description:'Rebar'});
 h.find(n=>n.props?.role==='combobox').props.onFocus();h.flush();h.render();
 assert.match(h.paths[0],/q=Rebar/);
 assert.doesNotMatch(h.paths[0],/main_category_id|subcategory_id/);
 h.find(n=>n.props?.role==='option').props.onClick();
 assert.equal(h.line.main_category_id,'main');assert.equal(h.line.subcategory_id,'steel');
 assert.equal(h.line.material_id,'mat-0');assert.equal(h.line.unit,'M');
});

test('selection fills ID/unit, custom text unlinks master, category change retains buyer text',()=>{
 const h=harness(false,{description:'Rebar'});h.find(n=>n.props?.role==='combobox').props.onFocus();h.flush();h.render();
 h.find(n=>n.props?.role==='option').props.onClick();assert.equal(h.line.material_id,'mat-0');assert.equal(h.line.unit,'M');h.render();
 h.find(n=>n.props?.role==='combobox').props.onChange({target:{value:'Custom RFQ text'}});h.render();
 assert.equal(h.line.material_id,null);assert.equal(h.paths[0],null,'do not request outdated search while debounce is pending');
 h.flush();h.render();assert.match(h.paths[0],/q=Custom/);
 h.find(n=>n.type==='category-select').props.onChange('other');h.render();
 assert.equal(h.line.material_id,null);assert.equal(h.line.subcategory_id,null);assert.equal(h.line.description,'Custom RFQ text');assert.equal(h.line.unit,'M');
});

test('custom description keeps the existing automatic classification path',async()=>{
 const h=harness(false,{main_category_id:null,subcategory_id:null});
 h.find(n=>n.props?.role==='combobox').props.onChange({target:{value:'Custom steel component'}});
 h.render();h.flush();await new Promise(setImmediate);
 assert.equal(h.line.material_id,null);
 assert.equal(h.line.main_category_id,'main');
 assert.equal(h.line.subcategory_id,'steel');
 assert.equal(h.line.unit,'M');
});


test('Arabic direction and independent rows retain independent selections',()=>{
 const first=harness(true),second=harness();
 assert.equal(first.find(n=>n.type==='fieldset').props.dir,'rtl');
 first.find(n=>n.props?.role==='combobox').props.onChange({target:{value:'Rebar'}});first.render();first.flush();first.render();
 first.find(n=>n.props?.role==='option').props.onClick();
 assert.equal(first.line.material_id,'mat-0');assert.equal(second.line.material_id,null);
});

test('specification and positive decimal quantity remain separate RFQ fields',()=>{
 const h=harness();
 const specs=h.find(n=>n.type==='textarea');
 specs.props.onChange({target:{value:'Pressure 16 bar\nBrand approved'}});
 assert.equal(h.line.specifications,'Pressure 16 bar\nBrand approved');
 h.render();
 const quantity=h.find(n=>n.type==='input'&&n.props.name==='items.0.quantity');
 assert.equal(quantity.props.step,'0.001');
 quantity.props.onChange({target:{value:'2.375'}});
 assert.equal(h.line.quantity,'2.375');
});

test('Edit RFQ restores a saved Material Master line and keeps its RFQ fields editable',()=>{
 const h=harness(false,{material_id:'mat-0',main_category_id:null,subcategory_id:null,unit:''});
 h.render();
 assert.equal(h.line.main_category_id,'main');
 assert.equal(h.line.subcategory_id,'steel');
 assert.equal(h.line.unit,'M');
 h.find(n=>n.type==='textarea').props.onChange({target:{value:'Buyer specification'}});
 h.render();
 h.find(n=>n.type==='input'&&n.props.name==='items.0.quantity').props.onChange({target:{value:'4.5'}});
 assert.equal(h.line.material_id,'mat-0');
 assert.equal(h.line.specifications,'Buyer specification');
 assert.equal(h.line.quantity,'4.5');
});
