from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user_id
from app.core.redis_manager import get_redis_client, notification_channel

router = APIRouter(tags=["streams"])

_KEEPALIVE_SECONDS = 15


async def _event_generator(user_id: int, request: Request) -> AsyncGenerator[str, None]:
    """유저의 알림 채널을 구독해 새 Notification을 SSE 이벤트로 전달합니다."""
    client = get_redis_client()
    channel = notification_channel(user_id)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        while True:
            if await request.is_disconnected():
                break
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_KEEPALIVE_SECONDS
            )
            if message is None:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {message['data']}\n\n"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.get("/notifications/stream")
async def notification_stream(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
):
    """SSE 실시간 알림 스트림 — 새 알림 생성 시 이벤트를 푸시합니다."""
    return StreamingResponse(
        _event_generator(current_user_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
