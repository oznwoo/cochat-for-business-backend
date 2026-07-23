from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from typing import Literal, List
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings
from app.pipelines.state import FeedbackState
from app.pipelines.shared.retriever_utils import asearch_hybrid_rrf, adelete_documents_from_vector_db, astore_document_to_vector_db

class GuidelineOutput(BaseModel):
    extracted_guideline: str = Field(description="단순명료한 한 줄짜리 일반화된 행동 지침 (가이드라인)")

class ValidationOutput(BaseModel):
    validation_result: Literal["Valid", "Merge", "Conflict"] = Field(description="가이드라인의 유효성 검증 상태")
    conflicting_doc_ids: List[str] = Field(description="Conflict 감지 시 문제의 기존 가이드라인 ID 배열", default_factory=list)


# ==============================================================================
# Node Functions


async def extract_correction_guideline(state: FeedbackState) -> dict:
    """원분류와 수정분류의 차이로부터 Few-shot 가이드라인을 LLM으로 추출"""
    prompt = ChatPromptTemplate.from_template(
        "당신은 사내 알림(메시지) 분류 시스템의 '정책 학습 엔진' 관리자입니다.\n"
        "AI가 아래 메시지를 바탕으로 '{original_urgency}' 등급을 내렸습니다. (사유: {original_rationale})\n"
        "하지만 멘토(사용자)가 이 등급을 '{user_corrected_urgency}'(으)로 최종 정정했습니다. (정정 사유: {feedback_reason})\n\n"
        "[메시지 본문]: {content}\n"
        "[메타데이터]: {metadata}\n\n"
        "이 오분류 경험을 바탕으로, 앞으로 비슷한 패턴의 메시지를 만났을 때 모델이 참조해야 할 "
        "일반화된 판단 기준(가이드라인)을 아주 간결하고 명확하게 한 줄로 명령조로 작성하세요."
    )
    llm = ChatGoogleGenerativeAI(model=settings.GEMINI_MODEL_NAME, temperature=0)
    structured_llm = llm.with_structured_output(GuidelineOutput)
    
    response = await (prompt | structured_llm).ainvoke({
        "original_urgency": state.get("original_urgency", ""),
        "original_rationale": state.get("original_rationale", ""),
        "user_corrected_urgency": state.get("user_corrected_urgency", ""),
        "feedback_reason": state.get("feedback_reason", ""),
        "content": state.get("content", ""),
        "metadata": state.get("metadata", {})
    })
    return {"extracted_guideline": response.extracted_guideline}

async def validate_guideline_consistency(state: FeedbackState) -> dict:
    """도출된 신규 가이드라인이 기존 DB의 가이드라인과 겹치거나 충돌하지 않는지(RAG Poisoning 영지) 교차 검증"""
    new_guideline = state.get("extracted_guideline", "")
    
    # 1. 기존 유사 가이드라인 3개 하이브리드 검색
    candidates = await asearch_hybrid_rrf(new_guideline, top_k=3)
    if not candidates:
        return {"validation_result": "Valid", "conflicting_doc_ids": []}
        
    context_str = "\n".join([f"- [ID: {c['id']}] {c['content']}" for c in candidates])
    
    # 2. LLM Evaluator로 충돌(Conflict) 여부 심판
    prompt = ChatPromptTemplate.from_template(
        "사용자가 방금 제공한 [신규 가이드라인]과 DB에 저장되어 있던 [기존 가이드라인 목록]을 비교합니다.\n\n"
        "[신규 가이드라인]: {new_guideline}\n\n"
        "[기존 가이드라인 목록]:\n{context_str}\n\n"
        "방침이 완전히 상충(정반대 분류 명령)하는 내용이 있다면 'Conflict'로 취급하고 해당 항목의 ID를 배열에 넣으세요.\n"
        "내용이 비슷하여 둘 다 있어도 무방하거나 상호 보완적이라면 'Valid'를 반환하세요."
    )
    llm = ChatGoogleGenerativeAI(model=settings.GEMINI_MODEL_NAME, temperature=0)
    structured_llm = llm.with_structured_output(ValidationOutput)
    
    response = await (prompt | structured_llm).ainvoke({
        "new_guideline": new_guideline,
        "context_str": context_str
    })
    
    return {
        "validation_result": response.validation_result,
        "conflicting_doc_ids": response.conflicting_doc_ids
    }

