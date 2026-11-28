"""Staged, optimistic RFQ revisions. The live RFQ changes only at publication."""
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Outbox, Quotation, QuoteLine, RFQ, RFQItem, RFQRevision, User
from app.response_contracts import RFQDetail
from app.schemas import RFQIn
from app.security import audit, current_user, org_member
from app.serializers import record

router = APIRouter()
RFQ_FIELDS = ('title', 'project_name', 'delivery_country', 'city', 'delivery_address',
              'required_delivery_date', 'response_deadline', 'additional_instructions',
              'required_qualifications')
MATCH_FIELDS = {'city', 'required_qualifications'}
ITEM_MATCH_FIELDS = {'material_id', 'main_category_id', 'subcategory_id', 'description',
                     'specifications', 'discipline', 'required_brand'}
ITEM_QUOTE_FIELDS = ITEM_MATCH_FIELDS | {'unit', 'quantity'}


class StageRevisionIn(BaseModel):
    expected_revision: int = Field(ge=0)
    rfq: RFQIn
    attachment_changes: bool = False


class RevisionOut(BaseModel):
    id: str
    rfq_id: str
    number: int
    status: str
    created_by_user_id: str
    updated_by: str | None = None
    created_at: datetime
    published_at: datetime | None
    before_snapshot: dict[str, Any]
    after_snapshot: dict[str, Any]
    changes: dict[str, Any]
    matching_summary: dict[str, Any]
    notification_status: dict[str, int] = Field(default_factory=dict)


class PublishedRevisionOut(BaseModel):
    revision: RevisionOut
    detail: RFQDetail


class DiscardedRevisionOut(BaseModel):
    discarded: bool


def snapshot(db: Session, rfq: RFQ) -> dict:
    items = db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq.id, RFQItem.is_active.is_(True))
                       .order_by(RFQItem.created_at, RFQItem.id)).all()
    return {'rfq': {field: record(rfq).get(field) for field in RFQ_FIELDS},
            'items': [record(item) for item in items]}


def difference(before: dict, after: dict) -> dict:
    changed = {field: {'previous': before['rfq'].get(field), 'new': after['rfq'].get(field)}
               for field in RFQ_FIELDS if before['rfq'].get(field) != after['rfq'].get(field)}
    old = {item['id']: item for item in before['items']}
    new = {item['id']: item for item in after['items']}
    modified = {item_id: {field: {'previous': old[item_id].get(field), 'new': new[item_id].get(field)}
                          for field in ITEM_QUOTE_FIELDS if old[item_id].get(field) != new[item_id].get(field)}
                for item_id in old.keys() & new.keys()}
    modified = {item_id: fields for item_id, fields in modified.items() if fields}
    matching_items = list((old.keys() ^ new.keys()) |
                          {item_id for item_id, fields in modified.items() if ITEM_MATCH_FIELDS & fields.keys()})
    quote_items = list((old.keys() ^ new.keys()) | set(modified))
    return {'fields': changed, 'added_materials': [new[item_id] for item_id in new.keys() - old.keys()],
            'removed_materials': [old[item_id] for item_id in old.keys() - new.keys()],
            'modified_materials': modified, 'matching_changed': bool(MATCH_FIELDS & changed.keys() or matching_items),
            'matching_item_ids': sorted(matching_items), 'quotation_item_ids': sorted(quote_items)}


def serialized(revision: RFQRevision) -> dict:
    return {'id': revision.id, 'rfq_id': revision.rfq_id, 'number': revision.number,
            'status': revision.status, 'created_by_user_id': revision.created_by_user_id,
            'created_at': revision.created_at, 'published_at': revision.published_at,
            'before_snapshot': revision.before_snapshot, 'after_snapshot': revision.after_snapshot,
            'changes': revision.changes, 'matching_summary': revision.matching_summary}


