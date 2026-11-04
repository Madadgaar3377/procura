"use client";
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './client';
export const ApiScope=createContext('public');
type Entry={data?:unknown;updated:number;task?:Promise<unknown>;controller?:AbortController;users:number};
const cache=new Map<string,Entry>();
let generation=0;
export function clearApiCache(){generation++;for(const entry of cache.values())entry.controller?.abort();cache.clear();}
if(typeof window!=='undefined'){
 window.addEventListener('procura:data-changed',()=>{generation++;for(const entry of cache.values()){entry.updated=0;entry.controller?.abort();entry.task=undefined;}});
 window.addEventListener('storage',event=>{if(event.key==='procura:data-changed')window.dispatchEvent(new Event('procura:data-changed'));});
 // Refresh when returning from an admin tab or another administrator's changes.
 window.addEventListener('focus',()=>window.dispatchEvent(new Event('procura:data-changed')));
}
export function useApi<T>(path:string|null,options:{keepPreviousData?:boolean}={}){
 const scope=useContext(ApiScope);const key=scope+'|'+(path||'');
 const [revision,setRevision]=useState(0);
 const [state,setState]=useState<{key:string;data?:T;error?:Error;fetching:boolean}>({key,fetching:!!path});
 const reload=useCallback(()=>{const entry=cache.get(key);if(entry)entry.updated=0;setRevision(v=>v+1);},[key]);
 useEffect(()=>{const invalidate=()=>{setRevision(v=>v+1);};window.addEventListener('procura:data-changed',invalidate);return()=>window.removeEventListener('procura:data-changed',invalidate);},[]);
 useEffect(()=>{
  if(!path){setState({key,fetching:false});return;}
  let active=true;const effectGeneration=generation;let entry=cache.get(key);
  if(!entry){entry={updated:0,users:0};cache.set(key,entry);}
  const current=entry;current.users++;
  if(cache.size>150){for(const [oldKey,old] of cache){if(cache.size<=150)break;if(!old.users&&!old.task)cache.delete(oldKey);}}
  const staleTime=path.startsWith('memberships/')?0:path.startsWith('directory/')?0:path.startsWith('taxonomy/categories')?15000:path.startsWith('taxonomy/')?300000:30000;
  if(current.updated&&Date.now()-current.updated<staleTime){setState({key,data:current.data as T,fetching:false});}
  else{
   setState(previous=>({key,data:(current.data??(options.keepPreviousData&&previous.key.startsWith(scope+'|')?previous.data:undefined)) as T,fetching:true}));
   if(!current.task){
    const controller=new AbortController();current.controller=controller;const epoch=generation;
    const task=api<T>(path,{signal:controller.signal}).then(data=>{if(epoch===generation&&!controller.signal.aborted){current.data=data;current.updated=Date.now();}return data;}).finally(()=>{if(current.task===task)current.task=undefined;});current.task=task;
   }
   current.task.then(data=>{if(active&&effectGeneration===generation)setState({key,data:data as T,fetching:false});}).catch(error=>{if(active&&effectGeneration===generation)setState({key,data:current.data as T,error:error instanceof Error?error:new Error('Request failed'),fetching:false});});
  }
  return()=>{active=false;current.users--;queueMicrotask(()=>{if(current.users===0&&current.task){current.controller?.abort();if(cache.get(key)===current)cache.delete(key);}});};
 },[key,path,revision,scope,options.keepPreviousData]);
 const visible=state.key===key?state:{key,data:options.keepPreviousData&&state.key.startsWith(scope+'|')?state.data:undefined,error:undefined,fetching:!!path};
 return {data:visible.data,error:visible.error,loading:visible.fetching&&visible.data===undefined,fetching:visible.fetching,reload};
}
