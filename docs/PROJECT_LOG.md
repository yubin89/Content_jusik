# 📒 프로젝트 위키 / 히스토리

> 이 문서는 **이 프로젝트의 모든 것을 한눈에** 보기 위한 자체 참고서입니다.
> 무엇을 왜 만들고, 각 단계 기준이 무엇이며, 지금까지 무엇이 바뀌었는지 기록합니다.
> **매 단계가 끝날 때마다 갱신**됩니다. (마지막 갱신: 2026-06-14)

---

## 1. 프로젝트 개요 (무엇을 · 왜)

매일 자동으로 **"남들보다 먼저 발굴할 주목 종목"** 을 수집·분석해서
**노션(Notion)** 에 요약 저장하는 파이프라인. (추후 워드프레스 임시저장까지 확장 예정)

- 실행 엔진: **GitHub Actions** (정해진 시각에 자동 실행되는 무료 로봇)
- 저장소: 노션 데이터베이스 A / B / C / D
- 알림: 텔레그램 (성공 요약 + 실패 시 에러 알림)
- 개발 브랜치: `claude/quirky-carson-w5fi1d`

---

## 2. 전체 파이프라인 (한눈에)

| 시각(KST) | 단계 | 하는 일 | 저장 위치 | 필요한 키 |
|---|---|---|---|---|
| 07:00 | Reddit 스캔 | 5개 서브레딧 언급량 급증 티커 | 노션 **A** | Reddit API |
| 16:00 | DART 공시 | 수주/계약/특허 + 시총대비 계약 10%+ | 노션 **B** | DART 키 |
| 16:30 | pykrx 수급 | 3거래일 연속 외인·기관 동시 순매수 | 노션 **C** | (없음) |
| 17:00 | Claude 분석 | A+B+C 종합 → 한 장 요약 | 노션 **D** | Claude API |

> GitHub Actions의 예약(cron)은 **UTC 기준**이라 워크플로 파일엔 UTC 시각(KST−9)이 적혀 있음.
> 예약 실행은 수~수십 분 지연되거나 드물게 누락될 수 있고, 레포가 60일간 활동 없으면 자동 비활성화됨.

---

## 3. 용어 · 기준 사전 (📌 "C-수급이 뭐고 어떤 기준?")

### 노션 페이지 A/B/C/D 의미
- **A (Reddit)**: 미국 레딧에서 갑자기 언급이 폭증한 종목 (전일 대비 +300%)
- **B (DART)**: 한국 공시 중 호재성 계약/수주/특허, 그것도 회사 규모(시총) 대비 큰 건
- **C (수급)**: 외국인·기관(=스마트머니)이 **며칠 연속 사 모으는** 한국 종목
- **D (요약)**: 위 A+B+C를 Claude가 읽고 중요도순으로 정리한 **하루치 한 장 요약**

### Step 2 "C-수급" 판정 기준 (현재 설정)
- **대상**: 코스피 + 코스닥 **시가총액 상위 200종목**
- **조건**: **최근 3거래일** 동안 **매일** `외국인합계 > 0` **그리고** `기관합계 > 0`
  (즉, 외국인과 기관이 둘 다 3일 내내 순매수)
- **정렬**: 연속일수(streak)가 긴 순 → 매수금액 합이 큰 순
- **출력**: `종목명(코드) — 연속 N일, 외인 +XX억, 기관 +XX억`
- **데이터 출처**: `pykrx` (한국거래소 공개데이터, API 키 불필요)
- **왜 이 기준?**: 수급(누가 사는지)은 가격보다 먼저 움직이는 선행신호. 개인이 아니라
  외인·기관이 "함께·연속" 들어오면 의미 있는 매집으로 해석.

---

## 4. 진행 상황 (체크리스트)

- [x] **Step 1 — 공유 토대 + 연결 테스트** ✅ (2026-06-14 완료)
  - GitHub Actions → 노션 쓰기 → 텔레그램 알림 전 구간 검증 완료
- [x] **Step 2 — pykrx 수급 스캔 (코드)** ✅ 코드 완료·푸시 (2026-06-14)
  - [ ] 사용자 테스트 대기: 노션 `C-수급` 표 생성 + `NOTION_DB_C` 시크릿 + 수동 실행
- [ ] **Step 3 — DART 공시 수집 (노션 B)** — DART 무료 키 필요
- [ ] **Step 4 — Reddit 스캔 (노션 A)** — Reddit 키 필요, 스냅샷 누적 방식
- [ ] **Step 5 — Claude 종합 분석 (노션 D)** — Claude 키는 여기서만 사용
- [ ] **Step 6 — (선택) WordPress 임시저장**

