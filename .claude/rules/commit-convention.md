# Commit Convention

## 형식

```
<type>: <description>

<optional body>

<optional footer>
```

### type

| type | 의미 |
|------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| refactor | 동작 변화 없는 코드 구조 개선 |
| docs | 문서 변경 |
| test | 테스트 추가/수정 |
| chore | 빌드, 설정, 의존성 등 잡무 |
| perf | 성능 개선 |
| ci | CI/CD 설정 변경 |

### description

- 한글 사용 가능, 명령형/현재형으로 간결하게
- 끝에 마침표 붙이지 않음

### footer (이슈 참조)

[github-issue-workflow.md](./github-issue-workflow.md) 규칙에 따라 모든 작업은 이슈 번호와 연결되므로, 커밋 footer에 이슈 참조를 남긴다.

- 이슈를 완전히 해결하는 커밋: `closes #번호`
- 이슈와 관련은 있지만 아직 완전히 해결되지 않은 커밋: `refs #번호`

단, `closes #번호`는 GitHub 자동 종료 트리거일 뿐이며, 실제 이슈 종료는 배포 서버에서 사용자가 확인한 뒤 수동으로 처리한다 (github-issue-workflow.md 참고). `closes`를 커밋에 적더라도 PR이 머지되기 전까지는 이슈가 자동으로 닫히지 않는다.

## 예시

```
fix: Discord OAuth 콜백에 user_id 누락 버그 수정

closes #5
```

```
feat: 앱 시작 시 MASTER_USER_ID 유저 자동 생성으로 FK 위반 방지

- 앱 시작 시 마스터 유저가 없으면 자동 생성하도록 수정
- Slack OAuth 콜백에서 발생하던 FK 위반 오류 해결

closes #4
```
