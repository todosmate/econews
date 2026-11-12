# econews 프로젝트 계획서 (PROJECT.md)

> 이 문서는 작업 기준 문서입니다. 새 세션에서는 이 파일을 먼저 읽고 "진행 현황" 체크리스트에서 이어서 작업합니다.
> 작업을 마칠 때마다 체크리스트와 "결정 사항"을 갱신합니다.

## 1. 목표
네이버 블로그 여러 개의 새 글을 모아 **요약·정리한 "신문" 페이지**를 하루 2회 자동 발행한다.

- **조간**: 매일 08:00 KST 발행. 전날 18:00 ~ 당일 08:00 사이 등록된 글
- **석간**: 매일 18:00 KST 발행. 당일 08:00 ~ 18:00 사이 등록된 글

## 2. 확정된 조건
| 항목 | 내용 |
|---|---|
| 블로그 | 네이버 블로그 2개 (추후 증가 가능) |
| 글 수 | 하루 총 6~7개 수준 |
| 호스팅 | GitHub (저장소 + GitHub Pages) |
| 수집 | 네이버 블로그 RSS (`https://rss.blog.naver.com/<블로그ID>.xml`) |
| 요약 | Claude API (API 키는 GitHub Secrets에 보관) |
| 서버/DB | 없음. 결과는 JSON 파일로 저장소에 커밋 |

## 3. 미확정 사항 (사용자에게 확인 필요)
- [ ] 블로그 2개의 네이버 블로그 ID(주소)
- [ ] 공개 사이트 여부 (무료 Pages는 공개 저장소만 가능. 비공개 원하면 대안 필요)
- [ ] GitHub 저장소 이름 / 계정명
- [ ] 요약 언어·분량 (기본안: 글당 3~5줄 한국어 + 호 전체 총평 3줄)

## 4. 아키텍처
```
GitHub Actions (cron: 08:00 / 18:00 KST)
   ↓
scripts/fetch.py   : blogs.json의 RSS 수집 → 해당 구간(window) 글만 필터
scripts/summarize.py: Claude API로 글별 요약 + 호 총평
   ↓
data/issues/YYYY-MM-DD-am.json, YYYY-MM-DD-pm.json 저장 + data/index.json 갱신 → 커밋
   ↓
GitHub Pages: docs/ (정적 HTML/JS)가 data/*.json을 읽어 신문 형태로 표시
```

### 시간대 처리
- Actions cron은 UTC 기준: 조간 `0 23 * * *`(=08:00 KST), 석간 `0 9 * * *`(=18:00 KST)
- 스케줄은 수 분~수십 분 지연될 수 있으므로 **실행 시각이 아니라 고정 구간(전날18:00~08:00 / 08:00~18:00 KST)을 계산해 필터링**한다.
- 중복 방지: 이미 발행된 호 파일이 있으면 건너뛴다. 글 URL(logNo) 기준으로 중복 제거.
- 수동 실행(`workflow_dispatch`)으로 특정 날짜·조/석간 재생성 가능하게 한다.

## 5. 저장소 구조 (예정)
```
econews/
├─ PROJECT.md              # 이 문서
├─ blogs.json              # 블로그 목록 (id, 이름) — 블로그 추가는 여기만 수정
├─ scripts/
│  ├─ fetch.py
│  ├─ summarize.py
│  └─ build_issue.py       # 위 둘을 묶어 호 JSON 생성
├─ data/
│  ├─ index.json           # 발행된 호 목록
│  └─ issues/*.json
├─ docs/                   # GitHub Pages 루트
│  ├─ index.html           # 최신 호
│  ├─ archive.html         # 지난 호 목록
│  └─ assets/ (css, js)
└─ .github/workflows/
   └─ publish.yml
```

### blogs.json 예시
```json
[
  { "id": "네이버블로그ID1", "name": "블로그 이름 1" },
  { "id": "네이버블로그ID2", "name": "블로그 이름 2" }
]
```

### 호(issue) JSON 예시
```json
{
  "date": "2026-11-11",
  "edition": "am",
  "window": { "from": "2026-11-10T18:00:00+09:00", "to": "2026-11-11T08:00:00+09:00" },
  "overview": "이번 호 총평...",
  "articles": [
    { "blog": "블로그 이름 1", "title": "...", "url": "...", "published": "...", "summary": "..." }
  ]
}
```

