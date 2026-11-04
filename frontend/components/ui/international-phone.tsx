"use client";
import {useId} from 'react';
import flags from 'react-phone-number-input/flags';
import {useLocale} from '@/lib/locale';
import {saudiPhone} from '@/lib/validation';
import './international-phone.css';
export function InternationalPhone({name='phone',value,onChange,error,disabled=false,label,required=true}:{name?:string;value:string;onChange:(value:string)=>void;error?:string;disabled?:boolean;label?:string;required?:boolean}){
 const {arabic}=useLocale();const id=useId();const Flag=flags.SA;
 const canonical=saudiPhone(value);const issue=error||(value&&!canonical?(arabic?'أدخل رقم جوال سعودي صالح':'Enter a valid Saudi mobile number'):'');
 return <div className="global-phone" dir={arabic?'rtl':'ltr'}>
 <label htmlFor={id}>{label||(arabic?'رقم الجوال':'Mobile number')}</label>
 <div className={`global-phone-controls ${issue?'invalid':''}`}>
 <span className="global-phone-country">{Flag&&<Flag title="Saudi Arabia"/>}<span>{arabic?'السعودية':'Saudi Arabia'}</span></span>
 <span className="global-phone-prefix" dir="ltr">+966</span>
 <input id={id} type="tel" inputMode="tel" autoComplete="tel-national" dir="ltr" placeholder="501234567" value={value} onChange={e=>onChange(e.target.value)} required={required} disabled={disabled} aria-invalid={Boolean(issue)} aria-describedby={`${id}-help`}/>
 </div><input type="hidden" name={name} value={canonical||value}/><input type="hidden" name={`${name}_country`} value="SA"/>
 <small id={`${id}-help`}>{issue||<>{arabic?'الصيغة الدولية':'International preview'}: <bdi>{canonical||'+966 …'}</bdi></>}</small>
 </div>;
}
