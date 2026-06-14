# stock_searching — 주식 주목종목 자동 수집·분석 파이프라인

매일 자동으로 "남들보다 먼저 발굴할 주목 종목"을 수집·분석해 **노션(Notion)** 에 저장하는
GitHub Actions 파이프라인입니다. (추후 워드프레스 임시저장까지 확장 예정)

## 전체 그림

| 시각(KST) | 단계 | 하는 일 | 저장 위치 |
|---|---|---|---|
| 07:00 | Reddit 스캔 | 5개 서브레딧 언급량 급증 티커 | 노션 A |
| 16:00 | DART 공시 | 수주/계약/특허 + 시총 대비 계약 10%+ | 노션 B |
| 16:30 | pykrx 수급 | 3일+ 연속 외인·기관 순매수 | 노션 C |
| 17:00 | Claude 분석 | A+B+C 종합 → 한 장 요약 | 노션 D |

> GitHub Actions의 cron은 **UTC 기준**이라 워크플로에는 UTC 시각(KST−9)이 적혀 있습니다.
> 예약 실행은 부하에 따라 수~수십 분 지연되거나 드물게 누락될 수 있습니다.
> 또한 **레포에 60일간 활동이 없으면 예약 실행이 자동 비활성화**됩니다(필요 시 수동 재활성화).

## 진행 상황

- [x] **Step 1 — 공유 토대 + 연결 테스트** (이 단계)
- [ ] Step 2 — pykrx 수급 스캔
- [ ] Step 3 — DART 공시 수집
- [ ] Step 4 — Reddit 스캔
- [ ] Step 5 — Claude 종합 분석
- [ ] Step 6 — (선택) WordPress 임시저장

---

## Step 1 사용법 (연결 테스트)

GitHub에 비밀값(Secrets)을 넣고, 테스트 워크플로를 수동 실행하면
**노션에 테스트 페이지가 생기고 텔레그램으로 성공 알림**이 옵니다.

### 1) 노션 준비
1. https://www.notion.so/my-integrations 에서 **New integration** 생성 → **Internal Integration Secret**(= `NOTION_TOKEN`) 복사.
2. 테스트용 **데이터베이스(표)** 를 하나 만든다(예: "연결테스트"). 표에는 기본 제목(Name) 열만 있어도 됩니다.
3. 그 데이터베이스 페이지 우측 상단 **··· → Connections → (방금 만든 integration) 추가** (권한 부여, 중요!).
4. 데이터베이스 URL에서 **DB id** 복사 → `NOTION_TEST_DB`.
   - URL 예: `notion.so/...workspace/`**`32자리영숫자`**`?v=...` 의 32자리 부분.

### 2) 텔레그램 준비 (무료, 5분)
1. 텔레그램에서 **@BotFather** 와 대화 → `/newbot` → 봇 토큰(= `TELEGRAM_BOT_TOKEN`) 받기.
2. 방금 만든 봇과 대화창을 열고 아무 메시지나 한 번 보낸다.
3. 브라우저에서 `https://api.telegram.org/bot<봇토큰>/getUpdates` 열기 → 결과의 `chat":{"id": 숫자` 가 `TELEGRAM_CHAT_ID`.
   - (텔레그램을 아직 안 만들었다면 비워둬도 됩니다 — 노션 테스트는 그대로 동작하고 알림만 건너뜁니다.)

### 3) GitHub Secrets 등록
레포 **Settings → Secrets and variables → Actions → New repository secret** 에서 아래를 추가:

| 이름 | 값 |
|---|---|
| `NOTION_TOKEN` | 노션 integration secret |
| `NOTION_TEST_DB` | 테스트 데이터베이스 id |
| `TELEGRAM_BOT_TOKEN` | (선택) 봇 토큰 |
| `TELEGRAM_CHAT_ID` | (선택) chat id |

### 4) 실행
레포 **Actions 탭 → 왼쪽에서 `test_connection` 선택 → Run workflow** 클릭.

**성공 시:** 노션 테스트 DB에 `🔧 연결 테스트 ...` 페이지가 생기고, 텔레그램에 ✅ 알림 도착.
**실패 시:** Actions 로그가 빨간불이 되고, 어떤 키/단계에서 막혔는지 로그와 텔레그램(설정된 경우)에 표시됩니다.

---

## 폴더 구조
```
scripts/
  common/
    config.py          # Secrets 로드/검증
    logger.py          # 로그 형식
    notify.py          # 텔레그램 알림
    notion_client.py   # 노션 쓰기 헬퍼
  test_connection.py   # Step 1 연결 테스트
.github/workflows/
  test_connection.yml  # 수동 실행 워크플로
requirements.txt
```