async def override_conflicting_guideline(state: FeedbackState) -> dict:
    """[1인 환경] 기존 핵심 정책과 충돌하는 경우, 사용자의 최신 피드백을 '최우선(Source of Truth)'으로 보고 과거 규칙을 덮어씀(Override)"""
    ids_to_delete = state.get("conflicting_doc_ids", [])
    if ids_to_delete:
        await adelete_documents_from_vector_db(ids_to_delete)
        print(f"🗑️ 구형 충돌 지침 규칙 {len(ids_to_delete)}개 삭제 완료 (IDs: {ids_to_delete})")
    
    return {}

async def store_feedback_guideline(state: FeedbackState) -> dict:
    """검증을 통과한 가이드라인을 RAG 컨텍스트 생성을 위해 Vector DB에 신규 Insert 수행"""
    guideline = state.get("extracted_guideline")
    if guideline:
        await astore_document_to_vector_db(
            content=guideline,
            metadata={
                "message_id": state.get("message_id", "feedback_auto"),
                "source": "user_feedback_guideline"
            }
        )
    return {}

# ==============================================================================
# Routing Functions
# ==============================================================================

def check_guideline_validity(state: FeedbackState) -> str:
    """검증 결과에 따른 라우팅"""
    res = state.get("validation_result", "Valid").lower()
    if res in ["valid", "merge"]:
        return "store"
    else:  # "conflict"
        return "override"

# ==============================================================================
# Graph Builder
# ==============================================================================

feedback_builder = StateGraph(FeedbackState)
feedback_builder.add_node("extract_correction_guideline", extract_correction_guideline)
feedback_builder.add_node("validate_guideline_consistency", validate_guideline_consistency)
feedback_builder.add_node("override_conflicting_guideline", override_conflicting_guideline)
feedback_builder.add_node("store_feedback_guideline", store_feedback_guideline)

feedback_builder.set_entry_point("extract_correction_guideline")
feedback_builder.add_edge("extract_correction_guideline", "validate_guideline_consistency")

feedback_builder.add_conditional_edges(
    "validate_guideline_consistency",
    check_guideline_validity,
    {
        "store": "store_feedback_guideline",
        "override": "override_conflicting_guideline"
    }
)
feedback_builder.add_edge("store_feedback_guideline", END)

# 충돌 규칙을 삭제한 후, 신규 규칙을 새로 삽입하기 위해 store 노드로 이동
feedback_builder.add_edge("override_conflicting_guideline", "store_feedback_guideline")

# ==============================================================================
# Graph Compilation
# ==============================================================================
# 체크포인터 없이 컴파일. MemorySaver는 thread_id별 체크포인트를 만료 없이
# 프로세스 메모리에 영구 보관해 메모리 누수를 일으켰고(#25), time-travel/resume 등
# 체크포인트 기능을 실제로 소비하는 코드가 없어 필요하지도 않았다.
feedback_graph = feedback_builder.compile()

# ==============================================================================
# External API Wrappers
# ==============================================================================

async def run_feedback_pipeline(initial_state: dict, config: dict = None) -> dict:
    """[피드백 처리] 파이프라인 실행 후 검증 결과 및 추출된 룰만 리턴"""
    full_state = await feedback_graph.ainvoke(initial_state, config=config)
    return {
        "message_id": full_state.get("message_id"),
        "extracted_guideline": full_state.get("extracted_guideline"),
        "validation_result": full_state.get("validation_result")
    }

__all__ = ["feedback_graph", "run_feedback_pipeline"]
