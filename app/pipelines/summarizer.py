from __future__ import annotations

import asyncio

from google import genai

from app.core.config import settings
from app.models.notification import Notification

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    return _client


def _build_prompt(notifications: list[Notification]) -> str:
    lines = ["다음은 집중 세션 중 수신된 알림 목록입니다. 중요도 순으로 간결하게 요약해 주세요.\n"]

    for n in notifications:
        priority = f"[{n.priority.upper()}]" if n.priority else "[미분류]"
        sender = n.sender_name or "Unknown"
        location = f"#{n.channel_name}" if n.channel_name else "DM"
        source = f" ({n.source_type})" if n.source_type else ""
        text = n.original_text or "(첨부파일)"
        lines.append(f"- {priority} {sender} / {location}{source}: {text}")

    lines.append("\n요약 형식:")
    lines.append("1. 긴급하게 확인해야 할 메시지 (있을 경우)")
    lines.append("2. 주요 내용 요약")
    lines.append("3. 나중에 확인해도 되는 내용 (있을 경우)")

    return "\n".join(lines)


async def generate_briefing(notifications: list[Notification]) -> str:
    """알림 목록을 받아 Gemini로 브리핑 텍스트를 생성."""
    if not notifications:
        return "집중 세션 중 수신된 알림이 없습니다."

    prompt = _build_prompt(notifications)
    client = _get_client()

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.GEMINI_MODEL_NAME,
        contents=prompt,
    )

    return response.text
