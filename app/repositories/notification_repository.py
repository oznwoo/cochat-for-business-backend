from __future__ import annotations

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
