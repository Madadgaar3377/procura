"use client";
import {useEffect,useState,useSyncExternalStore,type ReactNode} from 'react';
import {IconAlertTriangle} from '@tabler/icons-react';
import {Button} from '@/components/ui/button';
import {useLocale} from '@/lib/locale';
import {AdminModal,ModalHeader,ModalBody,ModalFooter,FormField} from './admin-modal';

type Request={id:number;message:string;kind:'confirm'|'prompt'|'alert';initial:string;resolve:(value:string|null)=>void};
const listeners=new Set<()=>void>();let active:Request|null=null,sequence=0;
function request(kind:Request['kind'],message:string,initial=''):Promise<string|null>{
 // A second click must not create a second accepted action.
 if(active)return Promise.resolve(null);
 return new Promise(resolve=>{active={id:++sequence,kind,message,initial,resolve};listeners.forEach(notify=>notify());});
}
export async function adminConfirm(message:string){return await request('confirm',message)==='confirmed';}
export function adminPrompt(message:string,initial=''){return request('prompt',message,initial);}
export async function adminAlert(message:string){await request('alert',message);}
function settle(value:string|null){const current=active;active=null;current?.resolve(value);listeners.forEach(notify=>notify());}

export function AdminDialogProvider({children}:{children:ReactNode}){
 const current=useSyncExternalStore(callback=>{listeners.add(callback);return()=>{listeners.delete(callback);};},()=>active,()=>null);
 useEffect(()=>()=>{if(active)settle(null);},[]);
 return <>{children}{current&&<ConfirmationDialog key={current.id} request={current}/>}</>;
}
export function ConfirmationDialog({request:current}:{request:Request}){
 const {arabic}=useLocale(),[value,setValue]=useState(current.initial);
 const prompt=current.kind==='prompt',alert=current.kind==='alert',danger=/delete|remove|reject|archive|حذف|رفض/i.test(current.message);
 const cancel=()=>settle(null);
 return <AdminModal open normalize={false} size="sm" onRequestClose={cancel} onClose={cancel} aria-labelledby="admin-confirm-title">
  <ModalHeader onClose={cancel}><h2 id="admin-confirm-title">{arabic?(prompt?'إدخال التفاصيل':alert?'تنبيه':'تأكيد الإجراء'):(prompt?'Enter details':alert?'Notice':'Confirm action')}</h2></ModalHeader>
  <ModalBody><form id="admin-confirm-form" onSubmit={event=>{event.preventDefault();settle(prompt?value:'confirmed');}}>
   <div className={`admin-confirm-message${danger?' is-danger':''}`}><IconAlertTriangle size={22}/><p>{current.message}</p></div>
   {prompt&&<FormField label={arabic?'التفاصيل':'Details'}><input autoFocus value={value} onChange={event=>setValue(event.target.value)}/></FormField>}
  </form></ModalBody>
  <ModalFooter>{!alert&&<Button type="button" variant="outline" onClick={cancel}>{arabic?'إلغاء':'Cancel'}</Button>}<Button form="admin-confirm-form" type="submit" variant={danger?'destructive':'default'}>{arabic?(alert?'حسناً':prompt?'حفظ':'تأكيد'):(alert?'OK':prompt?'Save':'Confirm')}</Button></ModalFooter>
 </AdminModal>;
}
