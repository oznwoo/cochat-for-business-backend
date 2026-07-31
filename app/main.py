import asyncio
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.endpoints import briefing, calendar_events, integrations, notifications, streams
from app.ingress.discord_gateway import start_gateway, stop_gateway
from app.ingress.slack_webhook import router as slack_router
from app.core.scheduler import run_gc_scheduler, run_retry_scheduler
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.user import User


def _upgrade_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


async def _run_migrations() -> None:
    """앱 시작 시 마이그레이션을 자동 실행합니다 (#50).

    Render 무료 플랜은 Pre-Deploy Command를 지원하지 않아, 배포 후 스키마가
    코드보다 뒤처지는 사고(#7 UndefinedColumnError)가 있었음. 처음엔
    subprocess로 `alembic upgrade head`를 실행했는데, 이 앱은 이미 512MB
    메모리 제약이 빠듯해서 무거운 의존성(langchain/langgraph 등)을 통째로
    재import하는 자식 프로세스를 띄우면 OOM으로 배포 자체가 크래시했다 (#52).
    같은 프로세스 안에서 alembic Python API를 직접 호출해 재import 비용을
    없앰. `command.upgrade`가 내부적으로 asyncio.run()을 호출해 이미 실행
    중인 이벤트 루프와 충돌하므로 to_thread로 별도 스레드에서 실행한다.
    실패 시 앱 시작 자체를 막아 스키마가 안 맞는 상태로 뜨는 것을 방지한다.
    """
    await asyncio.to_thread(_upgrade_to_head)


async def _ensure_master_user() -> None:
    """MASTER_USER_ID에 해당하는 유저가 없으면 플레이스홀더 유저를 생성합니다."""
    if settings.MASTER_USER_ID <= 0:
        return
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                select(User).where(User.id == settings.MASTER_USER_ID)
            )
            if result.scalar_one_or_none() is None:
                session.add(User(
                    id=settings.MASTER_USER_ID,
                    email="master@cochat.local",
                    password_hash="",
                    display_name="Master",
                ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. DB 마이그레이션 자동 적용 (#50, Render 무료 플랜은 Pre-Deploy Command 미지원)
    await _run_migrations()

    # 2. 마스터 유저 보장 (FK 위반 방지)
    await _ensure_master_user()

    # 3. 디스코드 게이트웨이 백그라운드 구동
    discord_task = await start_gateway()

    # 4. 벡터 DB 가비지 컬렉터 스케줄러 (태스크 스폰)
    gc_task = asyncio.create_task(run_gc_scheduler(interval_hours=24))

    # 5. 실패한 raw_event 재처리 스케줄러 (태스크 스폰) (#13)
    retry_task = asyncio.create_task(run_retry_scheduler(interval_minutes=10))

    yield

    # 6. 우아한 종료(Graceful Shutdown)
    gc_task.cancel()
    retry_task.cancel()
    await stop_gateway(discord_task)


app = FastAPI(
    title="CoChat API",
    description="여러 업무 채널의 알림을 통합하고 AI로 요약해 주는 CoChat 서비스의 백엔드 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(integrations.router, prefix="/api/v1")
app.include_router(slack_router, prefix="/api/v1")
# streams는 리터럴 경로(/notifications/stream)를 선언하므로, notifications의
# 파라미터 경로(/notifications/{notification_id})보다 먼저 등록해야 매칭이 가로채이지 않는다 (#35).
app.include_router(streams.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(briefing.router, prefix="/api/v1")
app.include_router(calendar_events.router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok"}
