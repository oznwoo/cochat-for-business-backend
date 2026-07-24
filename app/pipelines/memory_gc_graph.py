from langgraph.graph import StateGraph, END

from pydantic import BaseModel, Field
from typing import Literal, List
from langchain_core.prompts import ChatPromptTemplate
from app.pipelines.shared.llm import get_chat_llm

from app.core.config import settings
from app.pipelines.state import MemoryGCState
from app.pipelines.shared.retriever_utils import afetch_stale_memories_from_db, adelete_documents_from_vector_db

# ==============================================================================
# Pydantic Schemas for Output Parsing
# ==============================================================================

class MemoryEvaluation(BaseModel):
    id: str = Field(description="메모리의 원본 문서 ID")
    action: Literal["keep", "delete"] = Field(description="메모리 보존(keep) 또는 삭제(delete) 결정")
    reason: str = Field(description="그러한 결정을 내린 짧은 논리적 이유")

class EvaluatorOutput(BaseModel):
    evaluations: List[MemoryEvaluation] = Field(description="메모리 배열 각각에 대한 평가 결과 리스트")

# ==============================================================================
# Node Functions
# ==============================================================================

async def fetch_stale_memories(state: MemoryGCState) -> dict:
    """오래된 임베딩(시간순 가장 과거 데이터)을 Vector DB에서 조회"""
    memories = await afetch_stale_memories_from_db(limit=5)
    return {"target_memories": memories}

async def evaluate_memory_relevance(state: MemoryGCState) -> dict:
    """LLM이 현재 시점 기준으로 메모리의 유효성을 평가 (유지/삭제)"""
    targets = state.get("target_memories", [])
    if not targets:
        return {"evaluation_results": []}

    # 메모리 목록을 하나의 문자열로 결합 (Batch 처리를 위해)
    memories_str = ""
    for idx, mem in enumerate(targets):
        memories_str += f"[Index: {idx}] - [ID: {mem['id']}]\n"
        memories_str += f"Content: {mem['content']}\n"
        memories_str += f"Occurred_at: {mem.get('metadata', {}).get('occurred_at', 'N/A')}\n"
        memories_str += "-" * 50 + "\n"

    prompt = ChatPromptTemplate.from_messages([
        ("system", "당신은 Vector DB 메모리 가비지 컬렉터(GC)입니다.\n"
                   "제공된 과거 시스템 알림이나 로그 요약 목록을 읽고, 각각을 장기기억에 계속 유지('keep')할지, 아니면 가치가 소멸하여 파괴('delete')할지 결정하세요.\n"
                   "[삭제(delete) 기준]: 시간이 지나면 잊혀져도 되는 단발성 타임아웃, 임의 해결된 일반 알림, 당장 해결된 단순 버그 로그.\n"
                   "[유지(keep) 기준]: 심각한 시스템 장애나 보안 이슈로 기록에 남길 가치가 있거나, 아키텍처적 결함, SOP(표준 운영 절차) 등 지속적으로 언젠가 참조할 가능성이 있는 지식정보."
        ),
        ("user", "### 평가 대상 메모리 목록:\n{memories_str}")
    ])
    
    llm = get_chat_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(EvaluatorOutput)
    
    try:
        response = await (prompt | structured_llm).ainvoke({"memories_str": memories_str})
        # Pydantic 객체를 dict 형식으로 변환 후 상태에 저장
        results = [{"id": item.id, "action": item.action, "reason": item.reason} for item in response.evaluations]
        return {"evaluation_results": results}
    except Exception as e:
        print(f"⚠️ LLM GC 주기 평가 실패: {e}")
        return {"evaluation_results": []}

async def update_or_delete_vector_db(state: MemoryGCState) -> dict:
    """평가 결과에 따라 Vector DB의 임베딩 삭제 연산"""
    evaluations = state.get("evaluation_results", [])
    delete_ids = [item["id"] for item in evaluations if item["action"] == "delete"]
    
    if delete_ids:
        success = await adelete_documents_from_vector_db(delete_ids)
        if success:
            print(f"🗑️ [GC 완료] 총 {len(delete_ids)}개의 데이터 영구 삭제 통과 (IDs: {delete_ids})")
            
    return {}

# ==============================================================================
# Graph Builder
# ==============================================================================

gc_builder = StateGraph(MemoryGCState)
gc_builder.add_node("fetch_stale_memories", fetch_stale_memories)
gc_builder.add_node("evaluate_memory_relevance", evaluate_memory_relevance)
gc_builder.add_node("update_or_delete_vector_db", update_or_delete_vector_db)

gc_builder.set_entry_point("fetch_stale_memories")
gc_builder.add_edge("fetch_stale_memories", "evaluate_memory_relevance")
gc_builder.add_edge("evaluate_memory_relevance", "update_or_delete_vector_db")
gc_builder.add_edge("update_or_delete_vector_db", END)

# ==============================================================================
# Graph Compilation
# ==============================================================================
# 체크포인터 없이 컴파일. MemorySaver는 체크포인트를 만료 없이 프로세스 메모리에
# 영구 보관해 메모리 누수를 일으켰고(#25), time-travel/resume 등 체크포인트 기능을
# 실제로 소비하는 코드가 없어 필요하지도 않았다.
memory_gc_graph = gc_builder.compile()

# ==============================================================================
# External API Wrappers
# ==============================================================================

async def run_gc_pipeline(initial_state: dict, config: dict = None) -> dict:
    """[메모리 정리] 파이프라인 실행 후 처리된 결과 목록 리턴"""
    full_state = await memory_gc_graph.ainvoke(initial_state, config=config)
    return {
        "evaluation_results": full_state.get("evaluation_results", [])
    }

__all__ = ["memory_gc_graph", "run_gc_pipeline"]
