"use client";

import {useLocale} from '@/lib/locale';

export function FileUploadStatus({title,detail,percent}:{title:string;detail?:string;percent:number|null}){
 const {t}=useLocale();
 const value=percent===null?undefined:Math.max(0,Math.min(100,percent));
 return <div className={`category-upload-status${value===undefined?' is-validating':''}`} role="status" aria-live="polite">
  <div className="category-upload-copy"><span className="category-upload-orb" aria-hidden="true"><i/></span>
   <div><strong>{t(title)}</strong>{detail&&<small>{detail}</small>}</div>
   <b>{value===undefined?t('Please wait...'):`${value}%`}</b>
  </div>
  <div className={`category-upload-track${value===undefined?' is-indeterminate':''}`} role="progressbar"
   aria-label={t(title)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}>
   <i style={value===undefined?undefined:{width:`${value}%`}}/>
  </div>
 </div>;
}
