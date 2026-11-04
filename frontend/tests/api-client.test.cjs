const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
function load(fetch){
 const exports={};
 class ApiError extends Error{constructor(status,detail,retryAfter){super(String(detail));Object.assign(this,{status,detail,retryAfter});}}
 const source=ts.transpileModule(fs.readFileSync(require.resolve('../lib/api/client.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(source,{exports,require:()=>({ApiError}),process:{env:{NEXT_PUBLIC_API_URL:'http://127.0.0.1:8000/api/v1'}},Headers,AbortSignal,FormData,Event,setTimeout,clearTimeout,fetch,window:{location:{protocol:'http:',hostname:'localhost'},dispatchEvent:()=>{}}});
 return exports;
}
const result=(data,status=200,headers={})=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json',...headers}});

test('browser login and ordinary requests go directly to FastAPI with credentials',async()=>{
 const calls=[];const {api}=load(async(url,init)=>{calls.push({url,...init});return result({authenticated:true});});
 await api('auth/login',{method:'POST',body:JSON.stringify({identifier:'user',password:'123456'})});
 await api('admin/users');
 assert.equal(calls[0].url,'http://127.0.0.1:8000/api/v1/auth/browser/login');
 assert.equal(calls[1].url,'http://127.0.0.1:8000/api/v1/admin/users');
 assert.ok(calls.every(c=>c.credentials==='include'));
 assert.ok(calls.every(c=>!c.headers.has('Authorization')));
});

test('duplicate in-flight GETs share one backend request',async()=>{
 let calls=0;const {api}=load(async()=>{calls++;await new Promise(r=>setTimeout(r,10));return result({username:'shared'});});
 const values=await Promise.all([api('auth/me'),api('auth/me')]);
 assert.equal(calls,1);assert.equal(values[0].username,'shared');assert.equal(values[1].username,'shared');
});

test('expired cookies trigger one shared refresh then retry protected requests',async()=>{
 let refreshes=0;const hits=new Map();
 const {api}=load(async(url)=>{if(url.endsWith('/auth/browser/refresh')){refreshes++;await new Promise(r=>setTimeout(r,10));return result({authenticated:true});}const n=hits.get(url)||0;hits.set(url,n+1);return n?result({ok:true}):result({detail:'Expired'},401);});
 await Promise.all([api('auth/me'),api('organizations')]);
 assert.equal(refreshes,1);assert.equal([...hits.values()].reduce((a,b)=>a+b,0),4);
});

test('busy database failures preserve status and retry guidance without repeating mutations',async()=>{
 let calls=0;const {api}=load(async()=>{calls++;return result({detail:'Database busy'},503,{'Retry-After':'3'});});
 await assert.rejects(api('admin/users/id',{method:'DELETE'}),e=>e.status===503&&e.retryAfter==='3');
 assert.equal(calls,1);
});


test('transient read failures retry once while mutations never retry',async()=>{
 let calls=0;const {api}=load(async()=>++calls===1?result({detail:'Busy'},503):result({ok:true}));
 assert.equal((await api('reports/summary')).ok,true);assert.equal(calls,2);
});
test('cancellation is forwarded and never becomes a successful empty response',async()=>{
 const controller=new AbortController();controller.abort();
 const {api}=load(async(url,init)=>{init.signal.throwIfAborted();return result({});});
 await assert.rejects(api('rfqs',{signal:controller.signal}),e=>e.name==='AbortError');
});
test('offline backend produces useful connection guidance',async()=>{
 const {api}=load(async()=>{throw new TypeError('Failed to fetch');});
 await assert.rejects(api('rfqs'),/could not be reached/);
});