---

## 5. 변경 이력 (Changelog)

### 2026-06-14
- **Step 1 구현**: `scripts/common/`(config·logger·notify·notion_client) + `test_connection` 워크플로 + README.
- **버그 수정**: 첫 실행 시 `KeyError: 'properties'` 발생 → 원인은 `notion-client` 최신 API가
  DB를 'data sources' 구조로 반환. **해결**: 노션 클라이언트를 `notion_version="2022-06-28"` 로 고정.
- **텔레그램 연동**: 봇 생성·시크릿 등록 후 알림 정상 동작 확인.
- **Step 1 검증 완료**: 노션 테스트 행 생성 + 텔레그램 ✅ 알림 확인.
- **Step 2 구현**: `scripts/pykrx_scan.py` + `pykrx_scan.yml` + requirements에 `pykrx` 추가.
  가짜 데이터로 판정·정렬·금액변환 단위검증 통과. (실제 KRX 검증은 사용자 실행 예정)

---

## 6. Secrets 목록 (어떤 키가 어디 쓰이나)

> 위치: GitHub 레포 `Settings → Secrets and variables → Actions`

| 이름 | 용도 | 상태 |
|---|---|---|
| `NOTION_TOKEN` | 노션 쓰기 공통 토큰 | ✅ 등록됨 |
| `NOTION_TEST_DB` | Step 1 연결테스트 표 | ✅ 등록됨 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 | ✅ 등록됨 |
| `TELEGRAM_CHAT_ID` | 텔레그램 수신 대상 | ✅ 등록됨 |
| `NOTION_DB_C` | Step 2 수급 표 "C-수급" | ⏳ 사용자 등록 예정 |
| `NOTION_DB_B` | Step 3 DART 표 | ⏳ 예정 |
| `DART_API_KEY` | Step 3 DART 공시 | ⏳ 예정 |
| `NOTION_DB_A` | Step 4 Reddit 표 | ⏳ 예정 |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Step 4 Reddit | ⏳ 예정 |
| `NOTION_DB_D` / `ANTHROPIC_API_KEY` | Step 5 Claude 요약 | ⏳ 예정 |

---

## 7. 나중에 바꾸고 싶을 때 (조정 가능한 값)

| 무엇 | 현재값 | 파일 위치 |
|---|---|---|
| 수급: 대상 종목 수 | `TOP_N = 200` | `scripts/pykrx_scan.py` 상단 |
| 수급: 연속 순매수 일수 | `CONSECUTIVE_DAYS = 3` | `scripts/pykrx_scan.py` 상단 |
| 수급: 조회 구간(달력일) | `LOOKBACK_DAYS = 15` | `scripts/pykrx_scan.py` 상단 |
| 실행 시각 | cron `"30 7 * * *"` (=16:30 KST) | `.github/workflows/pykrx_scan.yml` |
| Claude 모델 | (예정) `claude-sonnet-4-6` 권장 | Step 5에서 설정 |

> 값을 바꾸고 싶으면 나에게 "TOP_N을 100으로 줄여줘"처럼 말하면 해당 파일만 고쳐 푸시함.

---

## 8. 운영 · 주의사항
- 각 단계 워크플로는 **독립 실행** — 하나 실패해도 나머지는 정상 동작.
- 실패하면 **Actions 빨간불 + 텔레그램 에러 알림(트레이스백 포함)** 으로 어디서 깨졌는지 표시.
- 휴장일/주말엔 "가장 최근 거래일" 데이터를 사용하거나 "데이터 없음"으로 정상 처리(실패 아님).
- 키는 **코드에 절대 저장하지 않고** GitHub Secrets에만 보관.

---

## 9. 파일 구조
```
scripts/
  common/
    config.py          # Secrets 로드/검증
    logger.py          # 로그 형식
    notify.py          # 텔레그램 알림
    notion_client.py   # 노션 쓰기 헬퍼 (API 버전 2022-06-28 고정)
  test_connection.py   # Step 1 연결 테스트
  pykrx_scan.py        # Step 2 수급 스캔
.github/workflows/
  test_connection.yml  # 수동 실행
  pykrx_scan.yml       # 16:30 KST 자동 + 수동
docs/
  PROJECT_LOG.md       # ← 이 문서
requirements.txt
README.md
```
