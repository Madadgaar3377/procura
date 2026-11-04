const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const path=require('node:path');
const ts=require('typescript');
const {NextRequest}=require('next/server');
function load(fetch){
 const filename=path.resolve(__dirname,'../lib/api/server.ts');
 const localRequire=createRequire(filename);
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
 const exports={};
 vm.runInNewContext(source,{exports,require:name=>name==='server-only'?{}:localRequire(name),process:{env:{NODE_ENV:'test',BACKEND_URL:'http://127.0.0.1:8000'}},URL,Headers,AbortSignal,setTimeout:(fn)=>{queueMicrotask(fn);return 0;},fetch,console});
 return exports.proxy;
}
const result=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
const request=(route,method='GET',cookie='',body)=>new NextRequest('http://localhost:3000/api/backend/'+route,{method,headers:{origin:'http://localhost:3000',cookie,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
test('login stores tokens only in HttpOnly cookies',async()=>{
 const proxy=load(async()=>result({access_token:'access-secret',refresh_token:'refresh-secret',token_type:'bearer'}));
 const response=await proxy(request('auth/login','POST','',{identifier:'test',password:'local-only'}),['auth','login']);
 assert.equal(response.status,200);assert.deepEqual(await response.json(),{authenticated:true});
 const cookies=response.headers.get('set-cookie');assert.match(cookies,/HttpOnly/i);assert.match(cookies,/SameSite=lax/i);assert.match(cookies,/procura_refresh=/);
 assert.equal(response.cookies.get('procura_refresh').maxAge,604800);
});

test('returning browser with only a refresh cookie restores authentication',async()=>{
 const calls=[];
 const proxy=load(async(url,init)=>{assert.equal(init.redirect,'error');calls.push({url,authorization:new Headers(init.headers).get('Authorization')});return url.endsWith('/auth/refresh')?result({access_token:'restored-access',refresh_token:'rotated-refresh'}):result({username:'returning-user'});});
 const response=await proxy(request('auth/me','GET','procura_refresh=saved-refresh'),['auth','me']);
 assert.equal(response.status,200);
 assert.equal(calls.length,2);
 assert.equal(calls[1].authorization,'Bearer restored-access');
 assert.equal(response.cookies.get('procura_refresh').maxAge,604800);
 assert.deepEqual(await response.json(),{username:'returning-user'});
});
test('mutations reject missing and foreign origins before contacting backend',async()=>{
 let calls=0;const proxy=load(async()=>{calls++;return result({});});
 for(const origin of [undefined,'https://foreign.example']){
 const response=await proxy(new NextRequest('http://localhost:3000/api/backend/auth/login',{method:'POST',headers:origin?{origin}:{}}),['auth','login']);assert.equal(response.status,403);
 }assert.equal(calls,0);
});
test('proxy blocks unknown paths and direct refresh token transport',async()=>{
 let calls=0;const proxy=load(async()=>{calls++;return result({});});
 assert.equal((await proxy(request('not-a-route'),['not-a-route'])).status,404);
 assert.equal((await proxy(request('auth/refresh','POST'),['auth','refresh'])).status,403);
 assert.equal((await proxy(request('rfqs'),['..','rfqs'])).status,404);assert.equal(calls,0);
});
test('expired access refreshes once, retries request and rotates cookies',async()=>{
 const calls=[];const proxy=load(async(url,init)=>{calls.push({url,headers:new Headers(init.headers)});if(url.endsWith('/auth/refresh'))return result({access_token:'new-access',refresh_token:'new-refresh'});return calls.length===1?result({detail:'Expired'},401):result({username:'buyer'});});
 const response=await proxy(request('auth/me','GET','procura_access=old; procura_refresh=refresh'),['auth','me']);
 assert.equal(response.status,200);assert.equal(calls.length,3);assert.equal(calls[2].headers.get('Authorization'),'Bearer new-access');assert.match(response.headers.get('set-cookie'),/new-refresh/);assert.deepEqual(await response.json(),{username:'buyer'});
});
test('failed refresh clears cookies and keeps the unauthorized result',async()=>{
 const proxy=load(async()=>result({detail:'Expired'},401));const response=await proxy(request('auth/me','GET','procura_access=old; procura_refresh=refresh'),['auth','me']);assert.equal(response.status,401);assert.match(response.headers.get('set-cookie'),/Max-Age=0/);
});
test('OTP development secrets never reach browser and rate limit feedback survives',async()=>{
 let proxy=load(async()=>result({message:'Queued',development_otp:'123456',development_code:'654321'}));let response=await proxy(request('auth/otp/request','POST'),['auth','otp','request']);assert.deepEqual(await response.json(),{message:'Queued'});
 proxy=load(async()=>new Response(JSON.stringify({detail:'Too many requests'}),{status:429,headers:{'Retry-After':'60'}}));response=await proxy(request('auth/me'),['auth','me']);assert.equal(response.status,429);assert.equal(response.headers.get('Retry-After'),'60');
});
test('backend outage is reported as failure without demo data',async()=>{
 const proxy=load(async()=>{throw new Error('do-not-leak-connection-details');});const response=await proxy(request('auth/me'),['auth','me']);assert.equal(response.status,502);assert.deepEqual(await response.json(),{detail:'The backend is unavailable'});
});

test('chunked uploads are bounded before forwarding upstream',async()=>{
 let calls=0;const proxy=load(async()=>{calls++;return result({});});
 const r=new NextRequest('http://localhost:3000/api/backend/imports/suppliers',{method:'POST',headers:{origin:'http://localhost:3000'},body:new Uint8Array(8_100_001)});
 const response=await proxy(r,['imports','suppliers']);assert.equal(response.status,413);assert.equal(calls,0);
});

test('password recovery verification stays public with an expired session cookie',async()=>{
 const calls=[];
 const proxy=load(async(url,init)=>{calls.push(url);return result({verified:true,reset_token:'limited-reset-token',development_code:'123456'});});
 const response=await proxy(request('auth/password/verify','POST','procura_refresh=stale',{identifier:'+966511111200',code:'123456'}),['auth','password','verify']);
 assert.equal(response.status,200);
 assert.equal(calls.length,1);
 assert.ok(calls[0].endsWith('/auth/password/verify'));
 assert.deepEqual(await response.json(),{verified:true,reset_token:'limited-reset-token'});
 assert.equal(response.headers.get('set-cookie'),null);
});

test('user administration forwards authorization and preserves backend denials',async()=>{
 const calls=[];
 const proxy=load(async(url,init)=>{calls.push({url,authorization:new Headers(init.headers).get('Authorization'),method:init.method});return result({detail:'Administrator access required'},403);});
 const response=await proxy(request('admin/users/user-id','PATCH','procura_access=ordinary-user',{is_admin:true}),['admin','users','user-id']);
 assert.equal(response.status,403);
 assert.deepEqual(calls,[{url:'http://127.0.0.1:8000/api/v1/admin/users/user-id',authorization:'Bearer ordinary-user',method:'PATCH'}]);
});
