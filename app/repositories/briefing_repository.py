from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.briefing import Briefing
from app.models.briefing_notification import BriefingNotification
from app.models.focus_session import FocusSession
from app.models.notification import Notification


# ── FocusSession ──────────────────────────────────────────────────────────────

async def create_focus_session(
    db: AsyncSession,
    user_id: int,
    planned_duration_minutes: int,
) -> FocusSession:
    session = FocusSession(
        user_id=user_id,
        planned_duration_minutes=planned_duration_minutes,
        started_at=datetime.now(timezone.utc),
        status="active",
    )
    db.add(session)
    await db.flush()
    return session


async def get_focus_session(db: AsyncSession, session_id: int) -> FocusSession | None:
    result = await db.execute(
        select(FocusSession).where(FocusSession.id == session_id)
    )
    return result.scalar_one_or_none()


async def get_active_session(db: AsyncSession, user_id: int) -> FocusSession | None:
    """유저의 현재 진행 중인 세션 조회.

    계획된 시간(planned_duration_minutes)을 이미 넘겼는데도 종료되지 않은 세션은
    프론트 이탈/크래시 등으로 방치된 것으로 간주해 자동으로 만료 처리한다 —
    그렇지 않으면 해당 유저는 새 집중 세션을 영원히 시작할 수 없게 된다 (#38).
    """
    result = await db.execute(
        select(FocusSession).where(
            FocusSession.user_id == user_id,
            FocusSession.status == "active",
        ).order_by(desc(FocusSession.started_at)).limit(1)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return None

    elapsed = datetime.now(timezone.utc) - session.started_at
    if elapsed > timedelta(minutes=session.planned_duration_minutes):
        session.status = "expired"
        session.ended_at = datetime.now(timezone.utc)
        await db.flush()
        return None

    return session


async def end_focus_session(db: AsyncSession, session: FocusSession) -> FocusSession:
    session.ended_at = datetime.now(timezone.utc)
    session.status = "completed"
    await db.flush()
    return session


# ── Notifications ─────────────────────────────────────────────────────────────

PRIORITY_RANK = {"Emergency": 0, "High": 1, "Normal": 2, "Low": 3}


async def get_notifications_for_session(
    db: AsyncSession,
    session: FocusSession,
) -> list[Notification]:
    """세션 시작~종료 사이 수신된 알림 조회. 우선순위 높은 순 정렬."""
    ended_at = session.ended_at or datetime.now(timezone.utc)

    result = await db.execute(
        select(Notification)
        .options(selectinload(Notification.integration))
        .where(
            Notification.occurred_at >= session.started_at,
            Notification.occurred_at <= ended_at,
        ).order_by(Notification.occurred_at.asc())
    )
    notifications = list(result.scalars().all())

    # 우선순위(Emergency > High > Normal > Low) → 발생시각 순 정렬
    notifications.sort(key=lambda n: (PRIORITY_RANK.get(n.priority, 4), n.occurred_at))
    return notifications


# ── Briefing ──────────────────────────────────────────────────────────────────

async def save_briefing(
    db: AsyncSession,
    session_id: int,
    content: str,
    notification_ids: list[int],
    action_items: list[str] | None = None,
) -> Briefing:
    briefing = Briefing(session_id=session_id, content=content, action_items=action_items or [])
    db.add(briefing)
    await db.flush()

    for nid in notification_ids:
        db.add(BriefingNotification(briefing_id=briefing.id, notification_id=nid))

    await db.flush()
    return briefing


async def get_latest_briefing(db: AsyncSession, user_id: int) -> Briefing | None:
    """유저의 가장 최근 브리핑 조회."""
    result = await db.execute(
        select(Briefing)
        .join(FocusSession, Briefing.session_id == FocusSession.id)
        .where(FocusSession.user_id == user_id)
        .options(selectinload(Briefing.notifications).selectinload(Notification.integration))
        .order_by(desc(Briefing.generated_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_briefing_by_id(db: AsyncSession, briefing_id: int) -> Briefing | None:
    result = await db.execute(
        select(Briefing)
        .where(Briefing.id == briefing_id)
        .options(selectinload(Briefing.notifications).selectinload(Notification.integration))
    )
    return result.scalar_one_or_none()
