from __future__ import annotations

import asyncio
import logging

from app.db.session import AsyncSessionLocal
from app.integrations.normalizer import NotificationEvent
from app.pipelines.entry import run_pipeline_with_memory

logger = logging.getLogger(__name__)

PIPELINE_TIMEOUT_SECONDS = 60


async def process_event_pipeline(event: NotificationEvent, *, raw_event_id: int) -> None:
    """raw_event 저장 이후 AI 분류 파이프라인을 실행하고 결과를 저장한다.

    raw_event 저장 트랜잭션과 분리된 별도 호출로 사용할 것 — 파이프라인 실패가
    이미 저장된 raw_event를 롤백시키지 않도록 하기 위함. 파이프라인 예외는 로깅 후
    무시한다 (재시도/dead-letter는 이슈 #13에서 별도 처리 예정).
    """
    try:
        notification = await asyncio.wait_for(
            run_pipeline_with_memory(event), timeout=PIPELINE_TIMEOUT_SECONDS
        )
    except Exception:
        logger.exception(
            "파이프라인 처리 실패: provider=%s raw_event_id=%s", event.provider, raw_event_id
        )
        return

    if notification is None:
        return

    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                db.add(notification)
    except Exception:
        logger.exception(
            "Notification 저장 실패: provider=%s raw_event_id=%s", event.provider, raw_event_id
        )