## 6. 화면 구성
- **메인**: 최신 호 — 제목(날짜·조간/석간), 총평, 블로그별 요약 카드, 원문 링크
- **아카이브**: 지난 호 목록
- (선택) 블로그별 필터, 키워드 검색

## 7. 주의 사항
- 네이버 RSS는 본문이 일부만 포함될 수 있음. 부족하면 글 URL 본문 가져오기(모바일 `m.blog.naver.com`)를 보조로 사용 — 필요할 때 결정.
- 타인 블로그 요약을 공개할 경우 원문 전문 게재 금지. 짧은 요약 + 원문 링크만.
- API 키 등 비밀값은 코드/저장소에 넣지 말고 GitHub Secrets(`ANTHROPIC_API_KEY`) 사용.
- 새 글이 없는 호는 "새 글 없음"으로 발행하거나 건너뛴다 (기본안: 건너뜀, 추후 결정).
- 요약 모델은 비용 대비 가벼운 모델(Haiku급)로 시작, 품질 부족 시 상향.

## 8. 진행 현황
- [x] 방향 결정: GitHub Actions + Pages + Claude API
- [x] 계획 문서 작성 (PROJECT.md)
- [x] 블로그 확정: `ranto28`(메르의 블로그), `red_annie`(그냥 아지매) → blogs.json
- [x] 코드 작성: scripts/build_issue.py (수집+필터+본문+요약+저장 통합), docs/index.html, .github/workflows/publish.yml
- [x] git / gh 설치 (git: `C:\Program Files\Git\cmd`, gh: `%LOCALAPPDATA%\Programs\gh\bin` — PATH 미등록이라 세션마다 `$env:Path` 추가 필요). python은 로컬에 없음(Actions에서만 실행)
- [x] 저장소 https://github.com/todosmate/econews (공개) 생성 및 push, gh 로그인(workflow 권한 포함)
- [x] Pages 설정 완료: main / docs → https://todosmate.github.io/econews/
- [x] Actions 수동 실행으로 수집·필터·JSON 생성·커밋 동작 확인 (API 키 없이 발췌문 요약 상태)
- [ ] **Settings → Secrets and variables → Actions → `ANTHROPIC_API_KEY` 등록** (미완료: 현재 요약이 발췌문으로 대체됨)
- [ ] 키 등록 후 `gh workflow run publish-issue -f edition=am -f force=true` 로 Claude 요약 검증
- [ ] 스케줄 동작 확인 (조간/석간 각 1회)
- [ ] (추후) 블로그 추가는 blogs.json에 한 줄 추가

