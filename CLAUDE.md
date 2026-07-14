# CoChat Backend — Claude 작업 지침

## 필독: 작업 시작 전

`.claude/` 폴더 안의 규칙을 먼저 읽고 따른다.

```
.claude/
└── rules/
    └── github-issue-workflow.md  # 이슈 등록 → 코드 수정 순서 규칙
```

## 핵심 규칙 요약

- **코드 수정 전 반드시 GitHub 이슈를 먼저 등록한다**
- 기존 이슈와 연관된 문제는 sub-issue로 등록한다
- 모든 작업은 `develop` 브랜치 기준으로 한다
- 이슈 레포: `oh0227/cochat-for-buisness-backend`

자세한 내용은 `.claude/rules/github-issue-workflow.md` 참고.

## 프로젝트 개요

- **스택**: FastAPI + SQLAlchemy (async) + PostgreSQL + LangChain PGVector
- **배포**: Render.com
- **주요 기능**: Slack/Discord 연동, AI 요약 브리핑, Vector DB 기반 메모리 관리
