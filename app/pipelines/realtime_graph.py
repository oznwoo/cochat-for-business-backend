from langgraph.graph import StateGraph, END

from pydantic import BaseModel, Field, field_validator
from typing import Literal
from langchain_core.prompts import ChatPromptTemplate
from app.pipelines.shared.llm import get_chat_llm

from app.core.config import settings
from app.pipelines.state import MessageState
from app.pipelines.shared.retriever_utils import asearch_hybrid_rrf, search_cross_encoder_rerank

# ==============================================================================
# Node Functions
# ==============================================================================

class AnalyzeMessageOutput(BaseModel):
    """LLM이 반드시 준수해야 하는 JSON 출력 스키마"""
    initial_urgency: Literal["Emergency", "High", "Normal", "Low"] = Field(
        description="초기 긴급도 파악 결과. 심각한 장애는 Emergency, 직접 멘션/중요 요청은 High, 일반 알림이나 로그는 Normal, 의미없는 잡담/대답은 Low."
    )
    judgment_rationale: str = Field(description="선택한 긴급도에 대한 상세한 논리적 판단 근거 (Chain of Thought)")
    # Groq(Llama) function_calling은 tool 인자를 서버 단에서 엄격히 타입 검증하는데,
    # 모델이 bool 값을 문자열("True")로 내려주면 그 자리에서 400으로 거부되어 버린다
    # (#31). bool 대신 문자열 스키마로 선언해 모델이 자연스럽게 내는 형식을 그대로
    # 받아들이고, 소비하는 쪽(analyze_message 반환부)에서 bool로 변환한다.
    should_store: Literal["true", "false"] = Field(description="의미 있는 업무 컨텍스트 혹은 중요 로그로써 추후 Vector DB에 기억할 가치가 있는가? true 또는 false 문자열로 응답.")
    storable_summary: str = Field(description="should_store가 true인 경우 검색용 필수 핵심 요약. false인 경우 빈 문자열.")
    issue_type: Literal["new_issue", "ongoing_update", "resolved", "independent"] = Field(
        description="이 메시지의 맥락 유형. (새 장애발생=new_issue, 기존 장애 진행=ongoing_update, 장애 해결/조치완료=resolved, 단발성/독립적 정보=independent)"
    )
    # should_store와 동일한 이유(#31)로 bool 대신 문자열 리터럴로 선언.
    is_schedule_related: Literal["true", "false"] = Field(
        description="마감일/미팅/약속 등 특정 시각에 처리해야 할 일정으로써 캘린더에 등록할 만한 내용을 포함하는가? true 또는 false 문자열로 응답."
    )

    @field_validator("should_store", "is_schedule_related", mode="before")
    @classmethod
    def _normalize_bool_like(cls, v):
        # 모델이 "True"/"False"처럼 대소문자를 섞어 내려주는 경우 Literal 매칭을 위해 정규화
        if isinstance(v, str):
            return v.strip().lower()
        return "true" if v else "false"

class ReassessOutput(BaseModel):
    final_urgency: Literal["Emergency", "High", "Normal", "Low"] = Field(
        description="RAG로 검색된 사내 가이드라인 문맥을 바탕으로 도출된 최종 긴급도"
    )
    judgment_rationale: str = Field(description="긴급도를 이와 같이 판단하게 된 이유 혹은 변경 사유 (핵심만 간결하게)")

