const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const ts=require('typescript');

function render(admin){
 const tables=[];
 const base={event:'notification.delivery',created_at:'2026-11-12',payload:{procurement_recipient:true,channel:'email',destination:'sales@example.com',recipient_source:'company',context:{reference:'RFQ-TEST',supplier_name:'Imported nameless supplier'}}};
 const jobs=[{...base,id:'email',status:'processed',delivery_status:'accepted'},
  {...base,id:'sms',status:'skipped',delivery_status:'no_contact',payload:{...base.payload,channel:'sms',destination:undefined,reason_detail:'No phone available'}},
  {...base,id:'uncertain',status:'uncertain',delivery_status:'submitting'}];
 const empty=()=>null;
 const dependencies={
  react:{...React,useEffect:()=>{}},
  '@/lib/locale':{useLocale:()=>({t:value=>value})},
  '@/features/shared/server-list':{useServerList:()=>({rows:[],controls:null,reload:empty})},
  '@/lib/api/use-api':{useApi:route=>({data:route==='notifications/admin/outbox'?jobs:[],reload:empty})},
  '@/features/shared/ui':{Heading:empty,State:empty,Action:({children})=>React.createElement('button',null,children),
   DataTable:props=>{tables.push(props);return React.createElement('div',null,props.rows.map(row=>React.createElement('p',{key:row.id},
    props.columns.map(c=>String(row[c]||'')).join(' | '),props.action?.(row))));}}
 };
 const filename=path.resolve(__dirname,'../features/notifications/workspace.tsx');
 const localRequire=createRequire(filename);const exports={};
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 vm.runInNewContext(source,{exports,require:name=>dependencies[name]||localRequire(name)});
 const html=renderToStaticMarkup(React.createElement(exports.default,{admin}));
 return {html,tables};
}

test('admin RFQ rows show masked destinations and accurate independent outcomes',()=>{
 const {html}=render(true);
 assert.match(html,/RFQ-TEST/);assert.match(html,/Imported nameless supplier/);
 assert.match(html,/sa\.\.\.@example.com/);assert.doesNotMatch(html,/sales@example.com/);
 assert.match(html,/Provider accepted/);assert.match(html,/Skipped: No phone available/);
 assert.match(html,/Outcome uncertain/);assert.doesNotMatch(html,/â|Delivered/);
});

test('skipped and accepted jobs have no retry; uncertain jobs require non-acceptance attestation',()=>{
 const table=render(true).tables.at(-1);
 assert.equal(table.action(table.rows[0]),null);assert.equal(table.action(table.rows[1]),null);
 const action=table.action(table.rows[2]);
 assert.equal(action.props.path,'notifications/admin/outbox/uncertain/retry');
 assert.equal(action.props.body.confirmed_not_accepted,true);
 assert.match(action.props.confirm,/NOT accepted/);
});

test('ordinary notification view does not render admin supplier outbox',()=>{
 const {html,tables}=render(false);
 assert.equal(tables.length,2);assert.doesNotMatch(html,/Imported nameless supplier|RFQ-TEST|Retry delivery/);
});
