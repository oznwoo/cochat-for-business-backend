import asyncio
from datetime import datetime
from app.pipelines.memory_gc_graph import run_gc_pipeline
from app.pipelines.retry import retry_failed_raw_events

async def run_gc_scheduler(interval_hours: int = 24):
    """
    백그라운드에서 일정 주기마다 Vector DB의 Stale Memory(오래된 요약 기록)를 정리하는 가비지 컬렉터 태스크.
    FastAPI Lifespan을 통해 관리됩니다.
    """
    print(f"🔄 백그라운드 GC 스케줄러가 데몬으로 구동되었습니다. (주기: 매 {interval_hours}시간)")
    
    # 개발 서버 시작 직후 바로 실행하면 오버헤드가 크므로 첫 회차는 10분 대기 (옵션)
    await asyncio.sleep(10)
    
    interval_seconds = interval_hours * 3600
    
    while True:
        try:
            print(f"🧹 [Auto GC] {datetime.now()} - 정기 Vector DB 가비지 컬렉션을 시작합니다...")
            
            # GC 파이프라인(LLM 검증 및 판단 -> 삭제) 시작
            config = {"configurable": {"thread_id": "auto_gc_routine"}}
            result = await run_gc_pipeline({}, config=config)
            
            evaluations = result.get("evaluation_results", [])
            delete_count = sum(1 for item in evaluations if item.get("action") == "delete")
            keep_count = len(evaluations) - delete_count
            
            print(f"✅ [Auto GC 완료] 검토 대상: {len(evaluations)}개 | 삭제 판정: {delete_count}개 | 유지 판정: {keep_count}개")
            
        except asyncio.CancelledError:
            # 서버가 종료될 때 CancelledError가 발생하므로 스케줄러를 안전하게 종료
            print("🛑 백그라운드 GC 스케줄러가 취소(종료)되었습니다.")
            break
        except Exception as e:
            # GC는 백그라운드 태스크이므로 에러가 나더라도 무한 루프를 깨트리지 않게 보호
            print(f"❌ [Auto GC 실패] 파이프라인 수행 중 에러 발생: {e}")
            
        # 다음 주기까지 대기 (Sleep을 깰 때 CancelledError를 자동으로 받게 됨)
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            print("🛑 백그라운드 GC 스케줄러가 종료 신호를 받아 대기를 중단합니다.")
            break


async def run_retry_scheduler(interval_minutes: int = 10):
    """백그라운드에서 일정 주기마다 실패한 raw_event를 재처리하는 태스크 (#13).

    파이프라인 처리 실패(LLM 타임아웃/장애 등)로 조용히 유실되던 알림을,
    Redis 메시지 락(60초 TTL)이 충분히 해제된 뒤 주기적으로 재시도한다.
    FastAPI Lifespan을 통해 관리됩니다.
    """
    print(f"🔄 백그라운드 재시도 스케줄러가 데몬으로 구동되었습니다. (주기: 매 {interval_minutes}분)")

    await asyncio.sleep(60)

    interval_seconds = interval_minutes * 60

    while True:
        try:
            print(f"♻️ [Auto Retry] {datetime.now()} - 실패한 raw_event 재처리를 시작합니다...")
            retried_count = await retry_failed_raw_events()
            print(f"✅ [Auto Retry 완료] 재처리 시도: {retried_count}건")

        except asyncio.CancelledError:
            print("🛑 백그라운드 재시도 스케줄러가 취소(종료)되었습니다.")
            break
        except Exception as e:
            print(f"❌ [Auto Retry 실패] 재처리 수행 중 에러 발생: {e}")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            print("🛑 백그라운드 재시도 스케줄러가 종료 신호를 받아 대기를 중단합니다.")
            break
