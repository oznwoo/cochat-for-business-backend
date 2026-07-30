from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.integrations.google_calendar.client import GoogleCalendarClient
from app.integrations.google_calendar.service import get_valid_google_calendar_token

router = APIRouter(tags=["calendar-events"])


async def _require_google_calendar_token(db: AsyncSession, user_id: int):
    token = await get_valid_google_calendar_token(db, user_id)
    if not token:
        raise HTTPException(status_code=404, detail="구글 캘린더 연동이 필요합니다.")
    return token


def _to_google_event_payload(
    *,
    title: str,
    start_at: datetime,
    end_at: datetime | None,
    is_all_day: bool,
    attendees: list[str],
    description: str | None,
    location: str | None,
) -> dict:
    """#46 요청 스키마(start_at/end_at/is_all_day)를 Google Calendar 이벤트 포맷으로 변환."""
    if is_all_day:
        start_date = start_at.date()
        # Google의 종일 일정 end.date는 배타적(exclusive) — 같은 날/미지정이면 다음날로 보정.
        end_date = end_at.date() if end_at else start_date
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        start = {"date": start_date.isoformat()}
        end = {"date": end_date.isoformat()}
    else:
        start = {"dateTime": start_at.isoformat()}
        end = {"dateTime": (end_at or start_at + timedelta(minutes=30)).isoformat()}

    event: dict = {"summary": title, "start": start, "end": end}
    if attendees:
        event["attendees"] = [{"email": email} for email in attendees]
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location
    return event


def _serialize_google_event(event: dict) -> dict:
    start_raw = event.get("start", {})
    end_raw = event.get("end", {})
    is_all_day = "date" in start_raw
    attendees = [a["email"] for a in event.get("attendees", []) if a.get("email")]
    return {
        "id": event.get("id"),
        "title": event.get("summary", ""),
        "start_at": start_raw.get("date") or start_raw.get("dateTime"),
        "end_at": end_raw.get("date") or end_raw.get("dateTime"),
        "is_all_day": is_all_day,
        "attendees": attendees,
        "description": event.get("description"),
        "location": event.get("location"),
        "meeting_link": event.get("hangoutLink"),
        # 알림 등록분과 수동 추가분을 구분 없이 연동된 캘린더 전체를 그대로 보여주기로 해서
        # (#46 설계 확인 시 결정), 이 CRUD는 자체 DB에 알림 연결 정보를 들고 있지 않음.
        "related_notification_ids": [],
    }


class CreateCalendarEventRequest(BaseModel):
    title: str
    start_at: datetime
    end_at: datetime | None = None
    is_all_day: bool = False
    attendees: list[str] = []
    description: str | None = None
    location: str | None = None


class UpdateCalendarEventRequest(BaseModel):
    title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    is_all_day: bool | None = None
    attendees: list[str] | None = None
    description: str | None = None
    location: str | None = None


@router.post("/calendar-events", status_code=201)
async def create_calendar_event(
    body: CreateCalendarEventRequest,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """알림과 무관하게 유저가 직접 만드는 일정 생성. 연동된 Google Calendar에 바로 등록."""
    token = await _require_google_calendar_token(db, user_id)

    payload = _to_google_event_payload(
        title=body.title,
        start_at=body.start_at,
        end_at=body.end_at,
        is_all_day=body.is_all_day,
        attendees=body.attendees,
        description=body.description,
        location=body.location,
    )

    client = GoogleCalendarClient()
    try:
        event = await client.create_event(access_token=token.access_token, event=payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="구글 캘린더 일정 생성에 실패했습니다.") from exc

    return _serialize_google_event(event)


@router.get("/calendar-events")
async def list_calendar_events(
    user_id: int,
    from_: date = Query(alias="from"),
    to: date = Query(),
    db: AsyncSession = Depends(get_db),
):
    """기간별 캘린더 일정 목록 조회. 연동된 Google Calendar 전체를 그대로 반환."""
    token = await _require_google_calendar_token(db, user_id)

    # Google API의 timeMax는 배타적(exclusive)이라 'to' 날짜까지 포함하려면 하루 더해야 함.
    time_min = datetime.combine(from_, datetime.min.time()).isoformat() + "Z"
    time_max = datetime.combine(to + timedelta(days=1), datetime.min.time()).isoformat() + "Z"

    client = GoogleCalendarClient()
    try:
        events = await client.list_events(
            access_token=token.access_token, time_min=time_min, time_max=time_max
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="구글 캘린더 일정 조회에 실패했습니다.") from exc

    return {"events": [_serialize_google_event(e) for e in events], "total": len(events)}


@router.patch("/calendar-events/{event_id}")
async def update_calendar_event(
    event_id: str,
    body: UpdateCalendarEventRequest,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """일정 수정. 시각을 바꾸는 경우 start_at/end_at을 함께 보내야 함."""
    if (body.start_at is None) != (body.end_at is None):
        raise HTTPException(
            status_code=400, detail="start_at과 end_at은 함께 전달해야 합니다."
        )

    token = await _require_google_calendar_token(db, user_id)

    payload: dict = {}
    if body.title is not None:
        payload["summary"] = body.title
    if body.start_at is not None and body.end_at is not None:
        time_payload = _to_google_event_payload(
            title=body.title or "",
            start_at=body.start_at,
            end_at=body.end_at,
            is_all_day=bool(body.is_all_day),
            attendees=[],
            description=None,
            location=None,
        )
        payload["start"] = time_payload["start"]
        payload["end"] = time_payload["end"]
    if body.attendees is not None:
        payload["attendees"] = [{"email": email} for email in body.attendees]
    if body.description is not None:
        payload["description"] = body.description
    if body.location is not None:
        payload["location"] = body.location

    if not payload:
        raise HTTPException(status_code=400, detail="수정할 필드가 없습니다.")

    client = GoogleCalendarClient()
    try:
        event = await client.update_event(
            access_token=token.access_token, event_id=event_id, event=payload
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="구글 캘린더 일정 수정에 실패했습니다.") from exc

    return _serialize_google_event(event)


@router.delete("/calendar-events/{event_id}", status_code=204)
async def delete_calendar_event(
    event_id: str,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """일정 삭제."""
    token = await _require_google_calendar_token(db, user_id)

    client = GoogleCalendarClient()
    try:
        await client.delete_event(access_token=token.access_token, event_id=event_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="구글 캘린더 일정 삭제에 실패했습니다.") from exc
