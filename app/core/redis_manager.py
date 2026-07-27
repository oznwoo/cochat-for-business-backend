import json
import redis.asyncio as redis
from typing import List
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.notification import Notification

_redis_client = None

def get_redis_client():
    """싱글톤 Redis Async 클라이언트 반환"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client

def notification_channel(user_id: int) -> str:
    """유저별 실시간 알림 SSE pub/sub 채널 키."""
    return f"notifications:user:{user_id}"

async def publish_notification(user_id: int, payload: dict) -> None:
    """새 알림을 유저 채널에 발행해 SSE 구독자에게 실시간 전달합니다."""
    client = get_redis_client()
    await client.publish(notification_channel(user_id), json.dumps(payload))

async def acquire_message_lock(message_id: str, timeout_seconds: int = 60) -> bool:
    """
    동일한 메시지(provider_object_id)에 대해 중복 처리를 방어하는 분산 락을 획득합니다.
    성공 시 True, 이미 처리중이거나 처리완료 상태라면 False를 반환합니다.
    """
    if not message_id:
        return True # ID가 없는 예외 케이스는 그냥 통과
        
    client = get_redis_client()
    lock_key = f"lock:msg:{message_id}"
    
    # nx=True 는 "이 키가 없을 때만 SET 한다"는 뜻으로, 완벽한 원자적(Atomic) Lock 기능을 수행합니다.
    # timeout_seconds를 주동적으로 걸어두어, 파이프라인 중단 시 영원히 데드락에 빠지는 것을 방지합니다.
    is_acquired = await client.set(lock_key, "processing", nx=True, ex=timeout_seconds)
    return bool(is_acquired)

async def mark_message_as_processed(message_id: str) -> None:
    """
    파이프라인이 정상적으로 끝난 메시지는 락을 해제하는 대신 '완료' 상태로 덮어씌우고
    아주 긴 만료시간(예: 1일)을 부여해 완전한 멱등성(Idempotency)을 보장합니다.
    """
    if not message_id:
        return
        
    client = get_redis_client()
    lock_key = f"lock:msg:{message_id}"
    await client.set(lock_key, "done", ex=86400) # 24시간 동안 재진입 철통 방어

async def add_to_short_term_memory(channel_id: str, message: str, limit: int = 50) -> None:
    """
    특정 채널의 Redis 단기 기억 버퍼에 새 메시지를 추가합니다.
    최근 50개(limit)의 맥락을 상시 유지합니다.
    """
    if not channel_id:
        return
        
    client = get_redis_client()
    key = f"chats_history:{channel_id}"
    
    # LPUSH로 맨 앞에 밀어넣음 (최신이 인덱스 0)
    await client.lpush(key, message)
    # limit 개수만큼만 보존하고 가장 오래된 메시지들은 쳐냄 (무한 증식 방지)
    await client.ltrim(key, 0, limit - 1)

async def fetch_short_term_memory(channel_id: str) -> List[str]:
    """
    Redis에서 채널의 최근 메시지 배열을 가져옵니다.
    반환될 때에는 오래된 메시지부터 차례대로(시간순) 배열되독 Reverse 처리하여 리턴합니다.
    (LLM이 문맥을 위에서 아래로 순서대로 읽기 좋게 만들기 위함)
    """
    if not channel_id:
        return []
        
    client = get_redis_client()
    key = f"chats_history:{channel_id}"
    
    # 0부터 -1까지 조회하면 리스트 전체 획득 (LPUSH라 0이 가장 최신)
    messages = await client.lrange(key, 0, -1)
    
    # Cache Miss (Redis에 데이터가 없거나 증발함) -> RDB 웜업(Warm-up)
    if not messages:
        print(f"🔄 [Cache Miss] Redis에 채널({channel_id}) 문맥이 없어 RDB에서 복구합니다...")
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Notification)
                .where(Notification.channel_id == channel_id)
                .order_by(Notification.occurred_at.desc())
                .limit(50)
            )
            result = await session.execute(stmt)
            notifications = result.scalars().all()
            
            if notifications:
                # DB의 결과는 최신순(occurred_at.desc()).
                # Redis의 구조는 인덱스 0이 가장 최신, 그 뒤로 과거 데이터가 이어지는 구조(LPUSH).
                # RPUSH에 최신부터 순서대로(notifications) 밀어넣으면, 인덱스 0이 알맞게 최신 메시지가 됨.
                messages = []
                for n in notifications:
                    body = n.rich_contents if n.rich_contents else (n.original_text or "")
                    messages.append(f"[{n.sender_name or 'Unknown'}]: {body}")
                
                await client.rpush(key, *messages)
                print(f"✅ RDB에서 {len(messages)}개의 메시지를 Redis로 복원 완료!")
            else:
                print("⚠️ RDB에도 과거 내역이 없는 완전한 신규/빈 채널입니다.")
                return []

    # 시간 순서대로 (오래된 것 -> 최신 것) 재배열하여 LLM에 전달하기 위해 Reverse
    return messages[::-1]
