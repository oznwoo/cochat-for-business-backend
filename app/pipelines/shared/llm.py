from functools import lru_cache

from langchain_groq import ChatGroq

from app.core.config import settings


@lru_cache(maxsize=None)
def get_chat_llm(temperature: float = 0) -> ChatGroq:
    """파이프라인 전역에서 쓰는 채팅 LLM. Gemini 무료 티어 일일 20건 한도(#17류 재발
    방지)로 Groq로 전환 — 무료 한도가 분당 기준이라 실사용에 넉넉함.

    temperature별로 인스턴스를 캐싱해 호출마다 새 HTTP 클라이언트/커넥션 풀이
    생기는 것을 막는다 (512MB 인스턴스 메모리 여유 확보, #29 계열)."""
    return ChatGroq(
        model=settings.GROQ_MODEL_NAME,
        temperature=temperature,
        api_key=settings.GROQ_API_KEY,
    )
