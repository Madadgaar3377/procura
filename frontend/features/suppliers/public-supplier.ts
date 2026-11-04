import 'server-only';
import {cache} from 'react';
import type {Schemas} from '@/lib/api/generated/schema';

export const readPublicSupplier=cache(async(id:string):Promise<Schemas['PublicSupplier']|null>=>{
 try{
  const configured=process.env.FASTAPI_INTERNAL_URL||process.env.BACKEND_URL||process.env.NEXT_PUBLIC_API_URL||'http://127.0.0.1:8000';
  const base=new URL(configured);
  if(!['http:','https:'].includes(base.protocol)||base.username||base.password)return null;
  const response=await fetch(`${base.origin}/api/v1/directory/suppliers/${encodeURIComponent(id)}?include_profile=true&include_categories=true`,{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(2200)});
  if(!response.ok)return null;
  const data=await response.json() as Schemas['PublicSupplier'];
  return typeof data.name==='string'&&data.verified?data:null;
 }catch{return null;}
});
