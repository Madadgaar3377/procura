from io import BytesIO
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import Settings
from app.storage import StorageUnavailable, bucket_for, upload_file


def test_r2_config_is_optional_but_strict_when_enabled():
    Settings(r2_enabled=False).validate_r2()
    with pytest.raises(RuntimeError, match='incomplete'):
        Settings(r2_enabled=True).validate_r2()
    settings = Settings(r2_enabled=True, r2_account_id='acct', r2_access_key_id='key',
        r2_secret_access_key='secret', r2_endpoint_url='https://acct.r2.cloudflarestorage.com')
    settings.validate_r2()


def test_bucket_selection_does_not_accept_client_bucket_names(monkeypatch):
    from app import storage
    monkeypatch.setattr(storage, 'get_settings', lambda: SimpleNamespace(
        r2_public_bucket='public-bucket', r2_private_bucket='private-bucket'))
    assert bucket_for('private') == 'private-bucket'
    assert bucket_for('public') == 'public-bucket'
    with pytest.raises(ValueError):
        bucket_for('attacker-bucket')


def test_upload_is_bounded_and_records_verified_digest(monkeypatch):
    from app import storage

    class Client:
        def put_object(self, **params):
            self.params = params
            return {'ETag': '"etag-value"'}

    client = Client()
    exceptions = ModuleType('botocore.exceptions')
    exceptions.BotoCoreError = type('BotoCoreError', (Exception,), {})
    exceptions.ClientError = type('ClientError', (Exception,), {})
    package = ModuleType('botocore')
    package.exceptions = exceptions
    monkeypatch.setitem(sys.modules, 'botocore', package)
    monkeypatch.setitem(sys.modules, 'botocore.exceptions', exceptions)
    monkeypatch.setattr(storage, 'get_storage_client', lambda: client)
    result = upload_file('private', 'private/rfq/id/object', BytesIO(b'%PDF-data'),
        'application/pdf', max_bytes=100)
    assert result['size_bytes'] == 9
    assert len(result['checksum_sha256']) == 64
    assert result['etag'] == 'etag-value'
    assert client.params['Bucket'] == 'procumaterial-private'
    with pytest.raises(ValueError):
        upload_file('private', 'private/rfq/id/too-big', BytesIO(b'123'),
            'application/pdf', max_bytes=2)


def test_presigned_download_and_delete_use_server_selected_private_bucket(monkeypatch):
    from app import storage

    exceptions = ModuleType('botocore.exceptions')
    exceptions.BotoCoreError = type('BotoCoreError', (Exception,), {})
    exceptions.ClientError = type('ClientError', (Exception,), {})
    package = ModuleType('botocore')
    package.exceptions = exceptions
    monkeypatch.setitem(sys.modules, 'botocore', package)
    monkeypatch.setitem(sys.modules, 'botocore.exceptions', exceptions)

    class Client:
        def generate_presigned_url(self, operation, Params, ExpiresIn):
            self.params = (operation, Params, ExpiresIn)
            return 'https://signed.example/short-lived'
        def delete_object(self, **params): self.deleted = params

    client = Client()
    monkeypatch.setattr(storage, 'get_storage_client', lambda: client)
    url = storage.generate_download_url('private', 'private/rfq/id/key', 'quote.pdf', 'application/pdf', 120)
    assert url.startswith('https://signed.example/')
    assert client.params[0] == 'get_object'
    assert client.params[1]['Bucket'] == 'procumaterial-private'
    assert client.params[2] == 120
    storage.delete_object('private', 'private/rfq/id/key')
    assert client.deleted == {'Bucket': 'procumaterial-private', 'Key': 'private/rfq/id/key'}


def test_disabled_storage_fails_clearly(monkeypatch):
    from app import storage
    storage.get_storage_client.cache_clear()
    monkeypatch.setattr(storage, 'get_settings', lambda: SimpleNamespace(
        r2_enabled=False, validate_r2=lambda: None))
    with pytest.raises(StorageUnavailable, match='disabled'):
        storage.get_storage_client()
    storage.get_storage_client.cache_clear()


def test_stored_file_migration_adds_metadata_table_without_touching_existing_tables():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration_path = Path(__file__).parents[1] / 'alembic' / 'versions' / 'y85b20d17c3e_stored_files.py'
    spec = importlib.util.spec_from_file_location('stored_files_migration', migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE rfqs (id VARCHAR(36) PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        tables = set(inspect(connection).get_table_names())
        assert {'users', 'organizations', 'rfqs', 'stored_files'} <= tables
        columns = {column['name'] for column in inspect(connection).get_columns('stored_files')}
        assert {'object_key', 'checksum_sha256', 'status', 'entity_id', 'deleted_at'} <= columns
    engine.dispose()
