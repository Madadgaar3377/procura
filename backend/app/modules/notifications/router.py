from app.search import ListSearch
from app.response_contracts import NotificationOut, OutboxOut
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel
from app.security import audit
from app.database import get_db
from app.models import Notification,Outbox,User
from app.security import current_user,admin_user
from app.serializers import record

router=APIRouter(prefix='/notifications',tags=['Notifications & Outbox'])

@router.get('',response_model=list[NotificationOut])
def mine(search:ListSearch=Depends(),user:User=Depends(current_user),db:Session=Depends(get_db)):
    return [record(n) for n in db.scalars(search.apply(select(Notification).where(Notification.recipient_user_id==user.id,Notification.channel=='in_app'),Notification.event,Notification.message,Notification.status,order=(Notification.created_at.desc(),Notification.id))).all()]

def display_delivery_status(event):
    provider=(event.delivery_status or '').lower()
    if event.status=='uncertain':return 'uncertain'
    if event.status=='failed':return 'failed'
    if event.status=='skipped':return 'skipped'
    if event.status=='retry':return 'retry_scheduled'
    if provider=='simulated':return 'simulated'
    if provider=='delivered':return 'delivered'
    if provider=='sent':return 'sent'
    if provider in {'accepted','pending','processed'} and event.status in {'awaiting_status','processed'}:return 'accepted'
    if event.status=='processing':return 'sending'
    return 'queued'


class DeliveryStatusOut(BaseModel):
    id:str
    outbox_id:str
    event:str
    channel:str
    status:str
    created_at:str
    provider_status:str|None
    provider_message_id:str|None
    provider_response_code:int|None
    last_error:str|None


@router.get('/deliveries',response_model=list[DeliveryStatusOut])
def delivery_statuses(user:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(Outbox).where(Outbox.event=='notification.delivery',Outbox.payload['user_id'].as_string()==user.id).order_by(Outbox.created_at.desc()).limit(100)).all()
    return [{'id':event.id,'outbox_id':event.id,'event':event.payload.get('template','notification.delivery'),
        'channel':event.payload.get('channel',''),'status':display_delivery_status(event),'created_at':event.created_at.isoformat(),
        'provider_status':event.delivery_status,'provider_message_id':event.provider_message_id,
        'provider_response_code':event.payload.get('provider_response_code'),'last_error':event.last_error} for event in rows]


class RetryDelivery(BaseModel):
    confirmed_not_accepted:bool=False


class DeliveryFeedback(BaseModel):
    outcome: str
    evidence: str


@router.post('/admin/outbox/{event_id}/feedback', response_model=OutboxOut)
def delivery_feedback(event_id: str, payload: DeliveryFeedback, admin: User=Depends(admin_user), db: Session=Depends(get_db)):
    """Record reviewed SMTP bounce/complaint or mailbox evidence, never inferred delivery."""
    from app.models import DeliveryRestriction
    event=db.scalar(select(Outbox).where(Outbox.id==event_id).with_for_update())
    if not event or not event.payload.get('procurement_recipient'):
        raise HTTPException(404,'Procurement delivery not found')
    if payload.outcome not in {'delivered','bounced','complaint','opted_out'} or not 5<=len(payload.evidence.strip())<=500:
        raise HTTPException(422,'Provide a supported outcome and 5-500 characters of provider/mailbox evidence')
    if not event.provider_message_id or event.delivery_status in {'simulated',None}:
        raise HTTPException(409,'No actual provider submission to reconcile')
    if payload.outcome=='delivered' and event.payload['channel']!='email':
        raise HTTPException(409,'SMS delivery is confirmed through provider status lookup')
    if payload.outcome in {'bounced','complaint','opted_out'}:
        channel=event.payload['channel'];destination=event.payload['destination']
        existing=db.scalar(select(DeliveryRestriction).where(DeliveryRestriction.channel==channel,
            DeliveryRestriction.destination==destination).with_for_update())
        reason='opted_out' if payload.outcome=='opted_out' else 'suppressed'
        if existing:existing.reason=reason;existing.detail=payload.evidence.strip()
        else:db.add(DeliveryRestriction(channel=channel,destination=destination,reason=reason,detail=payload.evidence.strip()))
    event.delivery_status=payload.outcome
    event.status='processed' if payload.outcome=='delivered' else 'failed' if payload.outcome=='bounced' else 'skipped'
    event.next_attempt_at=None
    event.payload={**event.payload,'feedback_evidence':payload.evidence.strip(),'feedback_at':datetime.now(timezone.utc).isoformat()}
    audit(db,admin,'notification.feedback_recorded',entity_id=event.id,outcome=payload.outcome)
    db.commit();result=record(event);result.pop('secret_ciphertext',None);return result


