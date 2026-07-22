from __future__ import annotations

import asyncio
import logging

import discord

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.integrations.discord.events import DiscordEventType
from app.integrations.discord.normalizer import normalize_message
from app.pipelines.entry import run_pipeline_with_memory
from app.repositories.integration_repository import get_integration_by_account
from app.repositories.raw_event_repository import save_raw_event

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True  # Privileged Intent — Developer Portal에서 활성화 필수
intents.guilds = True
intents.dm_messages = True

# lifespan에서 참조하기 위한 전역 bot 인스턴스
_bot: CoChatDiscordBot | None = None


class CoChatDiscordBot(discord.Client):
    async def on_ready(self) -> None:
        logger.info("Discord 봇 연결 성공: %s (id: %s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        payload = {
            "id": str(message.id),
            "content": message.content,
            "author": {
                "id": str(message.author.id),
                "username": message.author.name,
                "global_name": getattr(message.author, "global_name", None),
            },
            "channel_id": str(message.channel.id),
            "channel_name": getattr(message.channel, "name", None),
            "guild_id": str(message.guild.id) if message.guild else None,
            "timestamp": message.created_at.isoformat(),
            "attachments": [str(a.url) for a in message.attachments],
            "mentions": [str(u.id) for u in message.mentions],
            "mention_everyone": message.mention_everyone,
            # 스레드 감지용 — Discord에서 스레드는 별도 채널(public_thread 등)로 처리됨
            "channel_type": str(message.channel.type),
        }

        logger.debug("Discord 메시지 수신: %s", payload)

        guild_id: str | None = payload["guild_id"]
        if not guild_id:
            # DM은 integration_account가 없으므로 현재는 스킵
            return

        async with AsyncSessionLocal() as db:
            async with db.begin():
                integration = await get_integration_by_account(
                    db=db,
                    provider="discord",
                    account_identifier=guild_id,
                )
                if not integration:
                    logger.warning("guild_id=%s에 대한 IntegrationAccount가 없습니다. OAuth 연동을 먼저 완료하세요.", guild_id)
                    return

                raw_event = await save_raw_event(
                    db=db,
                    integration_id=integration.id,
                    provider="discord",
                    provider_event_id=payload["id"],
                    event_type=DiscordEventType.MESSAGE_CREATE,
                    payload=payload,
                )

                event = normalize_message(
                    payload=payload,
                    integration_id=integration.id,
                    raw_event_id=raw_event.id,
                )
                logger.info("NotificationEvent 생성: provider=%s integration_id=%s raw_event_id=%s", event.provider, event.integration_id, event.raw_event_id)

        # raw_event 저장 트랜잭션과 분리 — 파이프라인(LLM 호출 등)이 실패해도 raw_event는 유지
        try:
            notification = await run_pipeline_with_memory(event)
        except Exception:
            logger.exception("Discord 파이프라인 처리 실패: raw_event_id=%s", raw_event.id)
            return

        if notification is None:
            return

        async with AsyncSessionLocal() as db:
            async with db.begin():
                db.add(notification)


async def start_gateway() -> asyncio.Task:
    """FastAPI lifespan에서 호출. 봇을 백그라운드 태스크로 시작하고 Task를 반환."""
    global _bot
    token = settings.DISCORD_BOT_TOKEN
    if not token:
        logger.warning("DISCORD_BOT_TOKEN이 설정되지 않아 Discord 봇을 시작하지 않습니다.")
        return None

    _bot = CoChatDiscordBot(intents=intents)
    task = asyncio.create_task(_bot.start(token), name="discord_gateway")
    logger.info("Discord 게이트웨이 백그라운드 태스크 시작")
    return task


async def stop_gateway(task: asyncio.Task | None) -> None:
    """FastAPI lifespan 종료 시 호출. 봇 연결을 닫고 태스크를 정리."""
    global _bot
    if _bot and not _bot.is_closed():
        await _bot.close()
        logger.info("Discord 봇 연결 종료")

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def run_discord_gateway() -> None:
    """Discord Bot을 단독 실행하는 함수 (로컬 테스트용)."""
    token = settings.DISCORD_BOT_TOKEN
    if not token:
        raise ValueError(
            "DISCORD_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    bot = CoChatDiscordBot(intents=intents)
    await bot.start(token)
