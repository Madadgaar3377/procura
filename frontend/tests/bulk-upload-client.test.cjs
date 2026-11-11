const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');

function client(reply,{hostname='procumaterial.com',configured='https://api.example.test/api/v1',fetch=async()=>new Response('{}',{status:401})}={}){
 const requests=[];
 class XHR {
  constructor(){this.listeners={};this.upload={addEventListener:()=>{}};this.headers={};this.status=0;this.responseText='';requests.push(this);}
  open(method,url){Object.assign(this,{method,url});}
  setRequestHeader(key,value){this.headers[key.toLowerCase()]=value;}
  getResponseHeader(key){return key==='X-Request-ID'?'upload-test-id':null;}
  addEventListener(type,listener){this.listeners[type]=listener;}
  send(body){this.body=body;queueMicrotask(()=>reply(this));}
 }
 function compile(file){return ts.transpileModule(fs.readFileSync(require.resolve(file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;}
 const errors={};vm.runInNewContext(compile('../lib/api/errors.ts'),{exports:errors});
 const exports={};
 vm.runInNewContext(compile('../lib/api/client.ts'),{exports,require:()=>errors,process:{env:{NEXT_PUBLIC_API_URL:configured}},Headers,AbortSignal,FormData,Event,setTimeout,clearTimeout,fetch,XMLHttpRequest:XHR,window:{location:{protocol:'https:',hostname},dispatchEvent:()=>{}}});
 return {...exports,requests};
}

test('supplier jobs upload multipart with browser boundary and existing cookie credentials',async()=>{
 const api=client(xhr=>{xhr.status=202;xhr.responseText='{"batch_id":"saved-job"}';xhr.listeners.load();});
 const body=new FormData();body.set('file',new Blob(['excel']),'supplier.xlsx');
 assert.equal((await api.upload('imports/bulk/suppliers/jobs',body,()=>{})).batch_id,'saved-job');
 const request=api.requests[0];
 assert.equal(request.url,'https://api.example.test/api/v1/imports/bulk/suppliers/jobs');
 assert.equal(request.withCredentials,true);
 assert.equal(request.body,body);
 assert.equal(request.headers['content-type'],undefined);
});

for(const status of [401,403,413,422,429,500,502,504]){
 test(`HTTP ${status} remains an HTTP error with request ID, including streamed progress callbacks`,async()=>{
  const api=client(xhr=>{xhr.status=status;xhr.responseText='{"detail":"Upload rejected"}\n';xhr.listeners.progress();xhr.listeners.load();});
  await assert.rejects(api.uploadStream('imports/bulk/suppliers',new FormData(),()=>{},()=>{}),e=>e.status===status&&e.requestId==='upload-test-id'&&!e.message.includes('network'));
 });
}

test('www frontend uses configured backend and network interruption preserves received progress',async()=>{
 const events=[];
 const api=client(xhr=>{xhr.status=200;xhr.responseText='{"type":"started","batch_id":"job","progress_percent":0}\n{"type":"progress","progress_percent":50}\n';xhr.listeners.progress();xhr.listeners.error();},{hostname:'www.procumaterial.com'});
 await assert.rejects(api.uploadStream('imports/bulk/suppliers',new FormData(),()=>{},event=>events.push(event)),/could not reach/);
 assert.equal(events.at(-1).progress_percent,50);
 assert.equal(api.requests[0].url,'https://api.example.test/api/v1/imports/bulk/suppliers');
});

test('production cannot silently fall back to port 8000 or an unverified frontend API route',()=>{
 for(const hostname of ['procumaterial.com','www.procumaterial.com']){
  assert.throws(()=>client(()=>{},{hostname,configured:''}).apiUrl('imports/bulk/suppliers/jobs'),/Configure NEXT_PUBLIC_API_URL/);
 }
});

test('server-side import error is distinct from a connection failure',async()=>{
 const api=client(xhr=>{xhr.status=200;xhr.responseText='{"type":"error","message":"Database temporarily unavailable"}\n';xhr.listeners.progress();xhr.listeners.load();});
 await assert.rejects(api.uploadStream('imports/bulk/suppliers',new FormData(),()=>{},()=>{}),/Database temporarily unavailable/);
});