@router.get('/{rfq_id}/revisions', response_model=list[RevisionOut])
def history(rfq_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rfq = db.get(RFQ, rfq_id)
    if not rfq: raise HTTPException(404, 'RFQ not found')
    org_member(db, user, rfq.buyer_org_id, {'owner', 'manager', 'buyer'})
    rows = db.scalars(select(RFQRevision).where(RFQRevision.rfq_id == rfq_id)
                      .order_by(RFQRevision.number.desc()).limit(100)).all()
    authors = dict(db.execute(select(User.id, User.username).where(
        User.id.in_({row.created_by_user_id for row in rows}))).all()) if rows else {}
    statuses = {}
    for number, status, count in db.execute(select(
        Outbox.payload['context']['revision'].as_integer(), Outbox.status, func.count())
        .where(Outbox.event == 'notification.delivery',
               Outbox.payload['context']['rfq_id'].as_string() == rfq_id,
               Outbox.payload['template'].as_string() == 'rfq_revision')
        .group_by(Outbox.payload['context']['revision'].as_integer(), Outbox.status)):
        statuses.setdefault(number, {})[status] = count
    for number, status, count in db.execute(select(
        Outbox.payload['revision'].as_integer(), Outbox.status, func.count())
        .where(Outbox.event == 'rfq.revised', Outbox.payload['rfq_id'].as_string() == rfq_id)
        .group_by(Outbox.payload['revision'].as_integer(), Outbox.status)):
        statuses.setdefault(number, {})[f'queue_{status}'] = count
    return [{**serialized(row), 'updated_by': authors.get(row.created_by_user_id),
             'notification_status': statuses.get(row.number, {})} for row in rows]


@router.post('/{rfq_id}/revisions', status_code=201, response_model=RevisionOut)
def stage(rfq_id: str, payload: StageRevisionIn, user: User = Depends(current_user),
          db: Session = Depends(get_db)):
    from .router import prepare_lines
    rfq = db.scalar(select(RFQ).where(RFQ.id == rfq_id).with_for_update())
    if not rfq: raise HTTPException(404, 'RFQ not found')
    org_member(db, user, rfq.buyer_org_id, {'owner', 'manager', 'buyer'})
    if rfq.status not in {'published', 'collecting_quotes'}:
        raise HTTPException(409, {'code': 'rfq_not_revisable', 'message': 'Only published RFQs can be revised.'})
    if db.scalar(select(Quotation.id).where(Quotation.rfq_id == rfq.id, Quotation.status == 'awarded').limit(1)):
        raise HTTPException(409, {'code': 'rfq_awarded', 'message': 'An awarded RFQ cannot be revised.'})
    if payload.expected_revision != rfq.revision:
        raise HTTPException(409, {'code': 'stale_revision', 'message': 'This RFQ changed. Reload before revising.',
                                  'current_revision': rfq.revision})
    if payload.rfq.buyer_org_id != rfq.buyer_org_id:
        raise HTTPException(422, 'Cannot change RFQ organization')
    if payload.rfq.response_deadline.astimezone(UTC) <= datetime.now(UTC):
        raise HTTPException(422, [{'loc': ['body', 'rfq', 'response_deadline'],
                                   'msg': 'Deadline must be in the future', 'type': 'value_error'}])
    before = snapshot(db, rfq)
    old_ids = {item['id'] for item in before['items']}
    lines = prepare_lines(db, payload.rfq.items)
    submitted = [line['id'] for line in lines if line.get('id')]
    if len(submitted) != len(set(submitted)) or any(item_id not in old_ids for item_id in submitted):
        raise HTTPException(422, 'An RFQ material ID does not belong to this RFQ')
    for line in lines:
        if not line.get('id'): line['id'] = str(uuid4())
    after = {'rfq': {field: payload.rfq.model_dump(mode='json').get(field) for field in RFQ_FIELDS},
             'items': [{**line, 'quantity': str(line['quantity'])} for line in lines]}
    changes = difference(before, after)
    if not payload.attachment_changes and not changes['fields'] and not changes['added_materials'] and not changes['removed_materials'] and not changes['modified_materials']:
        raise HTTPException(422, 'No RFQ changes to publish')
    pending = db.scalar(select(RFQRevision).where(RFQRevision.rfq_id == rfq.id,
        RFQRevision.number == rfq.revision + 1, RFQRevision.status == 'pending').with_for_update())
    if pending and pending.created_by_user_id != user.id:
        raise HTTPException(409, {'code': 'revision_in_progress', 'message': 'Another buyer is reviewing a revision.'})
    revision = pending or RFQRevision(rfq_id=rfq.id, number=rfq.revision + 1, status='pending',
                                      created_by_user_id=user.id)
    if pending:
        for key in ('attachment_additions', 'attachment_removals', 'local_attachment_additions'):
            if key in pending.after_snapshot: after[key] = pending.after_snapshot[key]
        for key in ('added_attachments', 'removed_attachments'):
            if key in pending.changes: changes[key] = pending.changes[key]
    revision.before_snapshot, revision.after_snapshot, revision.changes = before, after, changes
    db.add(revision)
    audit(db, user, 'rfq.revision_staged', rfq.buyer_org_id, rfq.id, revision=revision.number)
    db.commit()
    return serialized(revision)


@router.post('/{rfq_id}/revisions/{revision_id}/publish', response_model=PublishedRevisionOut)
def publish_revision(rfq_id: str, revision_id: str, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    from .router import contents, prepare_lines
    rfq = db.scalar(select(RFQ).where(RFQ.id == rfq_id).with_for_update())
    if not rfq: raise HTTPException(404, 'RFQ not found')
    org_member(db, user, rfq.buyer_org_id, {'owner', 'manager', 'buyer'})
    revision = db.scalar(select(RFQRevision).where(RFQRevision.id == revision_id,
        RFQRevision.rfq_id == rfq.id).with_for_update())
    if not revision: raise HTTPException(404, 'Revision not found')
    if revision.status == 'published': return {'revision': serialized(revision), 'detail': contents(db, rfq)}
    if rfq.status not in {'published', 'collecting_quotes'}:
        raise HTTPException(409, {'code': 'rfq_not_revisable', 'message': 'This RFQ is no longer editable.'})
    if db.scalar(select(Quotation.id).where(Quotation.rfq_id == rfq.id, Quotation.status == 'awarded').limit(1)):
        raise HTTPException(409, {'code': 'rfq_awarded', 'message': 'An awarded RFQ cannot be revised.'})
    if revision.status != 'pending' or revision.number != rfq.revision + 1:
        raise HTTPException(409, {'code': 'stale_revision', 'message': 'This revision is outdated. Review the current RFQ.'})
    if not (revision.changes['fields'] or revision.changes['added_materials'] or revision.changes['removed_materials']
            or revision.changes['modified_materials'] or revision.changes.get('added_attachments')
            or revision.changes.get('removed_attachments')):
        raise HTTPException(422, 'No RFQ changes to publish')
    payload = RFQIn.model_validate({**revision.after_snapshot['rfq'], 'buyer_org_id': rfq.buyer_org_id,
                                    'items': revision.after_snapshot['items']})
    lines = prepare_lines(db, payload.items)
    current = {item.id: item for item in db.scalars(select(RFQItem).where(
        RFQItem.rfq_id == rfq.id, RFQItem.is_active.is_(True))).all()}
    before_ids = {item['id'] for item in revision.before_snapshot['items']}
    if before_ids != set(current):
        raise HTTPException(409, {'code': 'stale_revision', 'message': 'RFQ materials changed. Stage a new revision.'})
    affected = set(revision.changes['quotation_item_ids'])
    commercial_changed = bool({'response_deadline', 'required_delivery_date', 'delivery_country',
                               'city', 'delivery_address', 'additional_instructions'} & revision.changes['fields'].keys())
    affected_quote_ids = set()
    if affected:
        # Capture each submitted/draft quotation's original request before mutating RFQ rows.
        for line in db.scalars(select(QuoteLine).join(Quotation, Quotation.id == QuoteLine.quotation_id)
                               .where(Quotation.rfq_id == rfq.id, QuoteLine.rfq_item_id.in_(affected))):
            affected_quote_ids.add(line.quotation_id)
            if line.requested_snapshot is None and line.rfq_item_id in current:
                line.requested_snapshot = record(current[line.rfq_item_id])
    if revision.changes['added_materials'] or commercial_changed:
        affected_quote_ids.update(db.scalars(select(Quotation.id).where(Quotation.rfq_id == rfq.id)))
    if affected_quote_ids:
        for quote in db.scalars(select(Quotation).where(Quotation.id.in_(affected_quote_ids))):
            quote.review_required = True
            quote.review_reasons = list(dict.fromkeys([*(quote.review_reasons or []),
                f'RFQ revision {revision.number} changed requested materials; supplier review required']))
    db.execute(update(Quotation).where(Quotation.rfq_id == rfq.id,
        Quotation.id.not_in(affected_quote_ids),
        or_(Quotation.status == 'draft', Quotation.review_required.is_(False)))
        .values(rfq_revision=revision.number))
    for line in lines:
        item_id = line.pop('id')
        if item_id in current:
            item = current.pop(item_id)
            for field, value in line.items(): setattr(item, field, value)
        else:
            db.add(RFQItem(id=item_id, rfq_id=rfq.id, **line))
    now = datetime.now(UTC)
    for item in current.values():
        item.is_active, item.removed_at = False, now
    from app.models import StoredFile
    additions = set(revision.after_snapshot.get('attachment_additions') or [])
    removals = set(revision.after_snapshot.get('attachment_removals') or [])
    for stored in db.scalars(select(StoredFile).where(StoredFile.entity_type == 'rfq_revision',
            StoredFile.entity_id == revision.id, StoredFile.id.in_(additions))):
        stored.entity_type, stored.entity_id = 'rfq', rfq.id
    local_additions = [entry for entry in revision.after_snapshot.get('local_attachment_additions', []) if entry['id'] in additions]
    if local_additions:
        rfq.attachment_records = [*(rfq.attachment_records or []), *local_additions]
    if removals:
        rfq.attachment_records = [entry for entry in (rfq.attachment_records or []) if entry['id'] not in removals]
        for stored in db.scalars(select(StoredFile).where(StoredFile.entity_type == 'rfq',
                StoredFile.entity_id == rfq.id, StoredFile.id.in_(removals))):
            stored.status, stored.deleted_at = 'deleted', now
    for field in RFQ_FIELDS: setattr(rfq, field, getattr(payload, field))
    rfq.revision = revision.number
    rfq.updated_at, rfq.updated_by_user_id = now, user.id
    revision.status, revision.published_at = 'published', now
    if revision.changes['matching_changed']:
        # The existing indexed matcher evaluates previous matches plus candidates for
        # changed lines. Delivery stays in the outbox and never blocks this response.
        db.add(Outbox(event='rfq.published',
            payload={'rfq_id': rfq.id, 'revision': revision.number,
                     'matching_item_ids': revision.changes['matching_item_ids'],
                     'full_rematch': bool(MATCH_FIELDS & revision.changes['fields'].keys())},
            dedupe_key=f'rfq-revision-match:{rfq.id}:{revision.number}'))
    db.add(Outbox(event='rfq.revised',
        payload={'rfq_id': rfq.id, 'revision': revision.number,
                 'matching_changed': revision.changes['matching_changed'],
                 'affected_item_ids': revision.changes['quotation_item_ids'],
                 'notify_all': bool(revision.changes['fields'] or revision.changes['removed_materials']
                                    or revision.changes.get('added_attachments') or revision.changes.get('removed_attachments'))},
        dedupe_key=f'rfq-revised:{rfq.id}:{revision.number}'))
    audit(db, user, 'rfq.revision_published', rfq.buyer_org_id, rfq.id,
          revision=revision.number, changes=revision.changes)
    db.commit()
    return {'revision': serialized(revision), 'detail': contents(db, rfq)}


@router.delete('/{rfq_id}/revisions/{revision_id}', response_model=DiscardedRevisionOut)
def discard_revision(rfq_id: str, revision_id: str, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    from app.models import StoredFile
    rfq = db.scalar(select(RFQ).where(RFQ.id == rfq_id).with_for_update())
    if not rfq: raise HTTPException(404, 'RFQ not found')
    org_member(db, user, rfq.buyer_org_id, {'owner', 'manager', 'buyer'})
    revision = db.scalar(select(RFQRevision).where(RFQRevision.id == revision_id,
        RFQRevision.rfq_id == rfq.id).with_for_update())
    if not revision or revision.status != 'pending' or revision.created_by_user_id != user.id:
        raise HTTPException(409, 'Only your pending revision can be discarded')
    staged = db.scalars(select(StoredFile).where(StoredFile.entity_type == 'rfq_revision',
        StoredFile.entity_id == revision.id)).all()
    object_keys = [item.object_key for item in staged]
    local_keys = [entry['storage_key'] for entry in revision.after_snapshot.get('local_attachment_additions', [])]
    for item in staged: db.delete(item)
    audit(db, user, 'rfq.revision_discarded', rfq.buyer_org_id, rfq.id, revision=revision.number)
    db.delete(revision)
    db.commit()
    from app.storage import cleanup_object_best_effort
    for key in object_keys: cleanup_object_best_effort('private', key)
    if local_keys:
        from pathlib import Path
        from app.config import get_settings
        from app.import_storage import LocalImportStorage
        storage = LocalImportStorage(str(Path(get_settings().upload_dir) / 'rfq-attachments'))
        for key in local_keys: storage.delete(key)
    return {'discarded': True}