async def analyze_message(state: MessageState) -> dict:
    """1차 긴급도 및 저장 가치 판단 (Gemini Flash 사용)"""
    
    # 1. 모델과 파서 초기화 (실제 실행을 위해선 GROQ_API_KEY 환경변수 세팅 필수)
    llm = get_chat_llm(temperature=0)
    # json_mode는 스키마(필드명)를 프롬프트에 강제하지 않아 모델이 엉뚱한 키를
    # 반환해 파싱이 실패했다 (#31 재발). function_calling(기본값)이 스키마를
    # 정확히 전달하므로 되돌리고, 타입 문제는 스키마 쪽(should_store)에서 해결함.
    structured_llm = llm.with_structured_output(AnalyzeMessageOutput)

    # 2. 시스템 프롬프트 설계
    prompt = ChatPromptTemplate.from_messages([
        ("system", "당신은 업무용 메신저(Slack/Discord) 환경의 초고속 알람 필터링 비서입니다.\n\n"
                   "[응급도 분류 가이드라인]\n"
                   "- Emergency: 치명적 서비스 중단, 긴급 보안 이슈 등 즉각적인 조치가 없으면 안 되는 심각한 장애.\n"
                   "- High: 수신자(나)를 직접 멘션한 중요한 업무 요청, 빠른 확인이 필요한 이슈 및 오류 알림.\n"
                   "- Normal: 평범한 채널의 정보성 알림, 당장 조치할 필요 없는 배포 로그, 단순 참조용 스레드.\n"
                   "- Low: 단순 인사(안녕하세요), 동의/수긍('네 알겠습니다', '확인했습니다'), 잡담, 스팸 봇 메시지.\n\n"
                   "[이슈 타입 분류 가이드라인]\n"
                   "제공된 스레드 문맥을 보고 이 메시지가 장애나 작업의 '새로운 시작(new_issue)'인지, '진행 중인 상황 공유(ongoing_update)'인지, '최종 해결 및 조치 완료(resolved)'인지 판단하세요. 앞뒤 맥락이 없는 일회성 알림은 'independent' 입니다.\n\n"
                   "[일정 관련 여부 분류 가이드라인]\n"
                   "이 메시지가 특정 마감일, 미팅, 약속 등 캘린더에 일정으로 등록할 만한 구체적인 시각/기한을 포함하는지 판단하세요 (is_schedule_related). 단순 정보 공유나 잡담에는 true를 주지 마세요.\n\n"
                   "제공된 메타데이터와 본문을 가장 입체적으로 분석하여 JSON으로 반환하세요."
        ),
        ("user", "### 메타데이터:\n{metadata}\n\n"
                 "### 최근 스레드 문맥 (단기 기억):\n{history}\n\n"
                 "### 현재 메시지 본문:\n{content}")
    ])
    
    # 3. 모델 체인지업(Invocation)
    chain = prompt | structured_llm
    
    result = await chain.ainvoke({
        "metadata": state.get("metadata", {}),
        "history": state.get("conversation_history", []),
        "content": state.get("content", "")
    })
    
    # 4. 분석 결과 반환
    return {
        "initial_urgency": result.initial_urgency,
        "final_urgency": result.initial_urgency,
        "judgment_rationale": result.judgment_rationale,
        "should_store": result.should_store == "true",
        "storable_summary": result.storable_summary,
        "issue_type": result.issue_type,
        "is_schedule_related": result.is_schedule_related == "true"
    }

async def fast_retrieve_emergency_context(state: MessageState) -> dict:
    """[Emergency 전용] 초저지연 캐시/하이브리드 직접 검색 (Re-ranking 생략). 핵심 SOP, 치명적 오탐 로그만 조회"""
    query = state.get("content", "")
    # 비동기 pgvector 검색 수행 (< 150ms)
    fused_docs = await asearch_hybrid_rrf(query, top_k=2)
    contexts = [doc.get("content", "") for doc in fused_docs]
    return {"retrieved_context": contexts}

async def reassess_importance(state: MessageState) -> dict:
    """RAG 문맥 바탕 2차 검증 로직 (통합 노드)"""
    llm = get_chat_llm(temperature=0)
    structured_llm = llm.with_structured_output(ReassessOutput)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "당신은 기업용 메신저의 메시지 중요도 심사위원입니다.\n"
                   "이전에 AI 1차 평가 모델이 초기 긴급도(initial_urgency)를 부여했습니다.\n"
                   "방금 우리는 회사의 가이드라인(Context)을 검색해 왔습니다. 이 검색된 문맥에는 [명시적인 사내 강제 룰]과 [과거 진행된 유사 이슈들의 누적 요약 내역]이 섞여 있을 수 있습니다.\n\n"
                   "검색된 문맥 정보에 비추어 볼 때, 이전 평가 모델의 판정이 올바른지 다시 평가하고 최종 긴급도(final_urgency)를 도출하세요. 판단 원칙은 다음과 같습니다:\n"
                   "1. 만약 검색된 사내 가이드라인이 명시적으로 특정 상황을 Emergency 등 매우 높은 긴급도로 올리라고 지시한다면, 무조건 그 규칙을 최우선으로 존중하여 과감히 긴급도를 상향하세요.\n"
                   "2. 반대로 사내 지침에서 이 상황을 '무시해도 좋다'고 명시했다면 Low나 Normal로 낮추세요.\n"
                   "3. 단, 명시적 룰이 있더라도, 과거 수차례 누적된 비슷한 장애 요약본들이 '모두 자동으로 안전하게 복구되었음(예: 이미 안정화됨)'과 같은 압도적인 패턴을 띄고 있다면, 규칙과 실무 현황을 종합적으로 고려하여 당신이 판사로서 유연하게 최종 긴급도를 조율하세요.\n"
                   "4. 지침이나 관련 사례가 없다면 1차 긴급도를 유지합니다."
        ),
        ("user", "### 메타데이터 (채널, 나를 멘션했는지 여부 등):\n{metadata}\n\n"
                 "### 최근 스레드 문맥 (단기 기억):\n{history}\n\n"
                 "### 메시지 본문:\n{content}\n\n"
                 "### [1차 평가 결과]\n- 1차 산출 긴급도: {initial_urgency}\n- 1차 판단 근거: {judgment_rationale}\n\n"
                 "### [검색된 사내 가이드라인 (RAG Context)]\n{retrieved_context}"
        )
    ])
    
    chain = prompt | structured_llm
    
    retrieved_context = state.get("retrieved_context", [])
    context_str = "\n".join(retrieved_context) if retrieved_context else "검색된 문맥이 없습니다."
    
    response = await chain.ainvoke({
        "metadata": state.get("metadata", {}),
        "history": state.get("conversation_history", []),
        "content": state.get("content", ""),
        "initial_urgency": state.get("initial_urgency", "Normal"),
        "judgment_rationale": state.get("judgment_rationale", ""),
        "retrieved_context": context_str
    })
    
    final_urgency = response.final_urgency
    judgment_rationale = response.judgment_rationale
    should_store = state.get("should_store", False)
    storable_summary = state.get("storable_summary", "")
    
    # RAG 재평가 후 긴급도가 낮아지면 저장 플래그 무효화
    if final_urgency == "Low":
        should_store = False
        
    return {
        "final_urgency": final_urgency,
        "judgment_rationale": judgment_rationale,
        "should_store": should_store,
        "storable_summary": storable_summary.strip()
    }

