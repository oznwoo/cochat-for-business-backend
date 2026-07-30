from typing import Dict, Any

from app.integrations.normalizer import NotificationEvent
from app.models.notification import Notification
from app.pipelines.state import MessageState
from app.pipelines.realtime_graph import realtime_graph
from app.core.redis_manager import (
    fetch_short_term_memory, add_to_short_term_memory,
    acquire_message_lock, mark_message_as_processed
)

async def run_pipeline_with_memory(event: NotificationEvent) -> Notification | None:
    """
    웹훅으로부터 발생한 NotificationEvent를 받아 
    단기기억(Redis) 컨텍스트를 주입한 뒤 LangGraph 파이프라인을 구동하고,
    마지막에 이번 메시지를 다시 단기기억 버퍼에 추가하는 전방위 진입점입니다.
    
    분석이 끝나면 RDB에 즉시 삽입 가능한 Notification (SQLAlchemy Model) 
    객체로 반환합니다. 만약 중복 메시지일 경우 None을 반환합니다.
    """
    
    # 0. 중복 웹훅/동시대발성 중복 파이프라인 진입 차단 (Idempotency)
    is_first = await acquire_message_lock(event.provider_object_id)
    if not is_first:
        print(f"⚠️ 중복 메시지 진입 차단 (Lock) - 빠른 종료 (ID: {event.provider_object_id})")
        return None
    
    # 1. 단기기억(Conversation History) 확보
    channel_id = event.channel_id
    recent_history = []
    if channel_id:
        recent_history = await fetch_short_term_memory(channel_id)
        
    # 2. 이번 메시지 문자열 깔끔하게 포매팅 (rich_contents가 있으면 우선 사용, 없으면 original_text)
    sender = event.sender_name or "Unknown"
    text_to_store = event.rich_contents if event.rich_contents else event.original_text
    formatted_current_msg = f"[{sender}]: {text_to_store}"

    # 3. LangGraph 초기 상태(State) 구성
    metadata = {
        "provider": event.provider,
        "source_type": event.source_type,
        "workspace_id": event.workspace_id,
        "channel_id": channel_id,
        "sender_id": event.sender_id,
        "sender_name": event.sender_name,
        "channel_name": event.channel_name,
        "is_direct_target": event.is_direct_target,
        "is_broadcast": event.is_broadcast,
        "has_attachments": event.has_attachments,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "source_url": event.source_url
    }
    
    initial_state = {
        "message_id": event.provider_object_id,
        "content": text_to_store, # LLM도 풍부한 텍스트를 인풋으로 받음
        "metadata": metadata,
        "conversation_history": recent_history
    }
    
    # Thread ID 지정을 통해 Checkpointer 트래킹
    config = {"configurable": {"thread_id": event.provider_object_id}}
    
    # 4. 실시간 파이프라인 실행
    print(f"🚀 [Pipeline Entry] 이벤트 분석 시작 (ID: {event.provider_object_id})")
    print(f"👉 장착된 단기기억(스레드 문맥) 개수: {len(recent_history)}개")
    final_state = await realtime_graph.ainvoke(initial_state, config=config)
    
    # 5. 파이프라인 무사고 통과 시, 이 메시지를 단기기억에 Push
    # (반드시 After Execution 에 해야 LLM이 중복/동어반복 오류를 일으키지 않음)
    if channel_id:
        await add_to_short_term_memory(channel_id, formatted_current_msg)
        
    print(f"✅ [Pipeline Entry] 처리 완료 및 단기기억 최신화 완료.")
    
    # 6. RDB 저장을 위한 SQLAlchemy Notification 객체 조립
    priority = final_state.get("final_urgency")
    score_map = {"Emergency": 1.0, "High": 0.8, "Normal": 0.5, "Low": 0.1}
    priority_score = score_map.get(priority, 0.0)

    is_schedule_related = final_state.get("is_schedule_related", False)
    if not is_schedule_related:
        calendar_status = "none"
    elif priority in ("Emergency", "High"):
        calendar_status = "prompted"
    else:
        calendar_status = "pending"

    content_preview = event.original_text[:50] + "..." if len(event.original_text) > 50 else event.original_text
    
    # DB Model 맵핑
    notification_db_model = Notification(
        integration_id=event.integration_id,
        raw_event_id=event.raw_event_id,
        source_type=event.source_type,
        provider_object_id=event.provider_object_id,
        title=event.title,
        original_text=event.original_text,
        rich_contents=event.rich_contents,
        content_preview=content_preview,
        source_url=event.source_url,
        sender_name=event.sender_name,
        channel_name=event.channel_name,
        channel_id=event.channel_id,
        is_direct_target=event.is_direct_target,
        is_broadcast=event.is_broadcast,
        has_attachments=event.has_attachments,
        occurred_at=event.occurred_at,
        priority=priority,
        priority_score=priority_score,
        summary=final_state.get("storable_summary"),
        reason=final_state.get("judgment_rationale"),
        is_schedule_related=is_schedule_related,
        calendar_status=calendar_status,
        suggested_start_time=final_state.get("suggested_start_time"),
        suggested_duration_minutes=final_state.get("suggested_duration_minutes"),
    )
    
    # 7. 처리가 완전히 종료되었음을 마킹 (24시간 동안 재진입 방지)
    await mark_message_as_processed(event.provider_object_id)
    
    # 라우터에서 session.add() 하도록 반환
    return notification_db_model
