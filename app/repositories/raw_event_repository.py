from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.raw_event import RawEvent


async def save_raw_event(
    db: AsyncSession,
    integration_id: int,
    provider: str,
    provider_event_id: str,
    event_type: str,
    payload: dict,
) -> RawEvent:
    """원본 이벤트를 저장하되, 동일 provider_event_id는 재사용한다."""
    existing = await db.execute(
        select(RawEvent).where(
            RawEvent.provider == provider,
            RawEvent.integration_id == integration_id,
            RawEvent.provider_event_id == provider_event_id,
        )
    )
    raw_event = existing.scalar_one_or_none()
    if raw_event is not None:
        return raw_event

    raw_event = RawEvent(
        provider=provider,
        integration_id=integration_id,
        provider_event_id=provider_event_id,
        event_type=event_type,
        payload=payload,
    )
    db.add(raw_event)
    await db.flush()
    return raw_event


async def mark_raw_event_status(
    db: AsyncSession,
    raw_event_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """raw_event 처리 결과(completed/failed)를 기록한다 (#13).

    failed로 기록될 때는 retry_count를 함께 증가시켜, 재처리 스케줄러가
    영구 실패 건을 무한 재시도하지 않고 상한(MAX_RETRY_ATTEMPTS) 이후 포기하게 한다.
    """
    values: dict = {
        "status": status,
        "error_message": error_message,
        "processed_at": datetime.now(timezone.utc),
    }
    if status == "failed":
        values["retry_count"] = RawEvent.retry_count + 1

    await db.execute(
        update(RawEvent).where(RawEvent.id == raw_event_id).values(**values)
    )
    await db.commit()


async def list_failed_raw_events(
    db: AsyncSession,
    limit: int = 20,
    max_retries: int = 3,
) -> list[RawEvent]:
    """재시도 상한 미만의 failed raw_event를 오래된 순으로 조회한다 (#13)."""
    stmt = (
        select(RawEvent)
        .options(selectinload(RawEvent.integration))
        .where(RawEvent.status == "failed", RawEvent.retry_count < max_retries)
        .order_by(RawEvent.received_at.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_raw_events_by_integration_ids(
    db: AsyncSession,
    integration_ids: list[int],
    provider: str | None = None,
    limit: int = 100,
) -> list[RawEvent]:
    """List recent raw events across multiple integrations."""
    if not integration_ids:
        return []

    stmt = (
        select(RawEvent)
        .options(selectinload(RawEvent.integration))
        .where(RawEvent.integration_id.in_(integration_ids))
        .order_by(RawEvent.received_at.desc(), RawEvent.id.desc())
        .limit(limit)
    )

    if provider is not None:
        stmt = stmt.where(RawEvent.provider == provider)

    result = await db.execute(stmt)
    return list(result.scalars().all())
