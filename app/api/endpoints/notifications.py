from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.db.session import get_db
from app.integrations.google_calendar.client import GoogleCalendarClient
from app.integrations.google_calendar.service import get_valid_google_calendar_token
from app.repositories.notification_repository import (
    get_notification_by_id_for_user,
    list_calendar_candidates_for_user,
    list_notifications_for_user,
    serialize_notification,
    update_notification_calendar,
    update_notification_status,
)

router = APIRouter(tags=["notifications"])


def _serialize_notification(n) -> dict:
    return serialize_notification(n, provider=n.integration.provider if n.integration else None)


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


@router.get("/notifications/calendar-candidates")
async def list_calendar_candidates(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """일정 등록 후보(prompted/pending) 알림 목록 — 오프라인 중 놓친 것 + 누적된 pending 확인용."""
    notifications = await list_calendar_candidates_for_user(db, current_user_id)
    return {
        "notifications": [_serialize_notification(n) for n in notifications],
        "total": len(notifications),
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


class CreateCalendarEventRequest(BaseModel):
    start_time: datetime | None = None
    duration_minutes: int = Field(default=30, gt=0)


@router.post("/notifications/{notification_id}/calendar-event", status_code=201)
async def create_calendar_event(
    notification_id: int,
    body: CreateCalendarEventRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """알림을 구글 캘린더 일정으로 등록. is_schedule_related인 알림만 허용."""
    async with db.begin():
        notification = await get_notification_by_id_for_user(
            db=db, notification_id=notification_id, user_id=current_user_id
        )
        if not notification:
            raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
        if not notification.is_schedule_related:
            raise HTTPException(status_code=400, detail="일정 관련 알림이 아닙니다.")
        if notification.calendar_status == "registered":
            raise HTTPException(status_code=409, detail="이미 캘린더에 등록된 알림입니다.")

        token = await get_valid_google_calendar_token(db, current_user_id)
        if not token:
            raise HTTPException(status_code=404, detail="구글 캘린더 연동이 필요합니다.")

        start_time = body.start_time or notification.occurred_at
        if not start_time:
            raise HTTPException(
                status_code=400, detail="일정 시각을 확인할 수 없습니다. start_time을 직접 지정하세요."
            )
        end_time = start_time + timedelta(minutes=body.duration_minutes)

        client = GoogleCalendarClient()
        try:
            event = await client.create_event(
                access_token=token.access_token,
                event={
                    "summary": notification.title,
                    "description": notification.summary or notification.original_text or "",
                    "start": {"dateTime": start_time.isoformat()},
                    "end": {"dateTime": end_time.isoformat()},
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail="구글 캘린더 일정 생성에 실패했습니다.") from exc

        notification = await update_notification_calendar(
            db,
            notification,
            status="registered",
            event_id=event.get("id"),
            event_url=event.get("htmlLink"),
            event_start_time=start_time,
            event_end_time=end_time,
        )

    return _serialize_notification(notification)


@router.post("/notifications/{notification_id}/calendar-event/dismiss")
async def dismiss_calendar_event(
    notification_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """캘린더 등록 제안 거절 처리 (외부 API 호출 없음)."""
    async with db.begin():
        notification = await get_notification_by_id_for_user(
            db=db, notification_id=notification_id, user_id=current_user_id
        )
        if not notification:
            raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")

        notification = await update_notification_calendar(db, notification, status="dismissed")

    return _serialize_notification(notification)