async def deep_retrieve_context(state: MessageState) -> dict:
    """[High/Normal 전용] 높은 정확도를 위한 다단계 재랭킹(Multiphase Ranking) 검색"""
    query = state.get("content", "")
    
    # 1. 비동기 하이브리드 검색 및 RRF로 초기 후보군(Candidate Pool) 구성
    candidates = await asearch_hybrid_rrf(query, top_k=10)
    
    # 2. Cross-Encoder를 통한 2차 정밀 재랭킹 (< 1.5s) (Top-K를 5로 늘려 Rule과 History 모두 포함)
    reranked_docs = search_cross_encoder_rerank(candidates, query, top_k=5)
    
    contexts = [doc.get("content", "") for doc in reranked_docs]
    return {"retrieved_context": contexts}

class IssueMatchOutput(BaseModel):
    matched_id: str = Field(description="일치하는 진행 중 이슈의 ID. 일치하는 것이 없으면 'none'")
    reason: str = Field(description="판단 사유")

async def store_vector_db(state: MessageState) -> dict:
    """(should_store=True) 임베딩하여 Vector DB에 장기 기억으로 저장 (Issue 상태 추적 및 Upsert)"""
    summary = state.get("storable_summary")
    if not summary:
        return {}
        
    from app.pipelines.shared.retriever_utils import astore_document_to_vector_db, afetch_ongoing_memories_by_channel
    
    metadata = state.get("metadata", {})
    channel_id = metadata.get("channel_id")
    issue_type = state.get("issue_type", "independent")
    
    # 1. 이슈 상태 매핑
    final_status = "resolved" if issue_type == "resolved" else "ongoing" if issue_type in ["new_issue", "ongoing_update"] else "none"
    
    # 기본 저장용 메타데이터 구성
    store_meta = {
        "message_id": state.get("message_id"), 
        "urgency": state.get("final_urgency"),
        "source": "realtime_summary",
        "issue_status": final_status,
        "occurred_at": metadata.get("occurred_at"),
        "workspace_id": metadata.get("workspace_id"),
        "channel_id": channel_id,
        "sender_id": metadata.get("sender_id")
    }

    # 2. 기존 Ongoing 이슈에 달리는 후속 조치/해결 메시지인 경우 매칭 (Entity Resolution)
    if issue_type in ["ongoing_update", "resolved"] and channel_id:
        ongoing_mems = await afetch_ongoing_memories_by_channel(channel_id)
        if ongoing_mems:
            mem_str = "\n".join([f"- [ID: {m['id']}] {m['content']}" for m in ongoing_mems])
            
            llm = get_chat_llm(temperature=0)
            structured_llm = llm.with_structured_output(IssueMatchOutput)
            prompt = ChatPromptTemplate.from_template(
                "당신은 이슈 해결 추적 시스템입니다. 방금 들어온 [새로운 메시지 요약]이 현재 같은 채널에서 진행 중인 [과거 이슈 목록] 중 어떤 것의 후속 대화인지 심사하세요.\n"
                "[새로운 메시지 요약]: {summary}\n\n[진행 중인 과거 이슈 목록]:\n{mem_str}\n\n"
                "일치하는 이슈가 있다면 해당 ID를, 일치하는 게 전혀 없다면 'none'을 반환하세요."
            )
            try:
                match_res = await (prompt | structured_llm).ainvoke({"summary": summary, "mem_str": mem_str})
                if match_res.matched_id != "none":
                    # 매칭 성공: 기존 문서에 텍스트 덧붙이기(Merge) 후 Upsert
                    matched_mem = next((m for m in ongoing_mems if m["id"] == match_res.matched_id), None)
                    if matched_mem:
                        merged_content = f"{matched_mem['content']}\n\n[업데이트]: {summary}"
                        # 기존 문서의 message_id(고유식별자)를 재사용하여 Upsert 기동
                        store_meta["message_id"] = matched_mem["id"]
                        
                        await astore_document_to_vector_db(content=merged_content, metadata=store_meta)
                        print(f"🔄 [이슈 병합 성공] 기존 메모리({matched_mem['id']})에 새 진행사항 업데이트 완료. 상태: {final_status}")
                        return {}
            except Exception as e:
                print(f"⚠️ 이슈 매칭 중 오류 발생: {e}")

    # 3. 매칭 실패 혹은 신규 이슈인 경우 (순수 Insert)
    await astore_document_to_vector_db(content=summary, metadata=store_meta)
    return {}

