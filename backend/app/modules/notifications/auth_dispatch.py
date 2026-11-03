"""Start only the authentication deliveries authorized by the current request."""

import logging

from fastapi import BackgroundTasks

from app.config import get_settings
from app.database import SessionLocal

logger = logging.getLogger("uvicorn.error")


def dispatch_auth_deliveries(event_ids: list[str]) -> None:
    from app.notification_worker import process_batch

    try:
        with SessionLocal() as db:
            # Targeted IDs avoid draining unrelated supplier/customer notices.
            process_batch(db, limit=len(event_ids), event_ids=event_ids)
    except Exception as exc:  # noqa: BLE001 - never log driver credentials or message bodies
        logger.warning("Authentication dispatch unavailable (%s); jobs remain queued", type(exc).__name__)


def schedule_auth_deliveries(tasks: BackgroundTasks, event_ids: list[str]) -> None:
    settings = get_settings()
    logger.info("Authentication delivery configuration: auto_dispatch=%s mock=%s SMTP_configured=%s SMS_enabled=%s SMS_mode=%s SMS_credentials=%s jobs=%s", settings.auth_delivery_auto_dispatch, settings.mock_notifications, bool(settings.smtp_host and settings.smtp_username and settings.smtp_password), settings.sms_gateway_enabled, settings.sms_gateway_mode, bool(settings.sms_gate_username and settings.sms_gate_password), len(event_ids))
    if not settings.auth_delivery_auto_dispatch and event_ids:
        logger.warning("Authentication jobs queued; run notification_worker because automatic dispatch is disabled")
    if settings.auth_delivery_auto_dispatch and event_ids:
        tasks.add_task(dispatch_auth_deliveries, list(event_ids))
