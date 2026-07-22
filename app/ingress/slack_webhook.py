from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.integrations.slack.client import SlackClient
from app.integrations.slack.events import SlackEventType, to_normalizer_payload
from app.integrations.slack.normalizer import normalize_message
from app.pipelines.dispatch import process_event_pipeline
from app.repositories.integration_repository import (
    get_integration_by_slack_team_id,
    get_token_by_integration_id,
)
from app.repositories.raw_event_repository import save_raw_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])


async def _verify_slack_signature(request: Request) -> bytes:
    """Verify Slack Events API HMAC-SHA256 signature."""
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    body = await request.body()

    if not timestamp or not signature:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Slack signature headers")

    try:
        if abs(time.time() - int(timestamp)) > 300:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request timestamp too old")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid timestamp") from exc

    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        settings.SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Slack signature")

    return body


async def _load_metadata_names(
    access_token: str | None,
    channel_id: str | None,
    user_id: str | None,
) -> tuple[str | None, str | None]:
    """Load Slack display metadata using the stored installation token."""
    if not access_token:
        return None, None

    client = SlackClient(token=access_token)
    channel_name = await client.get_channel_name(channel_id) if channel_id else None
    sender_name = await client.get_sender_name(user_id) if user_id else None
    return channel_name, sender_name


@router.post("/webhooks/slack")
async def slack_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Slack Events API payloads and persist raw events."""
    body = await _verify_slack_signature(request)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc

    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    event: dict = payload.get("event", {})
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return {"ok": True}

    event_type_raw: str = event.get("type", "")
    if event_type_raw not in (SlackEventType.MESSAGE, SlackEventType.APP_MENTION):
        return {"ok": True}

    team_id: str | None = payload.get("team_id") or event.get("team")
    if not team_id:
        logger.warning("Slack event received without team_id; skipping.")
        return {"ok": True}

    channel_id: str | None = event.get("channel")
    user_id: str | None = event.get("user")
    provider_event_id: str = payload.get("event_id") or event.get("ts", "")

    async with AsyncSessionLocal() as db:
        async with db.begin():
            integration = await get_integration_by_slack_team_id(
                db=db,
                team_id=team_id,
            )
            if not integration:
                logger.warning(
                    "Slack integration not found for team_id=%s. Complete OAuth installation first.",
                    team_id,
                )
                return {"ok": True}

            token = await get_token_by_integration_id(db, integration.id)
            raw_event = await save_raw_event(
                db=db,
                integration_id=integration.id,
                provider="slack",
                provider_event_id=provider_event_id,
                event_type=event_type_raw,
                payload=event,
            )
            integration_id = integration.id
            raw_event_id = raw_event.id

        access_token = token.access_token if token else None

    channel_name, sender_name = await _load_metadata_names(
        access_token=access_token,
        channel_id=channel_id,
        user_id=user_id,
    )

    notification_event = normalize_message(
        payload=to_normalizer_payload(payload),
        integration_id=integration_id,
        raw_event_id=raw_event_id,
        channel_name=channel_name,
        sender_name=sender_name,
    )

    logger.info(
        "NotificationEvent created: provider=%s integration_id=%s raw_event_id=%s source_type=%s",
        notification_event.provider,
        notification_event.integration_id,
        notification_event.raw_event_id,
        notification_event.source_type,
    )

    # Slack Events API는 3초 안에 ack해야 하므로 파이프라인(LLM 호출 등)은
    # 백그라운드로 넘기고 즉시 응답한다 — 안 그러면 타임아웃 시 Slack이 같은
    # 이벤트를 재전송해 중복 처리를 유발할 수 있다.
    background_tasks.add_task(
        process_event_pipeline, notification_event, raw_event_id=raw_event_id
    )

    return {"ok": True}