# ==============================================================================
# Routing Functions
# ==============================================================================

def check_urgency(state: MessageState) -> str:
    urgency = state.get("initial_urgency", "Low")
    if urgency == "Emergency":
         return "emergency"
    elif urgency in ["High", "Normal"]:
         return "high_normal"
    else:
         return "low_store" if state.get("should_store", False) else "low_end"

def check_should_store(state: MessageState) -> str:
    return "store" if state.get("should_store", False) else "end"


# ==============================================================================
# Graph Builder
# ==============================================================================

realtime_builder = StateGraph(MessageState)
realtime_builder.add_node("analyze_message", analyze_message)
realtime_builder.add_node("fast_retrieve_emergency_context", fast_retrieve_emergency_context)
realtime_builder.add_node("deep_retrieve_context", deep_retrieve_context)
realtime_builder.add_node("reassess_importance", reassess_importance)
realtime_builder.add_node("store_vector_db", store_vector_db)

realtime_builder.set_entry_point("analyze_message")

# 라우팅 1: 분류에 따라 계층화된 검색(Adaptive RAG) 라우팅
realtime_builder.add_conditional_edges(
    "analyze_message",
    check_urgency,
    {
        "emergency": "fast_retrieve_emergency_context",
        "high_normal": "deep_retrieve_context",
        "low_store": "store_vector_db",
        "low_end": END
    }
)

# 조기 종료 분기를 제외한 나머지(RAG 분기들)는 재평가 노드로 수렴
realtime_builder.add_edge("fast_retrieve_emergency_context", "reassess_importance")
realtime_builder.add_edge("deep_retrieve_context", "reassess_importance")

# 재평가(Reassess) 후에는 바로 조건부 엣지로 저장 여부 결정 및 분기
realtime_builder.add_conditional_edges(
    "reassess_importance",
    check_should_store,
    {
        "store": "store_vector_db",
        "end": END
    }
)
realtime_builder.add_edge("store_vector_db", END)

# ==============================================================================
# Graph Compilation
# ==============================================================================
# 체크포인터 없이 컴파일. MemorySaver는 메시지마다 고유한 thread_id로 체크포인트를
# 만료 없이 프로세스 메모리에 영구 보관해 메모리 누수를 일으켰고(#25), time-travel/resume 등
# 체크포인트 기능을 실제로 소비하는 코드가 없어 필요하지도 않았다.
realtime_graph = realtime_builder.compile()


# ==============================================================================
# External API Wrappers (DTO 반환용 래퍼 함수)
# ==============================================================================

async def run_realtime_pipeline(initial_state: dict, config: dict = None) -> dict:
    """[실시간 처리] 파이프라인 실행 후 API 계층에 필요한 핵심 속성만 필터링하여 리턴"""
    full_state = await realtime_graph.ainvoke(initial_state, config=config)
    return {
        "message_id": full_state.get("message_id"),
        "final_urgency": full_state.get("final_urgency"),
        "judgment_rationale": full_state.get("judgment_rationale"),
        "should_store": full_state.get("should_store"),
        "storable_summary": full_state.get("storable_summary"),
        "is_schedule_related": full_state.get("is_schedule_related")
    }

__all__ = ["realtime_graph", "run_realtime_pipeline"]
