from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.integration_account import IntegrationAccount
from app.models.notification import Notification


def _scoped_to_user(query, user_id: int):
    return query.join(
        IntegrationAccount, Notification.integration_id == IntegrationAccount.id
    ).where(IntegrationAccount.user_id == user_id)


def serialize_notification(n: Notification, *, provider: str | None) -> dict:
    return {
        "id": n.id,
        "integration_id": n.integration_id,
        "provider": provider,
        "title": n.title,
        "sender_name": n.sender_name,
        "channel_name": n.channel_name,
        "source_type": n.source_type,
        "priority": n.priority,
        "original_text": n.original_text,
        "summary": n.summary,
        "reason": n.reason,
        "occurred_at": n.occurred_at.isoformat() if n.occurred_at else None,
        "source_url": n.source_url,
        "is_direct_target": n.is_direct_target,
        "status": n.status,
        "is_schedule_related": n.is_schedule_related,
        "calendar_status": n.calendar_status,
        "calendar_event_id": n.calendar_event_id,
        "calendar_event_url": n.calendar_event_url,
        "suggested_start_time": n.suggested_start_time.isoformat() if n.suggested_start_time else None,
        "suggested_duration_minutes": n.suggested_duration_minutes,
        "calendar_event_start_time": n.calendar_event_start_time.isoformat() if n.calendar_event_start_time else None,
        "calendar_event_end_time": n.calendar_event_end_time.isoformat() if n.calendar_event_end_time else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


async def list_notifications_for_user(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
) -> tuple[list[Notification], int]:
    """유저의 알림 목록을 최신순으로 조회. (알림 목록, 전체 개수) 튜플 반환."""
    query = select(Notification).options(selectinload(Notification.integration))
    query = _scoped_to_user(query, user_id)
    if status:
        query = query.where(Notification.status == status)

    count_query = _scoped_to_user(select(func.count(Notification.id)), user_id)
    if status:
        count_query = count_query.where(Notification.status == status)
    total = (await db.execute(count_query)).scalar_one()

    result = await db.execute(
        query.order_by(desc(Notification.occurred_at).nullslast(), desc(Notification.id))
        .limit(limit)
        .offset(offset)
    )
    notifications = list(result.scalars().all())
    return notifications, total


async def get_notification_by_id_for_user(
    db: AsyncSession, notification_id: int, user_id: int
) -> Notification | None:
    """유저 소유의 알림만 조회 (다른 유저의 알림 접근 차단)."""
    query = _scoped_to_user(
        select(Notification)
        .options(selectinload(Notification.integration))
        .where(Notification.id == notification_id),
        user_id,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def update_notification_status(
    db: AsyncSession, notification: Notification, status: str
) -> Notification:
    notification.status = status
    await db.flush()
    return notification


async def list_calendar_candidates_for_user(
    db: AsyncSession, user_id: int
) -> list[Notification]:
    """일정 등록 후보(prompted/pending) 알림 목록 조회 — 오프라인 중 놓친 것 + 누적된 pending 확인용."""
    query = _scoped_to_user(
        select(Notification)
        .options(selectinload(Notification.integration))
        .where(Notification.calendar_status.in_(["pending", "prompted"])),
        user_id,
    )
    result = await db.execute(
        query.order_by(desc(Notification.occurred_at).nullslast(), desc(Notification.id))
    )
    return list(result.scalars().all())


async def update_notification_calendar(
    db: AsyncSession,
    notification: Notification,
    *,
    status: str,
    event_id: str | None = None,
    event_url: str | None = None,
    event_start_time: datetime | None = None,
    event_end_time: datetime | None = None,
) -> Notification:
    notification.calendar_status = status
    if event_id is not None:
        notification.calendar_event_id = event_id
    if event_url is not None:
        notification.calendar_event_url = event_url
    if event_start_time is not None:
        notification.calendar_event_start_time = event_start_time
    if event_end_time is not None:
        notification.calendar_event_end_time = event_end_time
    await db.flush()
    return notification
