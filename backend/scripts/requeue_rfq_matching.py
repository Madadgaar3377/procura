"""Safely requeue one open RFQ or all stuck open RFQs. Never deletes matches or sends duplicate notifications."""
import argparse
from datetime import UTC, datetime
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Outbox, RFQ, Invitation, Quotation


def recover_rfq(db, rfq_id: str, *, prepare_existing_invitations: bool = False) -> bool:
    rfq = db.scalar(select(RFQ).where(RFQ.id == rfq_id).with_for_update(skip_locked=True))
    if not rfq or rfq.status not in {'published', 'collecting_quotes'} or rfq.response_deadline.replace(tzinfo=rfq.response_deadline.tzinfo or UTC) <= datetime.now(UTC):
        print(f'Skipping {rfq_id}: missing, locked, closed, or expired')
        return False
    jobs = db.scalars(select(Outbox).where(
        Outbox.event == 'rfq.published',
        Outbox.payload['rfq_id'].as_string() == rfq_id,
    ).order_by(Outbox.created_at.desc(), Outbox.id.desc())).all()
    now = datetime.now(UTC)
    active = next((job for job in jobs if job.status in {'pending', 'retry'} or (job.status == 'processing' and job.lock_until and job.lock_until.replace(tzinfo=job.lock_until.tzinfo or UTC) > now)), None)
    if active:
        print(f'RFQ {rfq_id}: existing active job {active.id} status={active.status}; no duplicate queued')
    else:
        stalled = next((job for job in jobs if job.status == 'processing'), None)
        recovery = stalled or next((job for job in jobs if job.dedupe_key == f'rfq-recovery:{rfq_id}'), None)
        if recovery:
            recovery.status = 'pending'
            recovery.attempts = 0
            recovery.next_attempt_at = None
            recovery.lock_until = None
            recovery.last_error = None
            print(f'RFQ {rfq_id}: requeued job {recovery.id}')
        else:
            recovery = Outbox(event='rfq.published', payload={'rfq_id': rfq_id},
                dedupe_key=f'rfq-recovery:{rfq_id}')
            db.add(recovery)
            db.flush()
            print(f'RFQ {rfq_id}: queued recovery job {recovery.id}')
    if prepare_existing_invitations:
        invitations = db.scalars(select(Invitation).outerjoin(Quotation, Quotation.invitation_id == Invitation.id).where(
            Invitation.rfq_id == rfq.id, Invitation.status.in_({'invited', 'accepted'}), Quotation.id.is_(None))).all()
        queued = 0
        for invitation in invitations:
            key = f'rfq-invitation:{invitation.id}'
            event = db.scalar(select(Outbox).where(Outbox.dedupe_key == key).with_for_update(skip_locked=True))
            if event and event.status in {'pending', 'retry', 'processing'}:
                continue
            if event:
                event.status = 'pending'; event.attempts = 0; event.lock_until = None
                event.next_attempt_at = None; event.last_error = None
            else:
                db.add(Outbox(event='rfq.invitation', dedupe_key=key, payload={
                    'rfq_id': rfq.id, 'supplier_org_id': invitation.supplier_org_id, 'invitation_id': invitation.id}))
            queued += 1
        print(f'RFQ {rfq_id}: queued preparation for {queued} existing invitation(s); prior delivery jobs retained')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('rfq_id', nargs='?', help='Specific RFQ ID to requeue')
    parser.add_argument('--all-stuck', action='store_true',
        help='Safely recover all open published RFQs with failed, stalled, or missing matching jobs')
    parser.add_argument('--prepare-existing-invitations', action='store_true',
        help='Replay invitation preparation for invitations without a quotation; existing delivery dedupe keys are retained')
    args = parser.parse_args()
    if not args.rfq_id and not args.all_stuck:
        parser.error('Provide an rfq_id or --all-stuck')
    with SessionLocal() as db:
        try:
            if args.all_stuck:
                now = datetime.now(UTC)
                open_rfqs = db.scalars(select(RFQ.id).where(
                    RFQ.status.in_({'published', 'collecting_quotes'}),
                    RFQ.response_deadline > now,
                )).all()
                recovered = 0
                for rid in open_rfqs:
                    latest = db.scalar(select(Outbox).where(
                        Outbox.event == 'rfq.published',
                        Outbox.payload['rfq_id'].as_string() == rid,
                    ).order_by(Outbox.created_at.desc(), Outbox.id.desc()).limit(1))
                    is_stuck = (
                        latest is None
                        or latest.status in {'failed', 'uncertain'}
                        or (latest.status == 'processing' and (not latest.lock_until or latest.lock_until.replace(tzinfo=latest.lock_until.tzinfo or UTC) <= now))
                    )
                    if is_stuck and recover_rfq(db, rid, prepare_existing_invitations=args.prepare_existing_invitations):
                        recovered += 1
                print(f'Recovered {recovered} stuck open RFQ(s)')
            else:
                if not recover_rfq(db, args.rfq_id, prepare_existing_invitations=args.prepare_existing_invitations):
                    raise SystemExit('RFQ is missing, closed, or expired; no job queued')
            db.commit()
        except Exception:
            db.rollback()
            raise


if __name__ == '__main__':
    main()
