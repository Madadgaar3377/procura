import { ApiError } from './errors';

export function apiUrl(path:string):string {
  const configured=process.env.NEXT_PUBLIC_API_URL;
  const fallback=typeof window==='undefined'?'http://127.0.0.1:8000/api/v1':`${window.location.protocol}//${window.location.hostname}:8000/api/v1`;
  const base=(configured||fallback).replace(/\/$/,'');
  if(!/^https?:\/\//.test(base))throw new Error('NEXT_PUBLIC_API_URL must be an absolute backend URL');
  return `${base}/${path.replace(/^\//,'')}`;
}

let refreshing:Promise<boolean>|null=null;
const reads=new Map<string,Promise<unknown>>();
function browserRoute(path:string):string {
  return path==='auth/login'?'auth/browser/login':path==='auth/logout'?'auth/browser/logout':path;
}
async function refreshBrowserSession():Promise<boolean> {
  if(!refreshing){
    refreshing=fetch(apiUrl('auth/browser/refresh'),{method:'POST',credentials:'include',cache:'no-store',signal:AbortSignal.timeout(20000)}).then(r=>r.ok).catch(()=>false).finally(()=>{refreshing=null;});
  }
  return refreshing;
}
async function perform<T>(path:string,init:RequestInit):Promise<T> {
  const headers=new Headers(init.headers);
  if(init.body&&!(init.body instanceof FormData))headers.set('Content-Type','application/json');
  const publicAuth=['auth/login','auth/register','auth/logout','auth/otp/request','auth/otp/verify','auth/password/forgot','auth/password/verify','auth/password/reset'].includes(path.split('?')[0]);
  const send=()=>fetch(apiUrl(browserRoute(path)),{...init,headers,credentials:'include',cache:'no-store',signal:init.signal?AbortSignal.any([init.signal,AbortSignal.timeout(20000)]):AbortSignal.timeout(20000)});
  async function request(){try{return await send();}catch(error){
    if(init.signal?.aborted)throw error;
    if(error instanceof Error&&error.name==='TimeoutError')throw new Error('The API request timed out. Please try again.');
    throw new Error('The API could not be reached. Check your connection and try again.');
  }}
  let response=await request();
  // Retry only safe reads, once, with bounded backoff.
  if((init.method||'GET').toUpperCase()==='GET'&&[502,503,504].includes(response.status)){
    const wait=Math.min(2000,Math.max(500,Number(response.headers.get('Retry-After')||0)*1000));
    await new Promise<void>((resolve,reject)=>{
      const abort=()=>{clearTimeout(timer);reject(init.signal?.reason);};
      const timer=setTimeout(()=>{init.signal?.removeEventListener('abort',abort);resolve();},wait);
      if(init.signal?.aborted)abort();else init.signal?.addEventListener('abort',abort,{once:true});
    });
    response=await request();
  }
  if(response.status===401&&!publicAuth&&await refreshBrowserSession())response=await request();
  const data:unknown=await response.json().catch(()=>null);
  if(!response.ok){
    if(response.status===401&&!publicAuth&&typeof window!=='undefined')window.dispatchEvent(new Event('procura:session-expired'));
    throw new ApiError(response.status,data&&typeof data==='object'&&'detail' in data?data.detail:null,response.headers.get('Retry-After'));
  }
  if(data&&typeof data==='object'){delete (data as Record<string,unknown>).development_otp;delete (data as Record<string,unknown>).development_code;}
  if((init.method||'GET').toUpperCase()!=='GET'&&typeof window!=='undefined'){window.dispatchEvent(new Event('procura:data-changed'));try{localStorage.setItem('procura:data-changed',`${Date.now()}:${Math.random()}`);}catch{/* Storage may be disabled. */}}
  return data as T;
}
export function api<T>(path:string,init:RequestInit={}):Promise<T> {
  if((init.method||'GET').toUpperCase()==='GET'&&!init.signal){
    let task=reads.get(path);
    if(!task){task=perform(path,init).finally(()=>reads.delete(path));reads.set(path,task);}
    return task as Promise<T>;
  }
  return perform<T>(path,init);
}

export function upload<T>(path: string, body: FormData, onProgress: (percent: number) => void, onUploaded?: () => void, attempt=0): Promise<T> {
  return new Promise((resolve,reject)=>{
    const request=new XMLHttpRequest();
    request.open('POST',apiUrl(path));
    request.withCredentials=true;
    request.timeout=125_000;
    request.setRequestHeader('Cache-Control','no-store');
    request.upload.addEventListener('progress',event=>{
      if(event.lengthComputable)onProgress(Math.min(100,Math.round(event.loaded/event.total*100)));
    });
    request.upload.addEventListener('load',()=>{onProgress(100);onUploaded?.();});
    request.addEventListener('load',async()=>{
      if(request.status===401&&attempt===0&&await refreshBrowserSession()){
        try{resolve(await upload<T>(path,body,onProgress,onUploaded,1));}catch(cause){reject(cause);}
        return;
      }
      let data:unknown=null;
      try{data=JSON.parse(request.responseText);}catch{}
      if(request.status<200||request.status>=300){
        if(request.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('procura:session-expired'));
        reject(new ApiError(request.status,data&&typeof data==='object'&&'detail'in data?data.detail:null,request.getResponseHeader('Retry-After')));
        return;
      }
      resolve(data as T);
    });
    request.addEventListener('error',()=>reject(new Error('The upload could not reach the API. Check your connection and try again.')));
    request.addEventListener('timeout',()=>reject(new Error('The workbook is taking longer than expected to validate. Try again, or use a smaller workbook.')));
    request.addEventListener('abort',()=>reject(new Error('The upload was interrupted. Please try again.')));
    request.send(body);
  });
}

export const json = (data: unknown) => JSON.stringify(data);
