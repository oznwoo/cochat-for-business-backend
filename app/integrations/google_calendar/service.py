from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.google_calendar.client import GoogleCalendarClient
from app.repositories.integration_repository import (
    get_token_by_integration_id,
    list_integrations_by_user,
    upsert_token,
)


async def get_valid_google_calendar_token(db: AsyncSession, user_id: int):
    """유저의 활성 google_calendar 연동 토큰을 반환. 만료 임박이면 리프레시 후 반환. 연동 없으면 None."""
    integrations = await list_integrations_by_user(
        db=db, user_id=user_id, provider="google_calendar", status="active"
    )
    if not integrations:
        return None

    token = await get_token_by_integration_id(db, integrations[0].id)
    if not token or not token.access_token:
        return None

    buffer = timedelta(seconds=60)
    if token.expires_at and token.expires_at < datetime.now(timezone.utc) + buffer:
        client = GoogleCalendarClient()
        try:
            refreshed = await client.refresh_access_token(
                refresh_token=token.refresh_token,
                client_id=settings.GOOGLE_CALENDAR_CLIENT_ID,
                client_secret=settings.GOOGLE_CALENDAR_CLIENT_SECRET,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="구글 캘린더 토큰 갱신에 실패했습니다. 재연동이 필요합니다."
            ) from exc

        expires_in = refreshed.get("expires_in")
        new_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
        )
        # Google의 리프레시 응답엔 보통 refresh_token이 없다 — 기존에 저장된 값을 그대로 유지해서
        # upsert_token에 넘기지 않으면 다음 리프레시가 영구적으로 깨진다.
        token = await upsert_token(
            db=db,
            integration_id=integrations[0].id,
            access_token=refreshed["access_token"],
            refresh_token=token.refresh_token,
            expires_at=new_expires_at,
        )

    return token
