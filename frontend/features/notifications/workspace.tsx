"use client";
import {useLocale} from '@/lib/locale';
import { useEffect } from 'react';
import {useServerList} from '@/features/shared/server-list';
import { useApi } from '@/lib/api/use-api';
import type { Schemas } from '@/lib/api/generated/schema';
import { Action,DataTable,Heading,State } from '@/features/shared/ui';
function deliveryRow(row:Schemas['OutboxOut']){
 const payload=(row.payload||{}) as Record<string,unknown>;const context=(payload.context||{}) as Record<string,unknown>;
 const destination=String(payload.destination||'');const channel=String(payload.channel||'');
 const masked=destination.includes('@')?destination.slice(0,2)+'...@'+destination.split('@')[1]:destination?destination.slice(0,4)+'...'+destination.slice(-3):'--';
 const status=row.status==='skipped'?`Skipped: ${String(payload.reason_detail||row.delivery_status||'no contact').replaceAll('_',' ')}`:row.status==='uncertain'?'Outcome uncertain':row.status==='failed'?'Failed':row.status==='retry'?'Retry scheduled':row.delivery_status==='simulated'?'Simulated':row.delivery_status==='accepted'?'Provider accepted':row.status==='pending'?'Pending':row.status==='processing'?'Processing':row.delivery_status||row.status;
 return {...row,rfq:context.reference||payload.template||row.event,supplier:context.supplier_name||context.org_id||'--',contact:payload.contact_name||payload.contact_id||payload.recipient_source||'--',channel,destination:masked,channel_status:status,provider_response:payload.provider_status_response_code||payload.provider_response_code||(payload.smtp_accepted?'SMTP accepted':'--'),last_attempt:payload.last_attempt_at||'--',reason:payload.reason_detail||row.last_error||'--'};
}
export default function Notifications({admin=false}:{admin?:boolean}){
 const {t}=useLocale();
 const q=useServerList<Schemas['NotificationOut']>('notifications','Search notifications');const delivery=useApi<Record<string,unknown>[] >('notifications/deliveries');const outbox=useApi<Schemas['OutboxOut'][] >(admin?'notifications/admin/outbox':null);
 const labels:Record<string,string>={queued:'Pending',sending:'Processing',accepted:'Provider accepted',sent:'Sent (delivery unconfirmed)',delivered:'Delivered',failed:'Failed',uncertain:'Outcome uncertain',skipped:'Skipped',retry_scheduled:'Retry scheduled',simulated:'Simulated'};
 useEffect(()=>{const timer=window.setInterval(()=>{delivery.reload();if(admin)outbox.reload();},5000);return()=>window.clearInterval(timer);},[admin,delivery.reload,outbox.reload]);
 return <><Heading title={admin?t('Notification logs'):t('Notifications')} description="In-app events and backend delivery records. Provider acceptance and confirmed delivery are distinct states."/>{q.controls}<State {...q} retry={q.reload}/><DataTable paginate={false} rows={q.rows} columns={['event','message','status','created_at','read_at']} action={r=>!r.read_at&&<Action path={`notifications/${r.id}/read`} onDone={q.reload}>{t("Mark read")}</Action>}/><h2>{t("Email and SMS delivery")}</h2><State {...delivery}/><DataTable rows={(delivery.data||[]).map(row=>({...row,status:labels[String(row.status)]||row.status}))} columns={['event','channel','status','provider_status','created_at','last_error']}/>{admin&&<><h2>{t("Outbox processing")}</h2><State {...outbox}/><DataTable rows={(outbox.data||[]).map(deliveryRow)} columns={['rfq','supplier','contact','channel','destination','channel_status','provider_response','provider_message_id','last_attempt','reason']} action={row=>['failed','uncertain'].includes(String(row.status))&&row.event==='notification.delivery'?<Action path={`notifications/admin/outbox/${row.id}/retry`} body={{confirmed_not_accepted:row.status==='uncertain'}} confirm={row.status==='uncertain'?'Confirm you checked the provider/mailbox logs and this message was NOT accepted. Retrying an accepted message could send a duplicate.':'Retry this failed delivery after correcting its configuration? Expired OTPs will not be sent.'} onDone={outbox.reload}>{t("Retry delivery")}</Action>:null}/></>}</>;
}
