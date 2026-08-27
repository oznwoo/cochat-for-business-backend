"""#55 — Groq rate limit(429) 시 메시지 유실 방지 동작 검증.

pytest 없이 단독 실행: `python test_rate_limit.py`
외부 API 키 불필요 — LLM 그래프와 Redis를 전부 모킹한다.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import groq

from app.pipelines.shared.errors import is_rate_limit_error


def test_is_rate_limit_error():
    rate = groq.RateLimitError.__new__(groq.RateLimitError)
    Exception.__init__(rate, "Error code: 429")
    assert is_rate_limit_error(rate) is True

    not_found = groq.NotFoundError.__new__(groq.NotFoundError)
    Exception.__init__(not_found, "Error code: 404 - model does not exist")
    assert is_rate_limit_error(not_found) is False

    assert is_rate_limit_error(ValueError("boom")) is False

    # __cause__ 체인으로 감싸진 경우
    try:
        try:
            raise rate
        except Exception as inner:
            raise RuntimeError("wrapped by langchain") from inner
    except Exception as outer:
        assert is_rate_limit_error(outer) is True

    print("✅ is_rate_limit_error: OK")


def _fake_event():
    from app.integrations.normalizer import NotificationEvent

    return NotificationEvent(
        provider="discord",
        integration_id=1,
        raw_event_id=1,
        original_text="🚨 긴급 배포 롤백 필요합니다. 지금 확인해주세요.",
        payload={},
        source_type="channel_message",
        provider_object_id="msg-rl-1",
        title="배포 채널",
        sender_name="배포봇",
        channel_name="deploy",
        channel_id="C-DEPLOY",
        source_url="http://example.com/msg",
        occurred_at=datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc),
    )


async def _run_degraded_case():
    event = _fake_event()
    rate = groq.RateLimitError.__new__(groq.RateLimitError)
    Exception.__init__(rate, "Error code: 429 - rate limit exceeded")

    with patch("app.pipelines.entry.realtime_graph") as graph, \
         patch("app.pipelines.entry.acquire_message_lock", new=AsyncMock(return_value=True)), \
         patch("app.pipelines.entry.fetch_short_term_memory", new=AsyncMock(return_value=[])), \
         patch("app.pipelines.entry.add_to_short_term_memory", new=AsyncMock()) as add_mem, \
         patch("app.pipelines.entry.mark_message_as_processed", new=AsyncMock()) as mark_done:
        graph.ainvoke = AsyncMock(side_effect=rate)

        from app.pipelines.entry import run_pipeline_with_memory

        notification = await run_pipeline_with_memory(event)

    assert notification is not None, "rate limit인데 None 반환 — 메시지 유실"
    assert notification.analysis_status == "rate_limited"
    assert notification.summary is None
    assert notification.priority is None
    assert notification.original_text == event.original_text
    assert notification.calendar_status == "none"
    assert notification.reason and "토큰" in notification.reason
    add_mem.assert_awaited_once()      # 단기기억엔 여전히 적재
    mark_done.assert_awaited_once()    # 재진입 방지 마킹
    print("✅ run_pipeline_with_memory (429 degraded): OK")


async def _run_non_rate_limit_still_raises():
    event = _fake_event()
    boom = groq.NotFoundError.__new__(groq.NotFoundError)
    Exception.__init__(boom, "Error code: 404 - model does not exist")

    with patch("app.pipelines.entry.realtime_graph") as graph, \
         patch("app.pipelines.entry.acquire_message_lock", new=AsyncMock(return_value=True)), \
         patch("app.pipelines.entry.fetch_short_term_memory", new=AsyncMock(return_value=[])), \
         patch("app.pipelines.entry.add_to_short_term_memory", new=AsyncMock()), \
         patch("app.pipelines.entry.mark_message_as_processed", new=AsyncMock()):
        graph.ainvoke = AsyncMock(side_effect=boom)

        from app.pipelines.entry import run_pipeline_with_memory

        try:
            await run_pipeline_with_memory(event)
        except groq.NotFoundError:
            print("✅ run_pipeline_with_memory (비 rate-limit 예외는 그대로 전파): OK")
            return
    raise AssertionError("404인데 예외가 삼켜짐 — 재처리 스케줄러가 못 잡음")


if __name__ == "__main__":
    test_is_rate_limit_error()
    asyncio.run(_run_degraded_case())
    asyncio.run(_run_non_rate_limit_still_raises())
    print("\n🎉 전체 통과 (#55)")
