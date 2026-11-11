"use client";
import {useEffect,useState} from 'react';
import {IconSearch,IconX} from '@tabler/icons-react';
import {Button} from '@/components/ui/button';
import {useApi} from '@/lib/api/use-api';
import {useLocale} from '@/lib/locale';

/** Keep bounded business-data filtering on the server, including organization scope. */
export function useServerList<T>(base:string|null,placeholder:string){
 const {t}=useLocale();const [term,setTerm]=useState(''),[query,setQuery]=useState(''),[page,setPage]=useState(0);
 useEffect(()=>{const timer=setTimeout(()=>{setQuery(term.trim());setPage(0);},300);return()=>clearTimeout(timer);},[term]);
 const path=base?`${base}${base.includes('?')?'&':'?'}q=${encodeURIComponent(query)}&limit=26&offset=${page*25}`:null;
 const result=useApi<T[]>(path,{keepPreviousData:true});
 useEffect(()=>{if(!result.fetching&&result.data?.length===0&&page>0)setPage(previous=>previous-1);},[result.fetching,result.data,page]);
 const rows=result.data?.slice(0,25)||[];
 const controls=<div className="server-list-controls"><label className="server-search"><IconSearch size={19} aria-hidden="true"/><span className="sr-only">{t(placeholder)}</span><input type="search" value={term} onChange={event=>setTerm(event.target.value)} placeholder={t(placeholder)}/>{term&&<button type="button" aria-label={t('Clear search')} onClick={()=>setTerm('')}><IconX size={18}/></button>}</label><div className="server-list-pagination"><Button type="button" variant="outline" disabled={!page||result.fetching} onClick={()=>setPage(p=>p-1)}>{t('Previous')}</Button><span>{t('page')} {page+1}</span><Button type="button" variant="outline" disabled={!result.data||result.data.length<=25||result.fetching} onClick={()=>setPage(p=>p+1)}>{t('Next')}</Button></div>{result.fetching&&<span role="status">{t('Loading records...')}</span>}</div>;
 return {...result,rows,controls,reset:()=>{setTerm('');setQuery('');setPage(0);result.reload();}};
}
