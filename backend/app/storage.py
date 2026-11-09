"""Central S3-compatible Cloudflare R2 client. Secrets stay server side."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from typing import BinaryIO

from app.config import get_settings


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
