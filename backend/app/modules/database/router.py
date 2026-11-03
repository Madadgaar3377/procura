import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.schemas import DatabaseConnectStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/db', tags=['Database Status'])


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
