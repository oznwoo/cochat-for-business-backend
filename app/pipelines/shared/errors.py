from __future__ import annotations

from typing import Iterator

# rate limit로 판단할 예외 클래스명 (groq SDK는 groq.RateLimitError, 다른 래핑도 대비).
_RATE_LIMIT_EXC_NAMES = {"RateLimitError", "TooManyRequests"}


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """예외와 그 __cause__/__context__ 체인을 순회한다.

    langchain_groq가 groq.RateLimitError를 다른 예외로 감싸 던지는 경우에도
    원인 예외를 놓치지 않기 위함.
    """
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.append(current.__cause__)
        stack.append(current.__context__)


def is_rate_limit_error(exc: BaseException) -> bool:
    """LLM 호출이 rate limit(HTTP 429)로 실패했는지 판별한다 (#55).

    Groq 무료 티어 한도 초과 시 groq SDK가 429와 함께 RateLimitError를 던지고
    langchain_groq가 이를 그대로 전파한다. 모델 폐기(404)·스키마 거부(400)·타임아웃
    등 다른 실패와 구분해, rate limit인 경우에만 '분석 없이 저장' 경로로 보낸다.
    """
    for current in _iter_exception_chain(exc):
        if type(current).__name__ in _RATE_LIMIT_EXC_NAMES:
            return True
        status = getattr(current, "status_code", None) or getattr(current, "code", None)
        if status == 429 or status == "429":
            return True
    return False
