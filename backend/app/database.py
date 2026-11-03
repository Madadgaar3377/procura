from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import make_url
from app.config import get_settings, normalize_database_url

class Base(DeclarativeBase):
    pass

def build_engine(url: str):
    url = normalize_database_url(url)
    parsed=make_url(url)
    if parsed.get_backend_name()=='postgresql' and parsed.host not in {'127.0.0.1','localhost','::1'}:
        # sslmode=require encrypts traffic but does not verify server identity.
        # Aiven/public CA trust or an explicitly configured CA must verify hostname.
        query=dict(parsed.query)
        query['sslmode']='verify-full'
        certificate=get_settings().database_sslrootcert.strip()
        if certificate:
            from pathlib import Path
            ca_path=Path(certificate).expanduser()
            if not ca_path.is_absolute():
                ca_path=Path(__file__).resolve().parents[1]/ca_path
            query['sslrootcert']=str(ca_path.resolve())
        else:
            query.setdefault('sslrootcert','system')
        url=parsed.set(query=query).render_as_string(hide_password=False)
    kwargs: dict[str, Any] = dict(pool_pre_ping=True, hide_parameters=True)
    if url.startswith('sqlite'):
        kwargs['connect_args'] = {'check_same_thread': False}
        if ':memory:' in url:
            kwargs['poolclass'] = StaticPool
    else:
        settings=get_settings()
        kwargs.update(pool_size=10, max_overflow=20, pool_timeout=5, pool_recycle=1800,
            connect_args={'connect_timeout':settings.database_connect_timeout,
                'options':f'-c lock_timeout={settings.database_lock_timeout_ms} -c statement_timeout={settings.database_statement_timeout_ms} -c idle_in_transaction_session_timeout=30000'})
    engine = create_engine(url, **kwargs)
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def enable_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()
    return engine

engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

from app.profile_schema import compatible_organization_reads
event.listen(SessionLocal.class_, 'do_orm_execute', compatible_organization_reads)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
