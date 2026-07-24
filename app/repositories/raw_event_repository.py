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
    """raw_event 처리 결과(completed/failed)를 기록한다 (#13)."""
    await db.execute(
        update(RawEvent)
        .where(RawEvent.id == raw_event_id)
        .values(
            status=status,
            error_message=error_message,
            processed_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


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