class DestinationRestriction(BaseModel):
    channel: str
    destination: str
    reason: str
    detail: str


@router.post('/admin/restrictions')
def restrict_destination(payload: DestinationRestriction, admin: User=Depends(admin_user), db: Session=Depends(get_db)):
    from app.models import DeliveryRestriction
    from app.modules.notifications.recipients import normalize_destination
    if payload.channel not in {'email','sms'} or payload.reason not in {'opted_out','suppressed'} or not 5<=len(payload.detail.strip())<=500:
        raise HTTPException(422,'Provide email/sms, opted_out/suppressed and a 5-500 character reason')
    try:destination=normalize_destination(payload.channel,payload.destination)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    row=db.scalar(select(DeliveryRestriction).where(DeliveryRestriction.channel==payload.channel,
        DeliveryRestriction.destination==destination).with_for_update())
    if row:row.reason=payload.reason;row.detail=payload.detail.strip()
    else:
        row=DeliveryRestriction(channel=payload.channel,destination=destination,reason=payload.reason,detail=payload.detail.strip());db.add(row)
    db.flush();audit(db,admin,'notification.destination_restricted',entity_id=row.id,reason=payload.reason)
    db.commit();return {'id':row.id,'reason':row.reason}


@router.post('/admin/outbox/{event_id}/retry',response_model=OutboxOut)
def retry_delivery(event_id:str,payload:RetryDelivery,admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    event=db.scalar(select(Outbox).where(Outbox.id==event_id).with_for_update())
    if not event or event.event!='notification.delivery':raise HTTPException(404,'Delivery job not found')
    if event.status not in {'failed','uncertain'}:raise HTTPException(409,'Only failed or uncertain jobs can be retried')
    if event.delivery_status in {'Failed','Cancelled'}:raise HTTPException(409,'Provider reported a terminal outcome. Request a new notification instead.')
    if event.status=='uncertain' and not payload.confirmed_not_accepted:
        raise HTTPException(409,'Confirm through provider or mailbox logs that the message was not accepted before retrying')
    if event.payload.get('channel')=='email':event.delivery_status=None
    if event.payload.get('procurement_recipient') and event.payload.get('channel')=='sms' and event.status=='uncertain' and payload.confirmed_not_accepted:
        event.delivery_status=None;event.provider_message_id=None
    event.status='retry';event.attempts=0;event.next_attempt_at=datetime.now(timezone.utc);event.lock_until=None;event.last_error=None
    audit(db,admin,'notification.retry_requested',entity_id=event.id,confirmed_not_accepted=payload.confirmed_not_accepted)
    db.commit();result=record(event);result.pop('secret_ciphertext',None);return result

@router.post('/{notification_id}/read',response_model=NotificationOut)
def mark_read(notification_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    item=db.get(Notification,notification_id)
    if not item or item.recipient_user_id!=user.id:raise HTTPException(404,'Notification not found')
    item.read_at=datetime.now(timezone.utc);db.commit();return record(item)

@router.get('/admin/outbox',response_model=list[OutboxOut])
def outbox(admin:User=Depends(admin_user),db:Session=Depends(get_db)):
    output=[]
    for item in db.scalars(select(Outbox).order_by(Outbox.created_at.desc()).limit(100)).all():
        data=record(item)
        data.pop('secret_ciphertext',None)
        output.append(data)
    return output
