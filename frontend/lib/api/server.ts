import 'server-only';
import { NextRequest, NextResponse } from 'next/server';
import routes from './generated/routes.json';
import type { Schemas } from './generated/schema';
const accessName='procura_access';
const refreshName='procura_refresh';
const production=process.env.NODE_ENV==='production';
const options={httpOnly:true,sameSite:'lax' as const,secure:production,path:'/'};
type Pair=Pick<Schemas['TokenPair'],'access_token'|'refresh_token'>;
const refreshing=new Map<string,Promise<Pair|null>>();
function upstream() {
 const value=process.env.FASTAPI_INTERNAL_URL || process.env.BACKEND_URL || 'http://127.0.0.1:8000';
 const url=new URL(value);
 if(!['http:','https:'].includes(url.protocol) || url.username || url.password) throw new Error('Invalid FASTAPI_INTERNAL_URL');
 return url.origin;
}
function allowed(path:string,method:string) {
 return Object.entries(routes).some(([pattern,methods])=>methods.includes(method) && new RegExp('^'+pattern.replace(/\{[^}]+\}/g,'[^/]+')+'$').test(path));
}
function cookies(response:NextResponse,pair:Pair) {
 response.cookies.set(accessName,pair.access_token,{...options,maxAge:30*60});
 response.cookies.set(refreshName,pair.refresh_token,{...options,maxAge:7*24*60*60});
}
function clear(response:NextResponse) {
 for(const name of [accessName,refreshName]) response.cookies.set(name,'',{...options,maxAge:0});
}
async function rotate(refresh:string) {
 let task=refreshing.get(refresh);
 if(!task) {
  task=(async()=>{
   const r=await fetch(`${upstream()}/api/v1/auth/refresh`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh}),cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)});
   return r.ok ? await r.json() as Pair : null;
  })();
  refreshing.set(refresh,task);
  void task.finally(()=>setTimeout(()=>refreshing.delete(refresh),5000)).catch(()=>{});
 }
 return task;
}
class PayloadTooLarge extends Error {}
async function boundedBody(request:NextRequest):Promise<ArrayBuffer|undefined>{
 if(['GET','HEAD'].includes(request.method)||!request.body)return undefined;
 const reader=request.body.getReader();const chunks:Uint8Array[]=[];let size=0;
 while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>8_100_000){await reader.cancel();throw new PayloadTooLarge();}chunks.push(value);}
 const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}return bytes.buffer as ArrayBuffer;
}
export async function proxy(request:NextRequest,path:string[]) {
 const route=path.join('/');
 if(path.some(p=>p==='.'||p==='..'||p.includes('/')||p.includes('\\')) || !allowed(route,request.method)) return NextResponse.json({detail:'Route not available'},{status:404});
 if(!['GET','HEAD'].includes(request.method) && request.headers.get('origin')!==(process.env.FRONTEND_ORIGIN || (request.headers.get('host') ? `${request.nextUrl.protocol}//${request.headers.get('host')}` : request.nextUrl.origin))) return NextResponse.json({detail:'Request origin rejected'},{status:403});
 if(route==='auth/refresh') return NextResponse.json({detail:'Session refresh is managed by the server'},{status:403});
 const size=Number(request.headers.get('content-length')||0);
 if(size>8_100_000)return NextResponse.json({detail:'Upload too large'},{status:413});
 try {
  const body=await boundedBody(request);
  if(body && body.byteLength>8_100_000)return NextResponse.json({detail:'Upload too large'},{status:413});
  let access=request.cookies.get(accessName)?.value;
  const refresh=request.cookies.get(refreshName)?.value;
  let pair:Pair|null=null;
  const isPublic=['auth/login','auth/register','auth/otp/request','auth/otp/verify','auth/password/forgot','auth/password/verify','auth/password/reset'].includes(route)||route.startsWith('directory/')||route.startsWith('taxonomy/')&&request.method==='GET'||route==='memberships/plans';
  if(!access && refresh && !isPublic){pair=await rotate(refresh);access=pair?.access_token;}
  const send=async()=>{
   const headers=new Headers();
   const contentType=request.headers.get('content-type');if(contentType)headers.set('Content-Type',contentType);
   if(access)headers.set('Authorization',`Bearer ${access}`);
   let payload:BodyInit|undefined=body;
   if(route==='auth/logout') {headers.set('Content-Type','application/json');payload=JSON.stringify({refresh_token:pair?.refresh_token||refresh||''});}
   // Material upload, preview, confirmation and progress calls only stage/read a
   // durable job now. Workbook parsing and application run in the worker.
   const longImport=route.startsWith('taxonomy/categories/import/')||route.startsWith('imports/suppliers')||route.startsWith('pricebooks/')&&route.endsWith('/bulk-import');
   const timeout=longImport?120_000:20_000;
   return fetch(`${upstream()}/api/v1/${route}${request.nextUrl.search}`,{method:request.method,headers,body:payload,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(timeout)});
  };
  let result=await send();
  if(result.status===401 && refresh && !isPublic && !pair){pair=await rotate(refresh);if(pair){access=pair.access_token;result=await send();}}
  let data=await result.json().catch(()=>({detail:'Invalid API response'}));
  if(result.ok && route==='auth/login') {pair=data as Pair;data={authenticated:true};}
  if(data && typeof data==='object') {delete data.development_otp;delete data.development_code;}
  const response=NextResponse.json(data,{status:result.status,headers:{'Cache-Control':'no-store'}});
  const retry=result.headers.get('Retry-After');if(retry)response.headers.set('Retry-After',retry);
  if(pair)cookies(response,pair);
  if(route==='auth/logout'||result.status===401&&!isPublic)clear(response);
  return response;
 }catch(error){if(error instanceof PayloadTooLarge)return NextResponse.json({detail:'Upload too large'},{status:413});return NextResponse.json({detail:error instanceof Error&&error.name==='TimeoutError'?'API request timed out':'The backend is unavailable'},{status:502,headers:{'Cache-Control':'no-store'}});}
}
