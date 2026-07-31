<p align="center">
  <b>CoChat Backend</b><br/>
  여러 업무 채널의 알림을 한 페이지로 모으고, AI가 긴급도 판단·요약·일정 추출까지 붙여주는 업무용 알림 통합 서비스 'CoChat'의 FastAPI 백엔드
</p>

<p align="center">
  <a href="https://github.com/oh0227/cochat-for-business-frontend">cochat-for-business-frontend</a>와 세트로 동작한다
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/LangGraph-1C3C3C?style=flat-square&logo=langchain&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/pgvector-RAG-6E56CF?style=flat-square" />
  <img src="https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white" />
  <img src="https://img.shields.io/badge/Status-Demo-informational?style=flat-square" />
</p>

---

## 소개

CoChat Backend는 Slack/Discord에 흩어진 업무 알림을 한 곳에 모으고, LangGraph 기반 AI 파이프라인이 긴급도(Emergency/High/Normal/Low)를 판단해 정말 중요한 것만 실시간으로(SSE) 올려주는 서비스 [cochat-for-business-frontend](https://github.com/oh0227/cochat-for-business-frontend)의 API 서버다. 메시지에 일정 정보가 포함돼 있으면 이를 추출해 Google Calendar 등록까지 이어주고, 집중(딥워크) 세션이 끝나면 그동안 쌓인 알림을 AI가 브리핑으로 요약해준다.

2026-03-24 첫 커밋 이후 약 4개월째 175개 커밋으로 개발 중이며, 데모 서비스로 Render에 배포되어 있다.

---

## 기술 스택

| 영역 | 기술 | 비고 |
|---|---|---|
| 언어 | Python 3.11 | |
| 프레임워크 | FastAPI | 비동기 엔드포인트, `lifespan`으로 백그라운드 태스크 관리 |
| ORM | SQLAlchemy (async) | `asyncpg` 드라이버 |
| 데이터베이스 | PostgreSQL + pgvector | 관계형 데이터 + 벡터 임베딩(RAG용 장기 기억)을 한 DB에서 처리 |
| 마이그레이션 | Alembic | |
| 캐시 / 메시징 | Redis | 스레드 단기 기억 버퍼, 메시지 중복 처리 방지 락(TTL) |
| AI 파이프라인 | LangGraph + LangChain | 상태 그래프 기반 적응형(Adaptive) 분류/RAG 파이프라인 |
| 분류 LLM | Groq (Llama 3.3 70B) | 실시간 메시지 긴급도/일정 분류, structured output(tool calling) |
| 임베딩 | Google Gemini | Groq가 임베딩 API를 제공하지 않아 임베딩만 별도로 사용 |
| 실시간 통신 | SSE (Server-Sent Events) | 새 알림을 프론트에 즉시 push |
| 외부 연동 | Slack Events API, Discord Gateway, Google Calendar API | OAuth 기반 유저별 연동 |
| 배포 | Render (Docker) | 단일 512MB 인스턴스 |

---

## 도메인 모델

```
User
 ├─ 1:N ─ IntegrationAccount (provider: slack / discord / google_calendar)
 │          ├─ 1:1 ─ IntegrationToken (access/refresh token, 만료 임박 시 자동 리프레시)
 │          └─ 1:N ─ RawEvent (원본 webhook/gateway payload, status: pending/failed/completed)
 │                     └─ 1:N ─ Notification (AI 분류 결과: 긴급도/요약/일정 추출)
 │                                ├─ 1:N ─ FeedbackReport (유저의 오분류 정정 → 가이드라인 학습)
 │                                └─ N:M ─ Briefing (briefing_notifications 조인 테이블)
 └─ 1:N ─ FocusSession (집중/딥워크 세션)
            └─ 1:N ─ Briefing (세션 종료 시 생성되는 AI 요약 + action items)
```

- **RawEvent 상태 추적(pending/failed/completed) + 재처리**: AI 분류가 서버 프로세스 내 인메모리로 실행돼 처리 도중 예외나 서버 재시작이 발생하면 알림이 조용히 유실될 수 있다. 원본 payload를 raw_event에 그대로 남기고 상태를 추적해, 실패 건은 백그라운드 스케줄러가 원본으로부터 재조립해 다시 투입한다.
- **Notification.calendar_status 상태 머신**: 일정 관련 알림의 캘린더 등록 흐름을 `none → pending/prompted → registered/dismissed`로 관리한다. 긴급도가 높은 일정은 즉시 `prompted`로 시작해 바로 물어보고, 그렇지 않으면 `pending`으로 모아뒀다가 나중에 한 번에 보여준다.
- **IntegrationToken을 IntegrationAccount와 분리**: provider마다 refresh 로직·만료 정책이 달라서, 연동 메타데이터와 토큰 갱신 책임을 별도 테이블로 나눴다.

---

## API 라우터

| 라우터 | prefix | 핵심 기능 |
|---|---|---|
| `integrations` | `/integrations` | Slack/Discord/Google Calendar OAuth 연동·해제, Slack 대화 동기화 |
| `notifications` | `/notifications` | 알림 목록/단건 조회, 읽음 처리, 캘린더 등록 후보 조회, 알림→캘린더 이벤트 등록/거절 |
| `streams` | `/notifications/stream` | SSE 기반 실시간 알림 스트림 |
| `briefing` | `/focus-sessions`, `/briefings` | 집중 세션 시작/종료/활성 세션 조회, 세션 종료 시 AI 브리핑 생성/목록/조회 |
| `calendar_events` | `/calendar-events` | 알림과 무관한 수동 일정 CRUD (Google Calendar 프록시) |
| `slack_webhook` | `/webhooks/slack` | Slack Events API 웹훅 수신 (ingress) |
| `discord_gateway` | — | Discord Gateway 상시 연결로 실시간 메시지 수신 (라우터가 아닌 백그라운드 커넥션) |

---

## 엔지니어링 하이라이트

### LangGraph 기반 적응형 알림 분류 파이프라인

**문제**: Slack/Discord로 쏟아지는 메시지를 사람이 일일이 훑지 않고 걸러내야 하는데, 모든 메시지에 똑같이 무거운 RAG 검색을 돌리면 응답이 느려지고 비용도 커진다.

**해결**: LangGraph `StateGraph`로 1차 분류(`analyze_message`) → 긴급도별 조건부 라우팅 → RAG 문맥 기반 2차 재평가(`reassess_importance`) → 벡터 DB 저장으로 이어지는 그래프를 구성했다. Emergency는 캐시 위주 초저지연 검색(< 150ms)만, High/Normal은 하이브리드 검색 + 재랭킹까지 거치는 계층화된(Adaptive) 검색으로 지연시간과 정확도를 분리했다.

파일: [app/pipelines/realtime_graph.py](app/pipelines/realtime_graph.py)

### Groq function-calling의 비결정적 실패 대응

**문제**: 구조화 출력(tool calling)으로 긴급도·일정 정보를 뽑는데, Groq가 서빙하는 모델이 `bool`/`int` 필드를 문자열로 반환해 스키마 검증에서 400으로 거부되거나, 자유 서술 필드(판단 근거) 생성 중 의미 없는 텍스트를 반복하는 루프에 빠져 함수 호출 자체를 완성 못 하는 경우가 있었다. 둘 다 메시지 분석 전체가 예외로 실패해 알림이 조용히 유실됐다.

**해결**:
- 타입 불일치는 `bool`/`int` 대신 모델이 실제로 내는 형식과 일치하는 문자열 스키마(`Literal["true","false"]`, `str`)로 선언하고, 파싱을 애플리케이션 레이어로 옮겼다.
- 반복 생성 루프는 자유 서술 필드 요구사항을 "상세한 Chain-of-Thought"에서 "1~2문장"으로 줄여 루프가 시작될 표면적 자체를 줄이고, 재시도 시 `temperature`를 0→0.4로 올려 동일 입력에 대한 결정적 반복 경로를 회피하게 했다.

파일: [app/pipelines/realtime_graph.py](app/pipelines/realtime_graph.py)

### 실패 격리 + 자동 재처리로 메시지 유실 방지

**문제**: 웹훅 저장과 AI 분류를 한 트랜잭션으로 묶으면, 분류 단계의 실패가 이미 들어온 원본 데이터 저장까지 롤백시켜버린다.

**해결**: raw_event 저장과 파이프라인 실행을 별도 호출로 분리하고, `raw_event.status`(pending/failed/completed)로 처리 상태를 추적한다. 실패 건은 원본 payload로부터 provider별로 `NotificationEvent`를 재조립해 백그라운드 스케줄러가 주기적으로 재투입한다. 동일 이벤트의 동시 처리는 유니크 인덱스 + Redis 락(TTL 60초)으로 막는다.

파일: [app/pipelines/dispatch.py](app/pipelines/dispatch.py), [app/pipelines/retry.py](app/pipelines/retry.py), [app/core/scheduler.py](app/core/scheduler.py)

### 메모리 제약 환경에서의 의식적인 트레이드오프

**문제**: 웹서버, Discord 게이트웨이 상시 연결, 백그라운드 스케줄러 2개, AI 파이프라인이 512MB 단일 Render 인스턴스에 함께 올라가 있어, 로컬 torch 기반 Cross-Encoder 재랭커까지 얹으면 OOM으로 죽는다.

**해결**: 재랭커를 임시 비활성화하고 RRF(하이브리드 검색) 순위로 폴백하도록 처리했다. LangGraph `MemorySaver`가 메시지마다 만료 없이 체크포인트를 무제한 누적해 일으키던 메모리 누수도 제거했다. 근본적으로는 AI 파이프라인/게이트웨이를 별도 워커로 분리해야 하지만, 데모 단계에서는 이 트레이드오프를 의식적으로 감수하고 구조 개선은 보류했다.

파일: [app/pipelines/shared/retriever_utils.py](app/pipelines/shared/retriever_utils.py)

### Provider 정규화 계층으로 Slack/Discord 통합 처리

**문제**: Slack과 Discord는 이벤트 payload 구조, 인증 방식, 실시간 수신 방식(웹훅 vs 상시 게이트웨이 연결)이 전부 다르다.

**해결**: 각 provider의 이벤트를 공통 `NotificationEvent` DTO로 정규화하는 계층을 두어, 이후 파이프라인은 provider를 몰라도 되게 분리했다. 재처리 시에도 원본 payload를 provider별로 다시 재조립하는 로직을 캡슐화해뒀다.

파일: [app/integrations/slack/normalizer.py](app/integrations/slack/normalizer.py), [app/integrations/discord/normalizer.py](app/integrations/discord/normalizer.py), [app/pipelines/retry.py](app/pipelines/retry.py)

### Google Calendar 연동 — 자체 DB 대신 프록시로 단순화

**문제**: 알림 기반 일정 등록과 별개로 유저가 직접 만드는 일정 CRUD가 필요했다. 처음엔 자체 `calendar_events` 테이블을 만들어 Google Calendar와 동기화하는 설계를 검토했지만, 동기화 실패 시 상태 불일치가 생기는 문제가 있었다.

**해결**: 이미 Google Calendar 연동(OAuth, 토큰 리프레시)이 있다는 점에 착안해 자체 DB 테이블 없이 Google Calendar API를 그대로 CRUD 프록시하는 방식으로 단순화했다 — 동기화 불일치 문제 자체가 발생하지 않는다. 토큰 리프레시 시 Google 응답에 `refresh_token`이 보통 빠져있는 함정을 방지하기 위해 기존 값을 보존하는 로직도 별도로 구현했다.

파일: [app/api/endpoints/calendar_events.py](app/api/endpoints/calendar_events.py), [app/integrations/google_calendar/service.py](app/integrations/google_calendar/service.py)

---

## 폴더 구조

```
app/
├── main.py                      # 엔트리포인트: lifespan(마스터 유저 보장/게이트웨이/스케줄러), 라우터 등록
├── api/
│   ├── deps.py                   # 임시 인증 스텁 (X-Cochat-User-Id 헤더 → user_id)
│   └── endpoints/
│       ├── integrations.py       # Slack/Discord/Google Calendar OAuth 연동 관리
│       ├── notifications.py      # 알림 조회/읽음 처리/캘린더 등록
│       ├── streams.py            # SSE 실시간 알림 스트림
│       ├── briefing.py           # 집중 세션 + AI 브리핑
│       └── calendar_events.py    # 자체 일정 CRUD (Google Calendar 프록시)
├── ingress/
│   ├── slack_webhook.py          # Slack Events API 웹훅 수신
│   └── discord_gateway.py        # Discord 게이트웨이 상시 연결
├── integrations/
│   ├── normalizer.py             # 공통 NotificationEvent DTO
│   ├── slack/                    # client / 이벤트 / 정규화 / 대화 동기화
│   ├── discord/                  # client / 이벤트 / 정규화
│   └── google_calendar/          # client + 토큰 조회/리프레시 서비스
├── pipelines/                     # LangGraph 기반 AI 파이프라인
│   ├── realtime_graph.py         # 실시간 메시지 분류 (긴급도/저장가치/일정여부/일정시각)
│   ├── feedback_graph.py         # 유저 피드백 → 오분류 가이드라인 추출
│   ├── memory_gc_graph.py        # Vector DB 가비지 컬렉션
│   ├── summarizer.py             # 집중 세션 브리핑 생성
│   ├── dispatch.py / retry.py    # 파이프라인 실행 + 실패 재처리
│   └── shared/                   # LLM 클라이언트, 임베딩, 하이브리드 검색 유틸
├── models/                        # SQLAlchemy ORM 모델
├── repositories/                  # DB 접근 계층
├── core/
│   ├── config.py                  # 환경변수 설정
│   ├── redis_manager.py           # 단기 기억 버퍼 + 메시지 중복 처리 락
│   └── scheduler.py               # GC/재처리 백그라운드 스케줄러
└── db/                             # 엔진, 세션

alembic/versions/                   # DB 마이그레이션
```

---

## 배포

Render의 단일 Docker 인스턴스(512MB)에 `main` 브랜치 push 시 자동 빌드·배포된다. Pre-Deploy Command(배포 시 커맨드 자동 실행)는 Render 유료 플랜 전용이라, 앱 부팅 시점에 `alembic upgrade head`를 자동 실행하도록 시도했으나 이미 빠듯한 512MB 예산에서 OOM으로 배포가 아예 죽는 것을 확인하고 되돌렸다. 그래서 스키마 변경이 있는 배포 후에는 `alembic upgrade head`를 로컬이나 Render Shell에서 별도로 실행해야 한다.

---

## 시작하기

```bash
git clone https://github.com/oh0227/cochat-for-business-backend.git
cd cochat-for-business-backend

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

docker-compose up -d    # PostgreSQL(pgvector) + Redis 로컬 구동

cp .env.example .env    # 아래 "외부 서비스 연동 설정" 참고해 값 채우기

alembic upgrade head     # 마이그레이션 적용

uvicorn app.main:app --reload
```

`GROQ_API_KEY`(메시지 분류)와 `GOOGLE_API_KEY`(임베딩)만 있으면 서버 자체는 뜨고 Slack/Discord/Google Calendar 없이도 REST API 구조는 확인할 수 있다. 실제로 메시지를 수신하고 캘린더에 등록하는 전체 흐름을 테스트하려면 아래 세 연동을 각각 설정해야 한다.

---

## 외부 서비스 연동 설정

세 연동 모두 OAuth 콜백을 로컬(`http://localhost:8000/...`)로 등록하면 로컬 서버로, Render 배포 주소로 등록하면 배포 서버로 연동된다. `.env.example`의 `*_REDIRECT_URI` 기본값은 로컬 개발 기준(`http://localhost:8000/...`)이니, 배포 서버에 연결하려면 각 플랫폼 설정 화면과 `.env` 양쪽에서 실제 배포 주소로 바꿔야 한다.

### Slack

1. [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. **OAuth & Permissions**에서 두 종류 스코프를 모두 추가한다 — 하나만 추가하면 Slack이 이벤트를 아예 안 보낸다.
   - **Bot Token Scopes**: `app_mentions:read`, `channels:history`, `groups:history`, `im:history`
   - **User Token Scopes**: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`, `users:read`
3. **OAuth & Permissions** → **Redirect URLs**에 콜백 URL 등록 (`.../api/v1/integrations/slack/callback`)
4. **Event Subscriptions**를 켜고 Request URL에 `.../api/v1/webhooks/slack` 등록 (앱이 떠 있어야 Slack의 `url_verification` 챌린지를 통과함 — 로컬 테스트는 ngrok 등으로 공인 URL이 필요). Subscribe to bot events에 `app_mention`, `message.channels`, `message.groups`, `message.im` 추가
5. **Basic Information**에서 `Client ID`/`Client Secret`/`Signing Secret`을 확인해 `.env`에 채운다

```
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
SLACK_SIGNING_SECRET=
SLACK_REDIRECT_URI=http://localhost:8000/api/v1/integrations/slack/callback
SLACK_BOT_TOKEN=          # 워크스페이스에 앱 설치 후 발급되는 xoxb- 토큰
```

### Discord

1. [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. **Bot** 탭에서 봇 생성 후, **Privileged Gateway Intents**의 **Message Content Intent**를 반드시 켠다 (꺼져 있으면 메시지 본문을 아예 못 받는다 — `app/ingress/discord_gateway.py`가 이 인텐트로 실시간 게이트웨이에 상시 연결한다)
3. **OAuth2 → General**에서 Redirect에 콜백 URL 등록 (`.../api/v1/integrations/discord/callback`)
4. Bot 토큰(**Bot** 탭)과 Client ID/Secret(**OAuth2 → General**)을 `.env`에 채운다. Bot 권한은 서버 메시지 조회에 필요한 `VIEW_CHANNEL + READ_MESSAGE_HISTORY`(permission integer `66560`)로 고정돼 있어 별도 설정은 필요 없다

```
DISCORD_BOT_TOKEN=
DISCORD_APPLICATION_ID=
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=http://localhost:8000/api/v1/integrations/discord/callback
```

5. 연동 플로우(`GET /api/v1/integrations/discord/oauth-url`)는 유저가 봇을 자신의 서버에 초대하는 "봇 설치" 링크를 발급한다 — 일반 로그인용 OAuth가 아니라 서버별 봇 설치라는 점에 유의

### Google Calendar

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트 생성 → **APIs & Services → Library**에서 **Google Calendar API** 활성화
2. **APIs & Services → Credentials**에서 OAuth 2.0 클라이언트 ID(웹 애플리케이션) 생성, 승인된 리디렉션 URI에 콜백 URL 등록 (`.../api/v1/integrations/google-calendar/callback`)
3. **OAuth consent screen**에서 스코프에 `calendar.events`, `openid`, `email` 추가 (테스트 단계면 테스트 사용자로 본인 계정 등록)
4. Client ID/Secret을 `.env`에 채운다 — refresh token을 매번 확실히 받기 위해 서버가 `access_type=offline&prompt=consent`로 요청하므로 별도 설정은 불필요

```
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GOOGLE_CALENDAR_REDIRECT_URI=http://localhost:8000/api/v1/integrations/google-calendar/callback
```

임베딩용 `GOOGLE_API_KEY`(Gemini)와는 별개의 자격 증명이다 — 하나는 [Google AI Studio](https://aistudio.google.com/) API 키, 하나는 GCP OAuth 클라이언트라 발급 경로가 다르다.

---

## 커밋 히스토리

총 175개 커밋. 2026-03-24 첫 커밋 이후 약 4개월째 이어지고 있다.
