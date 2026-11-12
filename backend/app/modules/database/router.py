import logging
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import catalog_cache
from app.database import engine, get_db, get_pool_status, query_metrics
from app.models import Outbox
from app.redis_client import redis_metrics
from app.schemas import DatabaseConnectStatus
from app.security import admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/db', tags=['Database Status & Performance'])


@router.get('/connect/status', response_model=DatabaseConnectStatus)
def connect_status():
    """Return a sanitized, read-only connectivity probe for the configured DB."""
    checked_at = datetime.now(timezone.utc)
    try:
        with engine.connect() as connection:
            if connection.dialect.name == 'postgresql':
                with connection.begin():
                    connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                    connection.exec_driver_sql('SELECT 1').scalar_one()
            else:
                connection.exec_driver_sql('SELECT 1').scalar_one()
    except SQLAlchemyError as exc:
        error_type = type(exc).__name__
        logger.warning('Database connectivity probe failed error_type=%s', error_type)
        return DatabaseConnectStatus(
            connected=False,
            status='disconnected',
            message='Database connection failed. Check server logs for details.',
            error=error_type,
            checked_at=checked_at,
        )

    return DatabaseConnectStatus(
        connected=True,
        status='connected',
        message='Database connection is healthy.',
        error=None,
        checked_at=checked_at,
    )


@router.get('/performance')
def performance_metrics(db: Session = Depends(get_db), admin=Depends(admin_user)):
    """Comprehensive observability for database pooling, Redis latency, L1/L2 cache, and outbox queue."""
    t0 = perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        db_latency = round((perf_counter() - t0) * 1000.0, 2)
        db_healthy = True
    except Exception:
        db_latency = None
        db_healthy = False

    # Outbox status counts
    queue_counts = {}
    total_queue = 0
    try:
        rows = db.execute(select(Outbox.status, func.count()).group_by(Outbox.status)).all()
        for status, count in rows:
            queue_counts[status] = count
            if status in {'pending', 'retry', 'awaiting_status', 'processing'}:
                total_queue += count
    except Exception:
        queue_counts = {'error': 'unavailable'}

    return {
        'database': {
            'healthy': db_healthy,
            'dialect': engine.dialect.name,
            'latency_ms': db_latency,
            'pool': get_pool_status(),
            'queries': query_metrics(),
        },
        'redis': redis_metrics(),
        'cache': catalog_cache.metrics(),
        'queue': {
            'active_depth': total_queue,
            'by_status': queue_counts,
        },
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
