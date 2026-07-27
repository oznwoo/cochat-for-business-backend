import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.endpoints import briefing, integrations, notifications, streams
from app.ingress.discord_gateway import start_gateway, stop_gateway
from app.ingress.slack_webhook import router as slack_router
from app.core.scheduler import run_gc_scheduler
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.user import User


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
    # 1. 마스터 유저 보장 (FK 위반 방지)
    await _ensure_master_user()

    # 2. 디스코드 게이트웨이 백그라운드 구동
    discord_task = await start_gateway()

    # 3. 벡터 DB 가비지 컬렉터 스케줄러 (태스크 스폰)
    gc_task = asyncio.create_task(run_gc_scheduler(interval_hours=24))
    
    yield
    
    # 3. 우아한 종료(Graceful Shutdown)
    gc_task.cancel()
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


@app.get("/health")
def health_check():
    return {"status": "ok"}
