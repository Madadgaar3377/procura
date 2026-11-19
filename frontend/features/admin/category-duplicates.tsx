"use client";
import {useState} from 'react';
import {useApi} from '@/lib/api/use-api';
import {api,json} from '@/lib/api/client';
import {useLocale} from '@/lib/locale';
import {Button} from '@/components/ui/button';
import {adminConfirm} from '@/features/shared/admin-confirm';
import type {Schemas} from '@/lib/api/generated/schema';

export function CategoryDuplicates({onDone}:{onDone:()=>void}){
 const {t}=useLocale(),[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const review=useApi<Schemas['DuplicateGroup'][]>(open?'taxonomy/categories/duplicates':null);
 async function merge(group:Schemas['DuplicateGroup']){
  if(busy||!await adminConfirm(t('Move linked references to the canonical category and archive its duplicate aliases? No materials or categories will be deleted.')))return;
  setBusy(true);setError('');
  try{await api('taxonomy/categories/duplicates/merge',{method:'POST',body:json({category_ids:group.categories.map(c=>c.id),preview_token:group.preview_token})});review.reload();onDone();}
  catch(cause){setError(cause instanceof Error?cause.message:t('Could not merge categories'));}
  finally{setBusy(false);}
 }
 return <details className="panel category-duplicates" onToggle={e=>setOpen(e.currentTarget.open)}><summary>{t('Review duplicate categories')}</summary>
  <p>{t('This preview groups actual active category IDs by normalized name and parent. Ambiguous groups remain unchanged.')}</p>
  {(error||review.error)&&<p role="alert">{error||review.error?.message}</p>}
  {review.loading&&<p role="status">{t('Loading...')}</p>}
  {review.data?.length===0&&<p>{t('No duplicate category records found.')}</p>}
  {review.data?.map(group=><section key={group.canonical_id} className="panel"><h3>{group.parent_name?`${group.parent_name} / `:''}{group.normalized_name}</h3>
   <ul>{group.categories.map(category=><li key={category.id}><bdi>{category.name_en} · {category.code}</bdi><br/><small><bdi>{category.id}</bdi> · {t('Linked references')}: {Object.values(category.references).reduce((sum,n)=>sum+n,0)}</small></li>)}</ul>
   {group.reasons.map(reason=><p key={reason}>{t(reason)}</p>)}
   <Button variant="outline" disabled={busy||!group.safe_to_merge} onClick={()=>void merge(group)}>{t('Merge reviewed duplicates')}</Button>
  </section>)}
 </details>;
}
