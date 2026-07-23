from typing import Optional
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.notification import Notification
from app.models.feedback_report import FeedbackReport
from app.pipelines.feedback_graph import run_feedback_pipeline

class FeedbackSubmitEvent(BaseModel):
    """프론트엔드/API에서 들어오는 유저 피드백 페이로드"""
    notification_id: int = Field(description="잘못 분류된 원본 Notification의 PK ID")
    user_corrected_urgency: str = Field(description="사용자가 정정한 올바른 긴급도 (예: Emergency, High, Normal, Low)")
    feedback_reason: Optional[str] = Field(default=None, description="사용자가 남긴 정정 사유 코멘트")

async def run_feedback_pipeline_with_db(event: FeedbackSubmitEvent) -> Optional[FeedbackReport]:
    """
    HTTP 핸들러에서 직접 호출할 수 있는 피드백 파이프라인 외부 래퍼
    - DB에서 연관 Notification 조회
    - State 조립 및 feedback_graph 실행
    - 결과에 따른 FeedbackReport DB 저장 및 리턴
    """
    async with AsyncSessionLocal() as session:
        # 1. 대상 원본 메시지 RDB 조회
        stmt = select(Notification).where(Notification.id == event.notification_id)
        result = await session.execute(stmt)
        notification_db = result.scalar_one_or_none()

        if not notification_db:
            print(f"⚠️ 피드백 대상을 찾을 수 없습니다. (ID: {event.notification_id})")
            return None

        # 2. 피드백 전용 State (딕셔너리) 조립
        # Notification 테이블에는 원래 AI가 내렸던 priority와 reason이 저장되어 있음
        content_to_use = notification_db.rich_contents if notification_db.rich_contents else (notification_db.original_text or "")
        
        initial_state = {
            "message_id": notification_db.provider_object_id,
            "content": content_to_use,
            "metadata": {
                "channel_id": notification_db.channel_id,
                "sender_name": notification_db.sender_name,
                "source_url": notification_db.source_url
            },
            "original_urgency": notification_db.priority or "Normal",
            "original_rationale": notification_db.reason or "",
            "user_corrected_urgency": event.user_corrected_urgency,
            "feedback_reason": event.feedback_reason or ""
        }

        # 3. LangGraph 파이프라인(feedback_graph) 실행 (실제 룰 추출 및 검증 처리)
        config = {"configurable": {"thread_id": f"feedback_{notification_db.provider_object_id}"}}
        pipeline_output = await run_feedback_pipeline(initial_state, config=config)

        # 4. 검증 완료 후, 피드백 히스토리를 DB에 기록 (FeedbackReport)
        new_report = FeedbackReport(
            notification_id=notification_db.id,
            report_type="priority_correction",
            expected_priority=event.user_corrected_urgency,
            comment=event.feedback_reason,
            model_version=f"{settings.GEMINI_MODEL_NAME}-pipeline"
        )
        
        # 참고용으로 추출된 가이드라인을 코멘트에 덧붙여서 디버깅 용이하게 만듦
        if pipeline_output.get("extracted_guideline"):
            new_report.comment = f"{(new_report.comment or '')}\n\n[AI Extracted Rule]: {pipeline_output['extracted_guideline']}".strip()

        session.add(new_report)
        await session.commit()
        await session.refresh(new_report)

        print(f"✅ 피드백 파이프라인 완료 및 기록 저장 (Report ID: {new_report.id})")
        return new_report
