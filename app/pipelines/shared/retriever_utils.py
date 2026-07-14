from typing import List, Dict, Any
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_core.documents import Document
from langchain_postgres.vectorstores import PGVector
from sqlalchemy import text
import os

from app.db.session import engine

_cross_encoder = None

async def _is_vector_table_ready() -> bool:
    """langchain_pg_embedding 테이블이 DB에 존재하는지 확인합니다."""
    check = text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'langchain_pg_embedding'
        LIMIT 1
    """)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(check)
            return result.fetchone() is not None
    except Exception:
        return False

def _get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        try:
            from sentence_transformers import CrossEncoder
            # 한국어에 특화된 로컬 Reranker 모델 로드 (최초 1회 다운로드 빌드됨)
            if not os.getenv("HF_TOKEN"):
                print("💡 에러: HF_TOKEN 환경변수가 설정되지 않았습니다!")
                print("💡 .env 파일에 키를 넣거나 터미널에서 export 해주세요.")
            _cross_encoder = CrossEncoder('Dongjin-kr/ko-reranker')
        except ImportError:
            print("⚠️ sentence-transformers가 설치되지 않았습니다. 컨테이너를 재빌드해주세요.")
            return None
    return _cross_encoder

def _get_vector_store() -> PGVector:
    """공용 PGVector 인스턴스 반환 함수"""
    embeddings = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001")
    
    return PGVector(
        embeddings=embeddings,
        collection_name="message_guidelines",
        connection=engine,
        use_jsonb=True,
    )

async def astore_document_to_vector_db(content: str, metadata: dict = None) -> bool:
    """텍스트 내용을 Vector DB에 삽입 (비동기)"""
    if not content:
        return False
        
    try:
        vector_store = _get_vector_store()
        meta = metadata or {}
        doc = Document(page_content=content, metadata=meta)
        
        # message_id가 있으면 명시적 id 리스트 생성 (Upsert로 동작하게 됨)
        msg_id = meta.get("message_id")
        doc_ids = [str(msg_id)] if msg_id else None
        
        await vector_store.aadd_documents([doc], ids=doc_ids)
        return True
    except Exception as e:
        print(f"⚠️ Vector DB 저장 실패: {e}")
        return False

async def adelete_documents_from_vector_db(ids: List[str]) -> bool:
    """Vector DB에서 특정 ID 배열에 해당하는 문서들을 삭제 (비동기)"""
    if not ids:
        return False
        
    try:
        vector_store = _get_vector_store()
        await vector_store.adelete(ids=ids)
        return True
    except Exception as e:
        print(f"⚠️ Vector DB 데이터 삭제 실패: {e}")
        return False

async def afetch_stale_memories_from_db(limit: int = 10) -> List[Dict[str, Any]]:
    """가장 오래된 (occurred_at 기준) realtime_summary 메모리를 가져옵니다."""
    if not await _is_vector_table_ready():
        return []

    # 랭체인 PGVector의 기본 테이블 구조에 의존 (cmetadata 컬럼 사용)
    query = text("""
        SELECT id, document, cmetadata 
        FROM langchain_pg_embedding 
        WHERE cmetadata->>'source' = 'realtime_summary'
        ORDER BY cmetadata->>'occurred_at' ASC NULLS LAST
        LIMIT :limit
    """)
    
    results = []
    try:
        async with engine.begin() as conn:
            result_proxy = await conn.execute(query, {"limit": limit})
            rows = result_proxy.fetchall()
            for row in rows:
                results.append({
                    "id": str(row.id),
                    "content": row.document,
                    "metadata": row.cmetadata
                })
        return results
    except Exception as e:
        print(f"⚠️ Vector DB 조회 쿼리 실패: {e}")
        return []

async def afetch_ongoing_memories_by_channel(channel_id: str) -> List[Dict[str, Any]]:
    """특정 채널 내에서 '진행 중(ongoing)'인 작업 메모리 추출"""
    if not channel_id:
        return []

    if not await _is_vector_table_ready():
        return []

    query = text("""
        SELECT id, document, cmetadata 
        FROM langchain_pg_embedding 
        WHERE cmetadata->>'channel_id' = :channel_id 
          AND cmetadata->>'issue_status' = 'ongoing'
        ORDER BY cmetadata->>'occurred_at' DESC
        LIMIT 5
    """)
    
    results = []
    try:
        async with engine.begin() as conn:
            result_proxy = await conn.execute(query, {"channel_id": channel_id})
            rows = result_proxy.fetchall()
            for row in rows:
                # Upsert용 외부 고유 ID는 cmetadata의 message_id로 보존됨
                results.append({
                    "id": str(row.cmetadata.get("message_id", row.id)),
                    "content": row.document,
                    "metadata": row.cmetadata
                })
        return results
    except Exception as e:
        print(f"⚠️ Ongoing 이슈 조회 쿼리 실패: {e}")
        return []

def compute_rrf(dense_results: List[Dict[str, Any]], sparse_results: List[Dict[str, Any]], k: int = 60) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion (RRF) 알고리즘을 이용해 두 검색 결과를 융합합니다.
    
    Args:
        dense_results: [{"id": "문서고유ID", "content": "문서내용", "score": float}, ...]
        sparse_results: [{"id": "문서고유ID", "content": "문서내용", "score": float}, ...]
        k: RRF 하이퍼파라미터 (일반적으로 60 사용 권장)
    Returns:
        fused_results: RRF Score 기준으로 정렬된 통합 문서 리스트
    """
    rrf_scores = {}
    doc_lookup = {}
    
    # 1. 밀집(Dense) 벡터 순위에 따른 점수 합산
    for rank, doc in enumerate(dense_results, start=1):
        doc_id = doc.get("id")
        if not doc_id:
            continue
        doc_lookup[doc_id] = doc
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank)
        
    # 2. 희소(Sparse/BM25) 벡터 순위에 따른 점수 누적 합산
    for rank, doc in enumerate(sparse_results, start=1):
        doc_id = doc.get("id")
        if not doc_id:
            continue
        doc_lookup[doc_id] = doc
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank)
        
    # 3. 누적 점수 높은 순으로 최종 정렬
    sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    
    # 4. 정렬된 ID를 바탕으로 결과 조립
    fused_results = []
    for doc_id, score in sorted_docs:
        fused_doc = doc_lookup[doc_id].copy()
        fused_doc["rrf_score"] = score
        fused_results.append(fused_doc)
        
    return fused_results


