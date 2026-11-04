"use client";
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {InternationalPhone} from '@/components/ui/international-phone';
import {Button} from '@/components/ui/button';
import {api,json} from '@/lib/api/client';
import {ApiError} from '@/lib/api/errors';
import {useLocale} from '@/lib/locale';

export function PhoneChange({onVerified}:{onVerified:()=>Promise<void>}) {
 const {arabic}=useLocale();const tr=(en:string,ar:string)=>arabic?ar:en;
 const [phone,setPhone]=useState(''),[password,setPassword]=useState(''),[challenge,setChallenge]=useState('');
 const [code,setCode]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [resendAt,setResendAt]=useState(0);const pending=useRef(false);
 useEffect(()=>{if(!challenge)return;let active=true;const poll=async()=>{try{const result=await api<{state:string;message:string}>(`auth/phone/change/${challenge}/status`);if(active){setNotice(result.message);if(result.state==='failed'||result.state==='expired')setError(result.message);}}catch{/* A delivery-status lookup never submits another message. */}};void poll();const timer=setInterval(()=>void poll(),5000);const stop=setTimeout(()=>clearInterval(timer),300000);return()=>{active=false;clearInterval(timer);clearTimeout(stop);};},[challenge]);
 async function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();if(pending.current)return;
  const form=new FormData(event.currentTarget);pending.current=true;setBusy(true);setError('');
  try{
   if(challenge){await api('auth/phone/change/confirm',{method:'POST',body:json({challenge_id:challenge,code})});await onVerified();setChallenge('');setPhone('');setCode('');setPassword('');setNotice(tr('Your new phone is verified and saved.','تم تأكيد رقم الهاتف الجديد وحفظه.'));}
   else{if(Date.now()<resendAt)throw new Error(tr('Please wait before requesting another code.','يرجى الانتظار قبل طلب رمز آخر.'));const response=await api<{challenge_id:string;message:string}>('auth/phone/change/request',{method:'POST',body:json({phone,phone_country:String(form.get('phone_country')||'')||null,current_password:password})});setChallenge(response.challenge_id);setPassword('');setCode('');setResendAt(Date.now()+60000);setNotice(tr('Code requested for your new phone. Your current phone remains unchanged.','تم طلب رمز لرقمك الجديد. لم يتم تغيير رقمك المسجل.'));}
  }catch(cause){setError(cause instanceof Error?cause.message:'Request failed');if(cause instanceof ApiError&&cause.status===429)setResendAt(Date.now()+Number(cause.retryAfter||60)*1000);}
  finally{pending.current=false;setBusy(false);}
 }
 return <section className="settings-phone-change"><h3>{tr('Change registered phone','تغيير رقم الهاتف المسجل')}</h3><p>{tr('Confirm your current password, then verify a code sent to the new number. International SMS delivery depends on carrier and device availability.','أكّد كلمة المرور الحالية ثم أدخل الرمز المرسل إلى رقمك الجديد. يعتمد وصول الرسائل الدولية على المشغّل وتوفّر الجهاز.')}</p><form onSubmit={submit}>
 {!challenge?<><InternationalPhone value={phone} onChange={value=>{setPhone(value);setNotice('');}} disabled={busy}/><label className="field">{tr('Current password','كلمة المرور الحالية')}<input type="password" required autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)} disabled={busy} maxLength={128}/></label></>:<><p><bdi>{phone}</bdi></p><label className="field">{tr('Verification code','رمز التحقق')}<input required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={event=>setCode(event.target.value)} disabled={busy}/></label><Button type="button" variant="outline" disabled={busy} onClick={()=>{setChallenge('');setCode('');setNotice(tr('Enter your current password to request a new code after the cooldown.','أدخل كلمة المرور الحالية لطلب رمز جديد بعد فترة الانتظار.'));}}>{tr('Change number or resend','تغيير الرقم أو إعادة الإرسال')}</Button></>}
 {error&&<p role="alert" className="settings-feedback error">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <Button type="submit" disabled={busy}>{busy?tr('Please wait…','يرجى الانتظار…'):challenge?tr('Verify and save new phone','تأكيد الرقم الجديد وحفظه'):tr('Send code to new phone','إرسال رمز إلى الرقم الجديد')}</Button>
 </form><p>{tr('If SMS does not arrive, keep your existing phone and contact support. Email verification does not replace phone ownership verification.','إذا لم تصلك الرسالة، احتفظ برقمك الحالي وتواصل مع الدعم. تأكيد البريد الإلكتروني لا يحل محل التحقق من ملكية الهاتف.')}</p></section>;
}
