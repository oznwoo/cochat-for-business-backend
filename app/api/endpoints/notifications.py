from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.db.session import get_db
from app.repositories.notification_repository import (
    get_notification_by_id_for_user,
    list_notifications_for_user,
    update_notification_status,
)

router = APIRouter(tags=["notifications"])


def _serialize_notification(n) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "sender_name": n.sender_name,
        "channel_name": n.channel_name,
        "source_type": n.source_type,
        "priority": n.priority,
        "original_text": n.original_text,
        "summary": n.summary,
        "reason": n.reason,
        "occurred_at": n.occurred_at,
        "source_url": n.source_url,
        "is_direct_target": n.is_direct_target,
        "status": n.status,
        "created_at": n.created_at,
    }


@router.get("/notifications")
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: Literal["unread", "read"] | None = Query(default=None),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """알림 목록 조회 (최신순)."""
    notifications, total = await list_notifications_for_user(
        db=db,
        user_id=current_user_id,
        limit=limit,
        offset=offset,
        status=status,
    )
    return {
        "notifications": [_serialize_notification(n) for n in notifications],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/notifications/{notification_id}")
async def get_notification(
    notification_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """단일 알림 조회."""
    notification = await get_notification_by_id_for_user(
        db=db, notification_id=notification_id, user_id=current_user_id
    )
    if not notification:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    return _serialize_notification(notification)


class UpdateNotificationRequest(BaseModel):
    status: Literal["unread", "read"]


@router.patch("/notifications/{notification_id}")
async def update_notification(
    notification_id: int,
    body: UpdateNotificationRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """알림 상태 업데이트 (읽음 처리 등)."""
    async with db.begin():
        notification = await get_notification_by_id_for_user(
            db=db, notification_id=notification_id, user_id=current_user_id
        )
        if not notification:
            raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")

        notification = await update_notification_status(db, notification, body.status)

    return _serialize_notification(notification)
