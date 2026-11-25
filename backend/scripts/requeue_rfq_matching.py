"""Safely requeue one open RFQ. Never deletes matches or sends notifications."""
import argparse
from datetime import UTC, datetime
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Outbox, RFQ, Invitation, Quotation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('rfq_id')
    parser.add_argument('--prepare-existing-invitations',action='store_true',
        help='Replay invitation preparation for invitations without a quotation; existing delivery dedupe keys are retained')
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            rfq = db.scalar(select(RFQ).where(RFQ.id == args.rfq_id).with_for_update())
            if not rfq or rfq.status not in {'published', 'collecting_quotes'} or rfq.response_deadline.replace(tzinfo=rfq.response_deadline.tzinfo or UTC) <= datetime.now(UTC):
                raise SystemExit('RFQ is missing, closed, or expired; no job queued')
            jobs = db.scalars(select(Outbox).where(
                Outbox.event == 'rfq.published',
                Outbox.payload['rfq_id'].as_string() == args.rfq_id,
            ).order_by(Outbox.created_at.desc(), Outbox.id.desc())).all()
            active = next((job for job in jobs if job.status in {'pending', 'retry', 'processing'}), None)
            if active:
                print(f'Existing job {active.id} status={active.status}; no duplicate queued')
            else:
                recovery = next((job for job in jobs if job.dedupe_key == f'rfq-recovery:{args.rfq_id}'), None)
                if recovery:
                    recovery.status = 'retry'
                    recovery.attempts = 0
                    recovery.next_attempt_at = None
                    recovery.lock_until = None
                    recovery.last_error = None
                    print(f'Requeued recovery job {recovery.id}')
                else:
                    recovery = Outbox(event='rfq.published', payload={'rfq_id': args.rfq_id},
                        dedupe_key=f'rfq-recovery:{args.rfq_id}')
                    db.add(recovery)
                    db.flush()
                    print(f'Queued recovery job {recovery.id}')
            if args.prepare_existing_invitations:
                invitations=db.scalars(select(Invitation).outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(
                    Invitation.rfq_id==rfq.id,Invitation.status.in_({'invited','accepted'}),Quotation.id.is_(None)))
                queued=0
                for invitation in invitations:
                    key=f'rfq-invitation:{invitation.id}'
                    event=db.scalar(select(Outbox).where(Outbox.dedupe_key==key).with_for_update(skip_locked=True))
                    if event and event.status in {'pending','retry','processing'}:continue
                    if event:
                        event.status='pending';event.attempts=0;event.lock_until=None
                        event.next_attempt_at=None;event.last_error=None
                    else:
                        db.add(Outbox(event='rfq.invitation',dedupe_key=key,payload={
                            'rfq_id':rfq.id,'supplier_org_id':invitation.supplier_org_id,'invitation_id':invitation.id}))
                    queued+=1
                print(f'Queued preparation for {queued} existing invitation(s); prior delivery jobs retained')
            db.commit()
        except Exception:
            db.rollback()
            raise


if __name__ == '__main__':
    main()