async def asearch_hybrid_rrf(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """하이브리드 검색 및 RRF 병합 수행 유틸리티 (실제 PostgreSQL pgvector 연결)"""
    if not await _is_vector_table_ready():
        return []

    try:
        # 공통 함수로 Vector DB 커넥션 획득
        vector_store = _get_vector_store()
        
        # 비동기 검색 (Dense)
        dense_docs = await vector_store.asimilarity_search_with_score(query, k=10)
        dense_results = [
            {"id": str(doc.metadata.get("id", i)), "content": doc.page_content, "score": score} 
            for i, (doc, score) in enumerate(dense_docs)
        ]
    except Exception as e:
        print(f"⚠️ Vector DB 조회 실패 (테이블이 비어있거나 생성되지 않음): {e}")
        dense_results = []
    
    # 4. 희소(Sparse/BM25) 검색
    sparse_results = []
    
    # 순수 Postgres Full-Text Search (FTS) 원시 쿼리
    sql_query = text("""
        SELECT e.cmetadata, e.document, ts_rank(to_tsvector('simple', e.document), plainto_tsquery('simple', :search_query)) as rank_score
        FROM langchain_pg_embedding e
        JOIN langchain_pg_collection c ON e.collection_id = c.uuid
        WHERE c.name = 'message_guidelines' 
          AND to_tsvector('simple', e.document) @@ plainto_tsquery('simple', :search_query)
        ORDER BY rank_score DESC
        LIMIT 10
    """)
    
    try:
        async with engine.connect() as conn:
            result = await conn.execute(sql_query, {"search_query": query})
            rows = result.fetchall()
            
            for i, row in enumerate(rows):
                meta = row[0] or {}
                doc_text = row[1]
                score = float(row[2])
                
                doc_id = meta.get("id", f"sparse_{i}")
                sparse_results.append({
                    "id": str(doc_id),
                    "content": doc_text,
                    "score": score
                })
    except Exception as e:
        print(f"⚠️ Vector DB Sparse 조회를 실패했습니다: {e}")
    
    # 5. RRF 융합 로직 태우기
    if not dense_results and not sparse_results:
        return []
        
    fused_results = compute_rrf(dense_results, sparse_results)
    
    return fused_results[:top_k]


def search_cross_encoder_rerank(candidates: List[Dict[str, Any]], query: str, top_k: int = 2) -> List[Dict[str, Any]]:
    """[High/Normal 전용] Cross-Encoder를 통한 정밀 재랭킹 로직"""
    if not candidates:
        return []
        
    encoder = _get_cross_encoder()
    if not encoder:
        # 모델 패키지가 없으면 기존 RRF 순위대로 자름
        return candidates[:top_k]
        
    pairs = [[query, c["content"]] for c in candidates]
    
    try:
        # 모델 추론 수행 (Logit 점수 반환)
        scores = encoder.predict(pairs)
        
        # 각 후보에 점수 매핑
        for idx, score in enumerate(scores):
            candidates[idx]["cross_encoder_score"] = float(score)
            
        # 가장 연관성이 높은 순서대로 딥 학습 내림차순 정렬
        reranked = sorted(candidates, key=lambda x: x.get("cross_encoder_score", -999.0), reverse=True)
        return reranked[:top_k]
    except Exception as e:
        print(f"⚠️ Cross-Encoder 추론 중 에러 발생: {e}")
        return candidates[:top_k]
