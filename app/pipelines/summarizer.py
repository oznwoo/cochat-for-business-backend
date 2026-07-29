from __future__ import annotations

from pydantic import BaseModel

from app.models.notification import Notification
from app.pipelines.shared.llm import get_chat_llm


class BriefingResult(BaseModel):
    content: str
    action_items: list[str]


def _build_prompt(notifications: list[Notification]) -> str:
    lines = ["다음은 집중 세션 중 수신된 알림 목록입니다. 중요도 순으로 간결하게 요약해 주세요.\n"]

    for n in notifications:
        priority = f"[{n.priority.upper()}]" if n.priority else "[미분류]"
        sender = n.sender_name or "Unknown"
        location = f"#{n.channel_name}" if n.channel_name else "DM"
        source = f" ({n.source_type})" if n.source_type else ""
        text = n.original_text or "(첨부파일)"
        lines.append(f"- {priority} {sender} / {location}{source}: {text}")

    lines.append("\ncontent는 다음 형식으로 작성하세요:")
    lines.append("1. 긴급하게 확인해야 할 메시지 (있을 경우)")
    lines.append("2. 주요 내용 요약")
    lines.append("3. 나중에 확인해도 되는 내용 (있을 경우)")
    lines.append(
        "\naction_items에는 위 알림들을 바탕으로 지금 바로 실행할 수 있는 구체적인 "
        "권장 조치를 짧은 문장으로 나열하세요. 실행할 게 없으면 빈 배열로 두세요."
    )

    return "\n".join(lines)


async def generate_briefing(notifications: list[Notification]) -> BriefingResult:
    """알림 목록을 받아 Groq로 브리핑(content + action_items)을 생성."""
    if not notifications:
        return BriefingResult(content="집중 세션 중 수신된 알림이 없습니다.", action_items=[])

    prompt = _build_prompt(notifications)
    llm = get_chat_llm(temperature=0)
    structured_llm = llm.with_structured_output(BriefingResult)

    return await structured_llm.ainvoke(prompt)
