from langchain_groq import ChatGroq

from app.core.config import settings


def get_chat_llm(temperature: float = 0) -> ChatGroq:
    """파이프라인 전역에서 쓰는 채팅 LLM. Gemini 무료 티어 일일 20건 한도(#17류 재발
    방지)로 Groq로 전환 — 무료 한도가 분당 기준이라 실사용에 넉넉함."""
    return ChatGroq(
        model=settings.GROQ_MODEL_NAME,
        temperature=temperature,
        api_key=settings.GROQ_API_KEY,
    )
