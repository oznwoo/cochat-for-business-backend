from datetime import datetime
from typing import TypedDict, Optional, List, Dict, Any

class MessageMetadata(TypedDict, total=False):
    """
    Slack과 Discord 등 플랫폼 종속성을 제거한 통합 메타데이터
    API 계층에서 변환(Mapping)되어 파이프라인으로 주입됩니다.
    """
    provider: str                    # "slack", "discord"
    source_type: str                 # "dm", "mention", "channel_message", "thread_reply"
    
    workspace_id: str                # 슬랙 team, 디스코드 guild_id
    channel_id: str                  # 채널 고유 ID
    sender_id: str                   # 발신자 고유 ID
    sender_name: str                 # 발신자 표시 이름
    channel_name: str                # 채널명
    
    is_direct_target: bool           # 나를 직접 멘션했거나 1:1 DM인가? (높은 가중치)
    is_broadcast: bool               # @everyone, @here 등 전체 공지인가?
    has_attachments: bool            # 첨부파일/Embed 포함 여부
    
    occurred_at: str                 # 발생 시각 (ISO8601, 슬랙 ts, 디스코드 timestamp 통합)
    source_url: str                  # 원문 링크 (Permalink)

# ==============================================================================
# ⚠️ TODO (API/Controller 연동 시 필수 구현 사항)
# ==============================================================================
# 파이프라인 외부(FastAPI 라우터/웹훅 핸들러 등)에서 아래 상태 객체를 주입할 때,
# Slack/Discord의 복잡한 페이로드 (본문 텍스트, Embeds, Attachments 설명 등)를 
# 모두 합친(Concat) '하나의 완성된 문자열'로 가공하여 `content` 필드에 넣어주어야 합니다.
# ==============================================================================

class MessageState(TypedDict):
    """
    실시간 메시지 처리 파이프라인 상태
    """
    message_id: str                  # 외부(API)에서 주입된 PostgreSQL 원본 메시지 식별자
    content: str                     # 발신 내용 원본 (API 단에서 텍스트+임베드 Concat 처리됨)
    conversation_history: List[Dict[str, Any]] # 메시지가 발생한 스레드의 최근 대화 맥락 (단기 기억 용도)
    metadata: MessageMetadata        # 정규화된 메시지 속성 정보
    
    initial_urgency: str             # "Emergency", "High", "Normal", "Low", ""
    retrieved_context: List[str]     # RAG 검색 결과 (과거 피드백, 문서 등)
    judgment_rationale: str          # LLM이 도출한 응급도 판단 근거 (피드백 시 원인 분석용)
    final_urgency: str               # "Emergency", "High", "Normal", "Low", ""
    
    should_store: bool               # 임베딩/요약 저장 여부
    storable_summary: str            # 저장에 적합하게 가공된 내용 요약
    issue_type: str                  # "new_issue", "ongoing_update", "resolved", "independent"
    is_schedule_related: bool        # 마감/미팅/약속 등 캘린더 등록 후보인지 여부
    suggested_start_time: Optional[datetime]   # LLM이 추출한 일정 추정 시작 시각 (참고용, #44)
    suggested_duration_minutes: Optional[int]  # LLM이 추출한 일정 추정 소요 시간(분)


class FeedbackState(TypedDict):
    """
    사용자 피드백 (오분류 정정) 처리 파이프라인 상태
    """
    message_id: str                  # 대상 원본 메시지 식별자 (PostgreSQL)
    content: str                     # 피드백 대상이 된 메시지 원문 (오류 분석용)
    metadata: MessageMetadata        # 해당 메시지의 발생 맥락 (DM이었는지 등 분석)
    
    original_urgency: str            # 기존에 시스템이 판단했던 잘못된 중요도
    original_rationale: str          # 기존 시스템이 오분류를 내렸던 판단 근거 (RDB 조회)
    user_corrected_urgency: str      # 사용자가 정정한 올바른 중요도
    feedback_reason: str             # (선택) 사용자가 입력한 오분류 사유
    
    extracted_guideline: str         # LLM이 도출한 향후 오분류 방지용 일반화 가이드라인 (Few-shot용)
    validation_result: str           # "Valid", "Merge", "Conflict" (RAG Poisoning 방지용 검증 결과)
    conflicting_doc_ids: List[str]   # 충돌하는 과거 가이드라인 ID 배열


class MemoryGCState(TypedDict):
    """
    메모리(Vector DB) 가비지 컬렉션 및 유지보수 파이프라인 상태
    """
    target_memories: List[Dict[str, Any]]     # 검토용으로 가져온 오래된 메모리 청크
    evaluation_results: List[Dict[str, Any]]  # 각 메모리에 대한 평가 결과 (유지, 가중치 하락, 삭제 판단)

__all__ = ["MessageMetadata", "MessageState", "FeedbackState", "MemoryGCState"]
