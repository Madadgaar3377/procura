from datetime import UTC, datetime
from pathlib import PurePath
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import RFQ, StoredFile, User, utcnow
from app.security import audit, current_user, org_member
from app.rate_limit import enforce_rate_limit
from app.storage import (StorageUnavailable, delete_object, generate_download_url,
                         generate_upload_url, get_object_metadata, get_object_prefix)

router = APIRouter(prefix='/files', tags=['File storage'])
ALLOWED = {'.pdf': ('application/pdf', b'%PDF'), '.png': ('image/png', b'\x89PNG\r\n\x1a\n'),
           '.jpg': ('image/jpeg', b'\xff\xd8\xff'), '.jpeg': ('image/jpeg', b'\xff\xd8\xff')}


class UploadRequest(BaseModel):
    purpose: str = Field(pattern='^rfq_attachment$')
    entity_id: str = Field(min_length=1, max_length=36)
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(max_length=120)
    size_bytes: int = Field(gt=0)


def _authorized(rfq, user, db, *, edit=False):
    if not rfq:
        raise HTTPException(404, 'RFQ not found')
    if edit:
        org_member(db, user, rfq.buyer_org_id, {'owner', 'manager', 'buyer'})
        if rfq.status != 'draft':
            raise HTTPException(409, 'Only draft attachments may be changed')
        return
    # Reuse the established RFQ detail policy: buyer members and invited suppliers.
    from app.modules.rfqs.router import details
    details(rfq.id, user, db)


def _file_access(file, user, db, *, edit=False):
    if file.entity_type != 'rfq' or file.purpose != 'rfq_attachment':
        if not user.is_admin:
            raise HTTPException(404, 'File not found')
        return
    _authorized(db.get(RFQ, file.entity_id), user, db, edit=edit)


