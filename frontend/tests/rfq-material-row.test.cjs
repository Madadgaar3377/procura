const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const ts=require('typescript');
const React=require('react');

function harness(arabic=false){
 const states=[],timers=new Map();let slot=0,sequence=0,tree,paths=[];
 let line={key:'row',main_category_id:'main',subcategory_id:'steel',material_id:null,description:'',unit:'',quantity:'1'};
 let records=Array.from({length:20},(_,i)=>({id:'mat-'+i,name_en:'Rebar '+i,name_ar:'',code:'CODE-'+i,unit:'M',main_category_id:'main',subcategory_id:'steel'}));
 const empty=()=>null;
 const dependencies={
  react:{...React,useId:()=> 'combo',useEffect:effect=>effect(),useState:initial=>{const i=slot++;if(!(i in states))states[i]=initial;return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];}},
  '@/lib/api/use-api':{useApi:p=>{paths.push(p);return {data:p?.startsWith('taxonomy/materials?')?records:undefined,loading:false,reload:empty};}},
  '@/lib/locale':{useLocale:()=>({t:s=>s,arabic})},
  '@/features/shared/searchable-select':{SearchableSelect:'category-select'},
  '@/features/shared/field-error':{FieldError:empty},
  '@/features/shared/loading':{RetryButton:empty},
  '@/components/ui/button':{Button:'button'},
 };
 const filename=path.resolve(__dirname,'../features/rfqs/material-row.tsx');const localRequire=createRequire(filename);const exports={};
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 vm.runInNewContext(source,{exports,require:name=>dependencies[name]||localRequire(name),URLSearchParams,setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id),document:{getElementById:()=>null}});
 function render(){slot=0;paths=[];tree=exports.MaterialRow({line,index:0,categories:[{id:'main',name_en:'Main'},{id:'other',name_en:'Other'},{id:'steel',parent_id:'main',name_en:'Steel'}],units:[{code:'M'}],onChange:next=>{line=next;},onRemove:empty});return tree;}
 function nodes(node,out=[]){if(!node||typeof node!=='object')return out;if(Array.isArray(node)){node.forEach(n=>nodes(n,out));return out;}out.push(node);nodes(node.props?.children,out);return out;}
 render();
 return {render,nodes,get line(){return line;},get paths(){return paths;},flush(){const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());},find:predicate=>nodes(tree).find(predicate),shortPage(){records=records.slice(0,8);}};
}

test('empty subcategory search and keyboard paging expose later materials',()=>{
 const h=harness();assert.match(h.paths[0],/subcategory_id=steel/);assert.match(h.paths[0],/q=&/);
 let input=h.find(n=>n.props?.role==='combobox');input.props.onFocus();h.render();
 input=h.find(n=>n.props?.role==='combobox');input.props.onKeyDown({key:'PageDown',preventDefault(){}});h.render();
 assert.match(h.paths[0],/offset=20/);
 h.shortPage();h.render();assert.equal(h.find(n=>n.type==='button'&&n.props.children==='Next').props.disabled,true);
});

test('selection fills ID/unit, description edit retains ID, category change clears dependents',()=>{
 const h=harness();h.find(n=>n.props?.role==='combobox').props.onFocus();h.render();
 h.find(n=>n.props?.role==='option').props.onClick();assert.equal(h.line.material_id,'mat-0');assert.equal(h.line.unit,'M');h.render();
 h.find(n=>n.props?.role==='combobox').props.onChange({target:{value:'Custom RFQ text'}});h.render();
 assert.equal(h.line.material_id,'mat-0');assert.equal(h.paths[0],null,'do not request outdated search while debounce is pending');
 h.flush();h.render();assert.match(h.paths[0],/q=Custom/);
 h.find(n=>n.type==='category-select').props.onChange('other');h.render();
 assert.equal(h.line.material_id,null);assert.equal(h.line.subcategory_id,null);assert.equal(h.line.description,'');assert.equal(h.line.unit,'');
});


test('Arabic direction and independent rows retain independent selections',()=>{
 const first=harness(true),second=harness();
 assert.equal(first.find(n=>n.props?.role==='combobox').props.dir,'rtl');
 first.find(n=>n.props?.role==='combobox').props.onFocus();first.render();
 first.find(n=>n.props?.role==='option').props.onClick();
 assert.equal(first.line.material_id,'mat-0');assert.equal(second.line.material_id,null);
});
