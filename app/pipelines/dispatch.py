from __future__ import annotations

import asyncio
import logging

from app.core.redis_manager import publish_notification
from app.db.session import AsyncSessionLocal
from app.integrations.normalizer import NotificationEvent
from app.models.integration_account import IntegrationAccount
from app.pipelines.entry import run_pipeline_with_memory
from app.repositories.notification_repository import serialize_notification
from app.repositories.raw_event_repository import mark_raw_event_status

logger = logging.getLogger(__name__)

PIPELINE_TIMEOUT_SECONDS = 60


async def process_event_pipeline(event: NotificationEvent, *, raw_event_id: int) -> None:
    """raw_event 저장 이후 AI 분류 파이프라인을 실행하고 결과를 저장한다.

    raw_event 저장 트랜잭션과 분리된 별도 호출로 사용할 것 — 파이프라인 실패가
    이미 저장된 raw_event를 롤백시키지 않도록 하기 위함. 파이프라인 예외는 로깅하고
    raw_event.status를 failed로 기록한다 (자동 재시도는 아직 없음 — #13).
    """
    try:
        notification = await asyncio.wait_for(
            run_pipeline_with_memory(event), timeout=PIPELINE_TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.exception(
            "파이프라인 처리 실패: provider=%s raw_event_id=%s", event.provider, raw_event_id
        )
        await _mark_status(raw_event_id, "failed", str(exc))
        return

    if notification is None:
        await _mark_status(raw_event_id, "completed")
        return

    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                db.add(notification)
    except Exception as exc:
        logger.exception(
            "Notification 저장 실패: provider=%s raw_event_id=%s", event.provider, raw_event_id
        )
        await _mark_status(raw_event_id, "failed", str(exc))
        return

    await _mark_status(raw_event_id, "completed")

    # SSE 실시간 발행은 부가 기능 — 실패해도 이미 완료된 저장 결과(raw_event status)에
    # 영향을 주지 않도록 별도 try/except로 분리.
    try:
        async with AsyncSessionLocal() as db:
            integration = await db.get(IntegrationAccount, notification.integration_id)
        if integration is not None:
            payload = serialize_notification(notification, provider=integration.provider)
            await publish_notification(integration.user_id, payload)
    except Exception:
        logger.exception(
            "실시간 알림 발행 실패 (SSE): notification_id=%s integration_id=%s",
            notification.id,
            notification.integration_id,
        )


async def _mark_status(raw_event_id: int, status: str, error_message: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        await mark_raw_event_status(db, raw_event_id, status, error_message)
