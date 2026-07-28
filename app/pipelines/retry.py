from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.integrations.discord.normalizer import normalize_message as normalize_discord_message
from app.integrations.normalizer import NotificationEvent
from app.integrations.slack.client import SlackClient
from app.integrations.slack.events import to_normalizer_payload
from app.integrations.slack.normalizer import normalize_message as normalize_slack_message
from app.models.raw_event import RawEvent
from app.pipelines.dispatch import process_event_pipeline
from app.repositories.integration_repository import get_token_by_integration_id
from app.repositories.raw_event_repository import list_failed_raw_events

logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 3


async def _rebuild_slack_event(db: AsyncSession, raw_event: RawEvent) -> NotificationEvent:
    """raw_events.payload(Slack event 본문)로부터 NotificationEvent를 재조립한다.

    저장 당시 payload는 webhook envelope이 아니라 그 안의 "event" 객체만 보존되므로,
    team_id는 integration.slack_team_id에서 복원하고 나머지는 to_normalizer_payload가
    기대하는 envelope 형태로 감싸서 재사용한다. channel_name/sender_name은 저장돼 있지
    않아 저장된 토큰으로 Slack API를 다시 호출해 조회한다.
    """
    integration = raw_event.integration
    token = await get_token_by_integration_id(db, integration.id)
    access_token = token.access_token if token else None

    envelope = {
        "team_id": integration.slack_team_id,
        "event_id": raw_event.provider_event_id,
        "event_time": None,
        "event": raw_event.payload,
    }
    normalizer_payload = to_normalizer_payload(envelope)

    channel_name: str | None = None
    sender_name: str | None = None
    if access_token:
        client = SlackClient(token=access_token)
        channel_id = normalizer_payload.get("channel_id")
        sender_id = normalizer_payload.get("sender_user_id")
        if channel_id:
            channel_name = await client.get_channel_name(channel_id)
        if sender_id:
            sender_name = await client.get_sender_name(sender_id)

    return normalize_slack_message(
        payload=normalizer_payload,
        integration_id=integration.id,
        raw_event_id=raw_event.id,
        sender_name=sender_name,
        channel_name=channel_name,
    )


async def _rebuild_event(db: AsyncSession, raw_event: RawEvent) -> NotificationEvent | None:
    if raw_event.provider == "discord":
        return normalize_discord_message(
            payload=raw_event.payload,
            integration_id=raw_event.integration_id,
            raw_event_id=raw_event.id,
        )
    if raw_event.provider == "slack":
        return await _rebuild_slack_event(db, raw_event)

    logger.warning(
        "알 수 없는 provider=%s raw_event_id=%s 재처리 스킵", raw_event.provider, raw_event.id
    )
    return None


async def retry_failed_raw_events(limit: int = 20) -> int:
    """실패 상태인 raw_event를 원본 payload로부터 재조립해 파이프라인에 재투입한다 (#13).

    성공/실패 결과는 process_event_pipeline이 다시 raw_event.status에 기록하므로,
    이 함수는 재시도 대상을 고르고 이벤트를 재조립해 넘기는 역할만 한다.
    Redis 메시지 락(acquire_message_lock)은 60초 TTL로 자동 해제되므로, 원본 처리
    실패 이후 락이 남아 재시도를 막는 일은 없다.
    """
    async with AsyncSessionLocal() as db:
        raw_events = await list_failed_raw_events(
            db, limit=limit, max_retries=MAX_RETRY_ATTEMPTS
        )

        events_to_retry: list[tuple[int, NotificationEvent]] = []
        for raw_event in raw_events:
            try:
                event = await _rebuild_event(db, raw_event)
            except Exception:
                logger.exception("raw_event 재조립 실패: raw_event_id=%s", raw_event.id)
                continue
            if event is not None:
                events_to_retry.append((raw_event.id, event))

    for raw_event_id, event in events_to_retry:
        logger.info(
            "실패 raw_event 재처리 시작: raw_event_id=%s provider=%s", raw_event_id, event.provider
        )
        await process_event_pipeline(event, raw_event_id=raw_event_id)

    return len(events_to_retry)
