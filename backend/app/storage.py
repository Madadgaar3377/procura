"""Central S3-compatible Cloudflare R2 client. Secrets stay server side."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import io
from pathlib import PurePath
from typing import BinaryIO
from uuid import uuid4
import zipfile

from app.config import get_settings

ALLOWED_IMAGE_TYPES: dict[str, tuple[str, ...]] = {
    '.jpg': ('image/jpeg', 'image/pjpeg', 'image/jpg'),
    '.jpeg': ('image/jpeg', 'image/pjpeg', 'image/jpg'),
    '.png': ('image/png', 'image/x-png'),
    '.webp': ('image/webp',),
}

ALLOWED_DOCUMENT_TYPES: dict[str, tuple[str, ...]] = {
    '.pdf': ('application/pdf',),
    '.png': ('image/png', 'image/x-png'),
    '.jpg': ('image/jpeg', 'image/pjpeg', 'image/jpg'),
    '.jpeg': ('image/jpeg', 'image/pjpeg', 'image/jpg'),
    '.webp': ('image/webp',),
    '.xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',),
    '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',),
}

_DANGEROUS_SNIPPETS = (b'<svg', b'<script', b'<!doctype html', b'<html', b'<?php')
_EXECUTABLE_PREFIXES = (b'MZ', b'\x7fELF', b'#!')


class StorageValidationError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class StorageUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_storage_client():
    settings = get_settings()
    settings.validate_r2()
    if not settings.r2_enabled:
        raise StorageUnavailable('Object storage is disabled')
    try:
        import boto3
        from botocore.config import Config
        return boto3.client('s3', endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name='auto', config=Config(signature_version='s3v4',
                connect_timeout=5, read_timeout=20, retries={'max_attempts': 2}))
    except ImportError as exc:
        raise StorageUnavailable('R2 storage dependency is unavailable') from exc


def bucket_for(bucket_type: str) -> str:
    settings = get_settings()
    if bucket_type == 'public':
        return settings.r2_public_bucket
    if bucket_type == 'private':
        return settings.r2_private_bucket
    raise ValueError('Invalid bucket type')


def public_object_url(key: str) -> str:
    """Build delivery URLs only for explicitly public-key paths with configured DNS."""
    from urllib.parse import quote, urlsplit
    base = get_settings().r2_public_base_url.strip().rstrip('/')
    parsed = urlsplit(base)
    if not base or parsed.scheme != 'https' or not parsed.netloc or ' ' in base:
        raise StorageUnavailable('Public image delivery domain is not configured')
    if not key.startswith('public/') or '..' in key.split('/') or key.startswith('/'):
        raise ValueError('Invalid public object key')
    return f'{base}/{quote(key, safe="/")}'


def upload_file(bucket_type: str, key: str, source: BinaryIO, content_type: str,
                cache_control: str = 'private, no-store', max_bytes: int | None = None):
    client = get_storage_client()
    body = bytearray()
    digest = sha256()
    while chunk := source.read(64 * 1024):
        body.extend(chunk)
        digest.update(chunk)
        if max_bytes is not None and len(body) > max_bytes:
            raise ValueError('Maximum file size exceeded')
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        result = client.put_object(Bucket=bucket_for(bucket_type), Key=key, Body=bytes(body),
            ContentType=content_type, CacheControl=cache_control, ChecksumSHA256=__import__('base64').b64encode(digest.digest()).decode())
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable('Object storage request failed') from exc
    return {'size_bytes': len(body), 'etag': result.get('ETag', '').strip('"'), 'checksum_sha256': digest.hexdigest()}


def generate_upload_url(bucket_type: str, key: str, content_type: str, ttl: int | None = None) -> str:
    from botocore.exceptions import BotoCoreError, ClientError
    from app.config import get_settings
    try:
        return get_storage_client().generate_presigned_url('put_object', Params={
            'Bucket': bucket_for(bucket_type), 'Key': key, 'ContentType': content_type,
            'CacheControl': 'private, no-store'}, ExpiresIn=ttl or get_settings().r2_presigned_upload_ttl)
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable('Object storage request failed') from exc


def generate_download_url(bucket_type: str, key: str, filename: str, content_type: str, ttl: int | None = None) -> str:
    from botocore.exceptions import BotoCoreError, ClientError
    from urllib.parse import quote
    from app.config import get_settings
    disposition = f'attachment; filename="{quote(filename, safe="")}"'
    try:
        return get_storage_client().generate_presigned_url('get_object', Params={
            'Bucket': bucket_for(bucket_type), 'Key': key, 'ResponseContentType': content_type,
            'ResponseContentDisposition': disposition, 'ResponseCacheControl': 'private, no-store'},
            ExpiresIn=ttl or get_settings().r2_presigned_download_ttl)
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable('Object storage request failed') from exc


def get_object_metadata(bucket_type: str, key: str):
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        return get_storage_client().head_object(Bucket=bucket_for(bucket_type), Key=key)
    except ClientError as exc:
        if exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 404 or exc.response.get('Error', {}).get('Code') in {'404', 'NoSuchKey', 'NotFound'}:
            return None
        raise StorageUnavailable('Object storage request failed') from exc


def get_object_prefix(bucket_type: str, key: str, length: int = 16) -> bytes:
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        response = get_storage_client().get_object(Bucket=bucket_for(bucket_type), Key=key,
            Range=f'bytes=0-{max(0, length - 1)}')
        return response['Body'].read(length)
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable('Object storage request failed') from exc
    except BotoCoreError as exc:
        raise StorageUnavailable('Object storage request failed') from exc


def object_exists(bucket_type: str, key: str) -> bool:
    return get_object_metadata(bucket_type, key) is not None


def delete_object(bucket_type: str, key: str) -> None:
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        get_storage_client().delete_object(Bucket=bucket_for(bucket_type), Key=key)
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable('Object storage request failed') from exc


def copy_object(source_bucket_type: str, source_key: str, target_bucket_type: str, target_key: str):
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        return get_storage_client().copy_object(Bucket=bucket_for(target_bucket_type), Key=target_key,
            CopySource={'Bucket': bucket_for(source_bucket_type), 'Key': source_key}, MetadataDirective='COPY')
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable('Object storage request failed') from exc


def health_check() -> bool:
    client = get_storage_client()
    settings = get_settings()
    client.head_bucket(Bucket=settings.r2_private_bucket)
    return True


def safe_filename(raw: str | None, fallback_ext: str = '') -> str:
    name = PurePath((raw or 'file').replace('\\', '/')).name
    cleaned = ''.join(c for c in name if c.isprintable() and c not in '"\r\n/\\')[:200]
    if not cleaned:
        return f'file{fallback_ext}'
    return cleaned


def build_object_key(prefix: str, entity_id: str, suffix: str = '') -> str:
    clean_prefix = prefix.strip('/')
    clean_suffix = suffix if suffix.startswith('.') or not suffix else f'.{suffix}'
    return f'{clean_prefix}/{entity_id}/{uuid4()}{clean_suffix}'


def validate_file_content(
    filename: str | None,
    content_type: str | None,
    content: bytes,
    allowed: dict[str, tuple[str, ...]],
    max_bytes: int,
) -> tuple[str, str]:
    """Validate extension, declared MIME, size, and actual binary signature."""
    if not content:
        raise StorageValidationError(422, 'Uploaded file is empty')
    if len(content) > max_bytes:
        raise StorageValidationError(413, 'File exceeds the configured size limit')

    suffix = PurePath((filename or '').replace('\\', '/')).suffix.lower()
    declared = (content_type or '').split(';', 1)[0].strip().lower()
    if suffix not in allowed or declared not in allowed[suffix]:
        raise StorageValidationError(415, 'Unsupported file type')

    def _reject_disguised_or_mismatch(mismatch_msg: str) -> None:
        head = content[:512].lstrip().lower()
        if any(content.startswith(prefix) for prefix in _EXECUTABLE_PREFIXES) or any(marker in head for marker in _DANGEROUS_SNIPPETS):
            raise StorageValidationError(422, 'Executable or active markup files are not allowed')
        raise StorageValidationError(422, mismatch_msg)

    if suffix == '.pdf' and not content.startswith(b'%PDF'):
        _reject_disguised_or_mismatch('File content does not match PDF format')
    elif suffix == '.png' and not content.startswith(b'\x89PNG\r\n\x1a\n'):
        _reject_disguised_or_mismatch('File content does not match PNG format')
    elif suffix in {'.jpg', '.jpeg'} and not content.startswith(b'\xff\xd8\xff'):
        _reject_disguised_or_mismatch('File content does not match JPEG format')
    elif suffix == '.webp' and not (len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP'):
        _reject_disguised_or_mismatch('File content does not match WebP format')
    elif suffix in {'.xlsx', '.docx'}:
        if not content.startswith(b'PK\x03\x04'):
            _reject_disguised_or_mismatch('File content does not match Office Open XML format')
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as exc:
            raise StorageValidationError(422, 'Uploaded Office document is corrupted') from exc
        if '[Content_Types].xml' not in names:
            raise StorageValidationError(422, 'Uploaded Office document is invalid')
        required_prefix = 'xl/' if suffix == '.xlsx' else 'word/'
        if not any(name.startswith(required_prefix) for name in names):
            raise StorageValidationError(422, 'Uploaded Office document does not match its declared type')

    return suffix, allowed[suffix][0]


def optimize_image_variant(
    content: bytes,
    *,
    max_dim: int = 512,
    square_crop: bool = False,
) -> tuple[bytes, str, str]:
    """Decode, strip EXIF/metadata, resize/crop, and encode a clean WebP image."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = 25_000_000
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
        with Image.open(io.BytesIO(content)) as img:
            oriented = ImageOps.exif_transpose(img)
            if oriented.mode not in ('RGB', 'RGBA'):
                oriented = oriented.convert('RGBA' if 'A' in oriented.getbands() else 'RGB')
            if square_crop:
                w, h = oriented.size
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                oriented = oriented.crop((left, top, left + side, top + side))
            oriented.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            oriented.save(out, format='WEBP', quality=85, method=4)
            return out.getvalue(), 'image/webp', '.webp'
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise StorageValidationError(422, 'Uploaded image could not be processed') from exc


def stream_object(bucket_type: str, key: str) -> tuple[bytes, str, int]:
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        response = get_storage_client().get_object(Bucket=bucket_for(bucket_type), Key=key)
        data = response['Body'].read()
        content_type = response.get('ContentType') or 'application/octet-stream'
        return data, content_type, len(data)
    except ClientError as exc:
        if exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 404 or exc.response.get('Error', {}).get('Code') in {'404', 'NoSuchKey', 'NotFound'}:
            raise FileNotFoundError(key) from exc
        raise StorageUnavailable('Object storage request failed') from exc
    except BotoCoreError as exc:
        raise StorageUnavailable('Object storage request failed') from exc


def cleanup_object_best_effort(bucket_type: str, key: str | None) -> None:
    if not key:
        return
    try:
        delete_object(bucket_type, key)
    except StorageUnavailable:
        pass