@router.post('/upload-url', status_code=201)
def upload_url(payload: UploadRequest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    enforce_rate_limit(request, scope='api-r2-upload', identity=user.id, limit=10, window_seconds=60)
    settings = get_settings()
    if not settings.r2_enabled:
        raise HTTPException(503, 'File storage is unavailable')
    rfq = db.get(RFQ, payload.entity_id)
    _authorized(rfq, user, db, edit=True)
    suffix = PurePath(payload.filename.replace('\\', '/')).suffix.lower()
    spec = ALLOWED.get(suffix)
    if not spec or payload.content_type != spec[0]:
        raise HTTPException(415, 'Attach a PDF, PNG or JPEG')
    if payload.size_bytes > settings.r2_max_document_bytes:
        raise HTTPException(413, 'File exceeds the configured size limit')
    count = db.scalar(select(func.count(StoredFile.id)).where(StoredFile.entity_type == 'rfq',
        StoredFile.entity_id == rfq.id, StoredFile.status.in_(['pending_upload', 'available']))) or 0
    if count >= 10:
        raise HTTPException(422, 'At most 10 RFQ attachments are allowed')
    safe = PurePath(payload.filename.replace('\\', '/')).name
    safe = ''.join(c for c in safe if c.isprintable() and c not in '"\r\n')[:200] or 'attachment' + suffix
    file = StoredFile(bucket_type='private', object_key=f'private/rfq/{rfq.id}/{uuid4()}',
        original_filename=safe, safe_filename=safe, content_type=spec[0], file_extension=suffix,
        size_bytes=payload.size_bytes, visibility='private', purpose='rfq_attachment', status='pending_upload',
        owner_user_id=user.id, uploaded_by_user_id=user.id, organization_id=rfq.buyer_org_id,
        entity_type='rfq', entity_id=rfq.id)
    db.add(file)
    try:
        url = generate_upload_url('private', file.object_key, spec[0])
        db.flush()
        audit(db, user, 'file.upload_authorized', rfq.buyer_org_id, file.id, purpose=file.purpose)
        db.commit()
    except StorageUnavailable as exc:
        db.rollback()
        raise HTTPException(503, 'File storage is temporarily unavailable') from exc
    return {'file_id': file.id, 'upload_url': url, 'method': 'PUT', 'headers': {
        'Content-Type': file.content_type, 'Cache-Control': 'private, no-store'},
        'expires_in': settings.r2_presigned_upload_ttl}


@router.post('/{file_id}/complete')
def complete(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    file = db.get(StoredFile, file_id)
    if not file:
        raise HTTPException(404, 'File not found')
    _file_access(file, user, db, edit=True)
    if file.status == 'available':
        return {'id': file.id, 'status': file.status, 'size_bytes': file.size_bytes}
    if file.status != 'pending_upload':
        raise HTTPException(409, 'File upload is not pending')
    try:
        metadata = get_object_metadata(file.bucket_type, file.object_key)
    except StorageUnavailable as exc:
        raise HTTPException(503, 'File storage is temporarily unavailable') from exc
    if not metadata:
        raise HTTPException(409, 'Uploaded file was not found')
    if metadata.get('ContentLength', 0) <= 0 or metadata.get('ContentLength', 0) > get_settings().r2_max_document_bytes:
        file.status = 'failed'
        db.commit()
        try:
            delete_object(file.bucket_type, file.object_key)
        except StorageUnavailable:
            pass
        raise HTTPException(413, 'Uploaded file exceeds the configured size limit')
    if metadata.get('ContentType') != file.content_type:
        file.status = 'quarantined'
        db.commit()
        raise HTTPException(422, 'Uploaded file type does not match its authorization')
    expected_signature = ALLOWED[file.file_extension][1]
    try:
        signature = get_object_prefix(file.bucket_type, file.object_key, len(expected_signature))
    except StorageUnavailable as exc:
        raise HTTPException(503, 'File storage is temporarily unavailable') from exc
    if not signature.startswith(expected_signature):
        file.status = 'quarantined'
        db.commit()
        raise HTTPException(422, 'Uploaded file content does not match its declared type')
    file.size_bytes = metadata['ContentLength']
    file.etag = metadata.get('ETag', '').strip('"') or None
    file.status = 'available'
    file.updated_at = utcnow()
    audit(db, user, 'file.upload_completed', file.organization_id, file.id, size_bytes=file.size_bytes)
    db.commit()
    return {'id': file.id, 'status': file.status, 'size_bytes': file.size_bytes}


@router.get('/{file_id}/download-url')
def download_url(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    file = db.get(StoredFile, file_id)
    if not file or file.deleted_at or file.status != 'available':
        raise HTTPException(404, 'File not found')
    _file_access(file, user, db)
    try:
        url = generate_download_url(file.bucket_type, file.object_key, file.safe_filename, file.content_type)
    except StorageUnavailable as exc:
        raise HTTPException(503, 'File storage is temporarily unavailable') from exc
    audit(db, user, 'file.download_authorized', file.organization_id, file.id)
    db.commit()
    return {'download_url': url, 'expires_in': get_settings().r2_presigned_download_ttl}


@router.delete('/{file_id}', status_code=204)
def remove_file(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    file = db.get(StoredFile, file_id)
    if not file or file.deleted_at:
        return
    _file_access(file, user, db, edit=True)
    file.status = 'deleted'
    file.deleted_at = datetime.now(UTC)
    audit(db, user, 'file.deleted', file.organization_id, file.id)
    db.commit()
    try:
        delete_object(file.bucket_type, file.object_key)
    except StorageUnavailable:
        # The soft-delete is durable; a reconciliation job can retry safely.
        pass


@router.get('/admin/summary')
def storage_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(403, 'Administrator access required')
    by_bucket = db.execute(select(StoredFile.bucket_type, func.count()).group_by(StoredFile.bucket_type)).all()
    by_status = db.execute(select(StoredFile.status, func.count()).group_by(StoredFile.status)).all()
    return {'total': db.scalar(select(func.count(StoredFile.id))) or 0,
        'bytes': db.scalar(select(func.coalesce(func.sum(StoredFile.size_bytes), 0))) or 0,
        'by_bucket': [{'bucket_type': kind, 'count': count} for kind, count in by_bucket],
        'by_status': [{'status': status, 'count': count} for status, count in by_status]}
