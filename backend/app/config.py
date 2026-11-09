from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = 'development'
    database_url: str = 'sqlite:///./data/procurement.db'
    jwt_secret: str = 'CHANGE_ME_FOR_LOCAL_ONLY'
    jwt_issuer: str = Field(default='procura', min_length=1, max_length=200)
    jwt_audience: str = Field(default='procura-api', min_length=1, max_length=200)
    token_minutes: int = 30
    refresh_days: int = 7
    admin_bootstrap_key: str = ''
    redis_url: str = ''
    cors_origins: str = 'http://localhost:3000,http://127.0.0.1:3000,https://procumaterial.com,https://www.procumaterial.com'
    database_connect_timeout: int = Field(default=5,ge=1,le=30)
    database_lock_timeout_ms: int = Field(default=3000,ge=100,le=30000)
    database_statement_timeout_ms: int = Field(default=15000,ge=1000,le=120000)
    database_sslrootcert: str = ''
    db_pool_size: int = Field(default=5, ge=1, le=50, validation_alias=AliasChoices('db_pool_size', 'DB_POOL_SIZE'))
    db_max_overflow: int = Field(default=2, ge=0, le=50, validation_alias=AliasChoices('db_max_overflow', 'DB_MAX_OVERFLOW'))
    db_pool_timeout: int = Field(default=10, ge=1, le=60, validation_alias=AliasChoices('db_pool_timeout', 'DB_POOL_TIMEOUT'))
    db_pool_recycle: int = Field(default=1800, ge=60, le=7200, validation_alias=AliasChoices('db_pool_recycle', 'DB_POOL_RECYCLE'))
    mock_notifications: bool = True
    auth_delivery_auto_dispatch: bool = True
    recovery_minutes: int = Field(default=10, ge=5, le=30)
    otp_resend_seconds: int = Field(default=60, ge=30, le=300)
    sms_provider: str = 'android_gateway'
    sms_gateway_enabled: bool = False
    sms_gateway_mode: str = 'mock'
    notification_test_phone: str = Field(default='',validation_alias=AliasChoices('notification_test_phone','sms_test_phone','test_phone'))
    notification_test_email: str = Field(default='',validation_alias=AliasChoices('notification_test_email','smtp_test_email','test_email'))
    notification_default_country: str = 'SA'
    sms_gate_api_url: str = Field(
        default='https://api.sms-gate.app/3rdparty/v1/messages',
        validation_alias=AliasChoices('sms_gate_api_url', 'sms_gateway_api_url', 'sms_gateway_base_url'),
    )
    sms_gate_username: str = Field(
        default='',
        validation_alias=AliasChoices('sms_gate_username', 'sms_gateway_username'),
    )
    sms_gate_password: str = Field(
        default='',
        validation_alias=AliasChoices('sms_gate_password', 'sms_gateway_password'),
    )

    @field_validator('sms_gate_api_url', mode='after')
    @classmethod
    def _normalize_sms_gate_url(cls, v: str) -> str:
        v = (v or '').rstrip('/')
        if v and not v.endswith('/3rdparty/v1/messages'):
            v = f'{v}/3rdparty/v1/messages'
        return v
    support_url: str = ''
    frontend_url: str = 'https://procumaterial.com'
    smtp_host: str = Field(
        default='mail.spacemail.com',
        validation_alias=AliasChoices('smtp_host', 'SMTP_HOST'),
    )
    smtp_port: int = Field(
        default=465,
        ge=1,
        le=65535,
        validation_alias=AliasChoices('smtp_port', 'SMTP_PORT'),
    )
    smtp_security: str = Field(
        default='SSL/TLS',
        validation_alias=AliasChoices('smtp_security', 'SMTP_SECURITY'),
    )
    smtp_username: str = Field(
        default='',
        validation_alias=AliasChoices('smtp_username', 'SMTP_USERNAME'),
    )
    smtp_password: str = Field(
        default='',
        validation_alias=AliasChoices('smtp_password', 'SMTP_PASSWORD'),
    )
    smtp_from_email: str = Field(
        default='',
        validation_alias=AliasChoices('smtp_from_email', 'SMTP_FROM_EMAIL'),
    )
    smtp_from_name: str = Field(
        default='Procumaterial',
        validation_alias=AliasChoices('smtp_from_name', 'SMTP_FROM_NAME'),
    )
    smtp_use_tls: bool = Field(
        default=True,
        validation_alias=AliasChoices('smtp_use_tls', 'SMTP_USE_TLS'),
    )
    field_encryption_key: str = ''
    delivery_timeout_seconds: int = Field(default=10,ge=1,le=30)
    delivery_max_attempts: int = Field(default=5,ge=1,le=10)
    delivery_status_max_polls: int = Field(default=24,ge=1,le=100)
    upload_dir: str = './data/private'
    max_upload_bytes: int = 5_000_000
    r2_enabled: bool = False
    r2_account_id: str = ''
    r2_access_key_id: str = ''
    r2_secret_access_key: str = ''
    r2_endpoint_url: str = ''
    r2_region: str = 'auto'
    r2_public_bucket: str = 'procumaterial-public'
    r2_private_bucket: str = 'procumaterial-private'
    r2_public_base_url: str = ''
    r2_presigned_upload_ttl: int = Field(default=300, ge=60, le=900)
    r2_presigned_download_ttl: int = Field(default=300, ge=60, le=900)
    r2_max_image_bytes: int = Field(default=10_485_760, ge=1)
    r2_max_document_bytes: int = Field(default=26_214_400, ge=1)
    r2_allowed_origins: str = ''
    matching_batch_size: int = 5
    require_capability_approval: bool = False
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1] / '.env', extra='ignore')

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == 'production'

    @property
    def is_test(self) -> bool:
        return self.app_env.lower() == 'test'

    def validate_r2(self) -> None:
        if not self.r2_enabled:
            return
        required = (self.r2_account_id, self.r2_access_key_id, self.r2_secret_access_key,
                    self.r2_endpoint_url, self.r2_public_bucket, self.r2_private_bucket)
        if not all(value.strip() for value in required):
            raise RuntimeError('R2 is enabled but required R2 configuration is incomplete')
        expected = f'https://{self.r2_account_id}.r2.cloudflarestorage.com'
        if self.r2_endpoint_url.rstrip('/') != expected:
            raise RuntimeError('R2_ENDPOINT_URL must use the configured Cloudflare account endpoint')
        if self.r2_region != 'auto':
            raise RuntimeError('R2_REGION must be auto')


def normalize_database_url(url: str) -> str:
    """Use the installed psycopg v3 driver for ordinary PostgreSQL URLs."""
    if url.startswith('postgres://'):
        return 'postgresql+psycopg://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + url[len('postgresql://'):]
    if url.startswith('postgresql+psycopg2://'):
        return 'postgresql+psycopg://' + url[len('postgresql+psycopg2://'):]
    return url

@lru_cache
def get_settings() -> Settings:
    return Settings()