## 8-1. 구현 메모
- 데이터 위치는 Pages 루트가 docs/ 이므로 `docs/data/` (issues/*.json, index.json). 5절의 data/ 는 이 경로로 대체됨.
- 본문은 `blog.naver.com/PostView.naver?blogId=..&logNo=..` 에서 `.se-main-container` 를 파싱 (RSS description은 발췌문 ~400자뿐). 실패 시 발췌문으로 대체. 로컬(PowerShell)로 접근 가능함을 확인함.
- 요약 모델 기본값 `claude-haiku-5-5` (환경변수 `ECONEWS_MODEL`로 변경). 키가 없으면 발췌문으로 대체.
- 호 자동 판별: 실행 시각(KST) 13시 이전 = 조간, 이후 = 석간. 새 글이 없으면 호를 만들지 않음.
- 로컬 실행: `pip install -r requirements.txt` 후 `python scripts/build_issue.py --date YYYY-MM-DD --edition am --force`
- 코드는 아직 한 번도 실행해보지 못함 (로컬에 python 없음) → 첫 Actions 실행 로그로 검증 필요.

## 9. 결정 기록
- 2026-11-11: DB 없이 JSON 파일 + 정적 사이트 방식 채택. 글이 하루 6~7개로 소량이라 충분.
- 2026-11-11: ranto28 글은 본문의 "한줄 코멘트" 문단을 원문 그대로 `comment` 필드로 추출해 표시 (blogs.json `comment_marker`). 동작 확인됨.
- 2026-11-11: API 키 없는 요약으로 GitHub Models를 시도했으나 **2026-07-30에 서비스 종료**되어 사용 불가 (엔드포인트가 "OK"만 반환). github 경로 코드는 제거함.
- 2026-11-11: 비교용으로 **추출 요약(키 불필요) + Gemini 무료 등급(`GEMINI_API_KEY` Secret)** 을 동시에 생성해 `summaries`/`overviews`에 저장, 화면 상단 버튼으로 전환. Claude 키가 있으면 claude도 추가됨. 대표 요약 우선순위 claude > gemini > extractive.
- 2026-11-11: 요약 분량을 본문 길이에 비례하게 변경 (본문의 약 15%, 250~1500자, 3~15문장 — `target_length()`).
- Gemini 모델은 ListModels로 Flash → Flash-Lite 순 자동 선택(무료 일일 한도 소진 시 다음 모델). `GEMINI_MODEL` 환경변수로 고정 가능.
- 2026-11-11: **최종 결정 — 요약은 Gemini.** 단, 요약은 blogs.json에 `"summarize": true` 인 블로그만 적용 (현재 ranto28만).
  나머지 블로그(및 앞으로 추가되는 블로그 기본값)는 요약 없이 본문 앞 4~5줄(최대 400자)을 `preview`로 표시.
  Gemini 실패 시에만 추출 요약으로 대체(`summary_by: "extractive"`, 화면에 "핵심 문장 발췌" 표시). 비교 탭·Claude 경로·anthropic 의존성 제거.
  총평(overview)은 Gemini 요약 글이 2건 이상일 때만 생성.

- 2026-11-11: 요약 분량을 **본문의 1/3~1/2**로 변경 (짧은 요약은 비약이 많아 의미 없음 — 사용자 요청). 모델 입력 본문 최대 30,000자.
- 2026-11-11: 한줄 코멘트는 **원문 그대로 발췌** — 마커 문단부터 빈 줄 전까지, "한줄 코멘트." 문구 포함, 취소선은 `~~ ~~`로 보존해 화면에 <s>로 표시.
  본문 파싱은 `<p>` 문단 단위로 span 조각을 합침 (메르 님이 문장 중간에 글자색을 바꿔 코멘트가 잘리던 문제 수정).
- 참고: Gemini Flash 모델이 자주 503(과부하)을 반환 → 30초 뒤 1회 재시도 후 다음 모델(Flash-Lite까지)로 넘어감.

- 2026-11-11: 일반 블로그 4개 추가(11-12 조간부터) — pillion21(알바트로스의 파생 이야기), pjws3(주부이야기), pokara61(포카라의 실전투자), gentlenavy(젠네의 블로그). 현재 총 6개, 요약은 ranto28만.

- 2026-11-11: 휴대폰 지원 — PWA(docs/manifest.webmanifest, docs/sw.js 네트워크 우선, docs/icons/*.png, 홈 화면 추가 시 전체 화면).
  발행 알림은 ntfy.sh 사용: Secret `NTFY_TOPIC`(값은 공개 저장소에 기록하지 않음, 사용자에게만 전달). build_issue.py가 notify.json 생성 →
  워크플로가 Pages 반영 90초 대기 후 전송. 알림 클릭 시 해당 호로 이동. 전송 동작 확인 완료.

- 2026-11-12: 화면 디자인을 **종이 신문 스타일**로 변경 (docs/index.html만 교체, 데이터 형식·스크립트 변경 없음).
  큰 제호(ECONEWS) + 날짜/조간·석간 띠 + 예고 띠(그 호의 다른 글 2~3건) + 지난 호 INDEX 박스. 흑백(종이색/먹색), 다크 모드 지원.
  글 목록은 기존처럼 세로 리스트 유지, 본문은 화면 크기와 무관하게 **항상 1단** (사용자 요청). 글꼴은 Google Fonts(Abril Fatface, Black Han Sans, Noto Serif KR).

## 10. blogs.json 설정 방법 (현행)
```json
{ "id": "네이버ID", "name": "표시 이름", "summarize": true, "comment_marker": "한줄 코멘트" }
```
- `summarize` 생략 = 요약 안 함(미리보기 4~5줄). 사용자가 요약을 요청한 블로그만 `true`.
- `comment_marker` 지정 시 본문에서 해당 문구로 시작하는 마지막 문단을 원문 그대로 `comment`로 표시.
- 호 JSON의 글 필드: `summary`+`summary_by` (요약 블로그) 또는 `preview` (그 외), `comment`, `length`(본문 글자 수).
