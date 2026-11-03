"""Run alongside the existing outbox worker: python -m app.modules.pricebooks.worker."""
import argparse,time,logging
from types import SimpleNamespace
from sqlalchemy import select,cast,DateTime,func
from app.database import SessionLocal
from app.models import ImportBatch,Invitation,Quotation,Organization,RFQ
from datetime import datetime,timezone
from app.modules.pricebooks.imports import process_rate_import
from app.modules.quotations.automation import prepare_authorized_quote

def process_pricing_jobs(db,limit=10):
    processed=[]
    for _ in range(max(1,min(limit,100))):
        batch=db.scalar(select(ImportBatch).where(ImportBatch.kind=='supplier_rates',ImportBatch.status.in_({'uploaded','queued','applying'})).order_by(ImportBatch.created_at).limit(1).with_for_update(skip_locked=True))
        if not batch:break
        batch_id=batch.id
        try:
            event=SimpleNamespace(event='pricebook.import.parse' if batch.status=='uploaded' else 'pricebook.import.apply',payload={'batch_id':batch.id})
            process_rate_import(db,event);db.commit();processed.append(batch_id)
        except Exception:
            db.rollback();batch=db.get(ImportBatch,batch_id)
            if batch:batch.status='failed';batch.failure_reason='Pricing worker could not finish this batch. Previously saved rows are preserved.';db.commit()
            logging.exception('Private rate job failed batch=%s',batch_id)
    # Check bounded new invitations; skip locked rows allow multiple worker instances.
    policy=Organization.quote_automation
    threshold=cast(policy['authorized_at'].as_string(),DateTime(timezone=True)) if db.get_bind().dialect.name=='postgresql' else func.datetime(policy['authorized_at'].as_string())
    created=Invitation.created_at if db.get_bind().dialect.name=='postgresql' else func.datetime(Invitation.created_at)
    ids=db.scalars(select(Invitation.id).join(Organization,Organization.id==Invitation.supplier_org_id).join(RFQ,RFQ.id==Invitation.rfq_id).where(policy['enabled'].as_boolean().is_(True),created>=threshold,RFQ.status.in_({'published','collecting_quotes'}),RFQ.response_deadline>datetime.now(timezone.utc),Invitation.status.in_({'invited','accepted'}),~select(Quotation.id).where(Quotation.invitation_id==Invitation.id).exists()).order_by(Invitation.created_at).limit(100).with_for_update(skip_locked=True,of=Invitation)).all()
    for invitation_id in ids:
        try:
            quote=prepare_authorized_quote(db,invitation_id);db.commit()
            if quote:processed.append(quote.id)
        except Exception:db.rollback();logging.exception('Authorized quotation preparation failed invitation=%s',invitation_id)
    return processed

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');parser.add_argument('--interval',type=int,default=3);args=parser.parse_args()
    while True:
        with SessionLocal() as db:process_pricing_jobs(db)
        if args.once:break
        time.sleep(max(1,args.interval))
if __name__=='__main__':main()
