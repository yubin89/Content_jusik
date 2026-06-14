# 📒 프로젝트 위키 / 히스토리

> 이 문서는 **이 프로젝트의 모든 것을 한눈에** 보기 위한 자체 참고서입니다.
> 무엇을 왜 만들고, 각 단계 기준이 무엇이며, 지금까지 무엇이 바뀌었는지 기록합니다.
> **매 단계가 끝날 때마다 갱신**됩니다. (마지막 갱신: 2026-06-14, Step 3 구현)

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
| 16:30 | pykrx 수급 | 3거래일 연속 기관 순매수(외인 동반 가점) | 노션 **C** | KRX 무료계정 |
| 17:00 | Claude 분석 | A+B+C 종합 → 한 장 요약 | 노션 **D** | Claude API |

> GitHub Actions의 예약(cron)은 **UTC 기준**이라 워크플로 파일엔 UTC 시각(KST−9)이 적혀 있음.
> 예약 실행은 수~수십 분 지연되거나 드물게 누락될 수 있고, 레포가 60일간 활동 없으면 자동 비활성화됨.

---

## 3. 용어 · 기준 사전 (📌 "C-수급이 뭐고 어떤 기준?")

### 노션 페이지 A/B/C/D 의미
- **A (Reddit)**: 미국 레딧에서 갑자기 언급이 폭증한 종목 (전일 대비 +300%)
- **B (DART)**: 한국 공시 중 호재성 계약/수주/특허, 그것도 회사 규모(시총) 대비 큰 건
- **C (수급)**: **기관**(+외인 동반)이 **며칠 연속 사 모으는** 한국 종목
- **D (요약)**: 위 A+B+C를 Claude가 읽고 중요도순으로 정리한 **하루치 한 장 요약**

### Step 2 "C-수급" 판정 기준 (현재 설정)
- **대상**: 코스피 + 코스닥 **시가총액 상위 200종목**
- **조건 (기관 중심)**: **최근 3거래일** 동안 **매일 `기관합계 > 0`** (기관 연속 순매수, 필수)
  - **외국인 동반**: 같은 기간 외인도 순매수면 `외인 동반` 태그 + 정렬 가점 (없어도 기관만으로 채택)
  - 이력: '둘 다 각각' → '합산' → (2026-06-14) **'기관 필수 + 외인 동반 가점'**. (외국인 매수는 패시브·차익거래 등 비방향성 노이즈가 많아 기관에 더 무게)
- **정렬**: 연속일수(streak)가 긴 순 → 매수금액 합이 큰 순
- **출력**: `종목명(코드) — 연속 N일, 외인 +XX억, 기관 +XX억`
- **데이터 출처**: `pykrx` (한국거래소 공개데이터, API 키 불필요)
- **왜 이 기준?**: 수급(누가 사는지)은 가격보다 먼저 움직이는 선행신호. 개인이 아니라
  외인·기관이 "함께·연속" 들어오면 의미 있는 매집으로 해석.

### Step 2 추가 지표 (수급 통과 종목에만 계산)
- **거래량 급증**: 최근일 거래량 ≥ 최근 20거래일 평균 ×`VOL_SURGE_MULT`(기본 **1.5배**) → 관심·자금 유입 신호
- **52주 신고가권**: 최근 종가 ≥ 252거래일 최고 종가 ×`HIGH_NEAR_RATIO`(기본 **0.90**) → 추세 돌파 신호
- **🔥 3중 신호**: 수급 + 거래량 급증 + 신고가권 **둘 다** 충족
- **⭐ 2중 신호**: 수급 + (거래량 또는 신고가 중 **1개**)
- 노션 C 페이지 구성: ① 🔥3중, ② ⭐2중, ③ 전체 수급 종목(각 종목에 `거래량 N배 / 신고가` 태그). 텔레그램엔 3중·2중 개수 + 상위 종목.

---

## 4. 진행 상황 (체크리스트)

- [x] **Step 1 — 공유 토대 + 연결 테스트** ✅ (2026-06-14 완료)
  - GitHub Actions → 노션 쓰기 → 텔레그램 알림 전 구간 검증 완료
- [x] **Step 2 — pykrx 수급 스캔** ✅ 완료·검증 (2026-06-14)
  - KRX 로그인 → 200종목 스캔 → 노션 C 저장 → 텔레그램 알림 전 구간 정상 확인
  - 기준을 '외인·기관 합산 순매수'로 완화 (첫 검증 0종목 → 완화). KRX 로그인 필수(아래 5번 참고)
- [ ] **Step 3 — DART 공시 수집 (노션 B)** ⏳ 구현 완료, 사용자 시크릿 등록 + 검증 대기
  - `scripts/dart_scan.py` + `.github/workflows/dart_scan.yml` 작성 완료
  - 가짜 데이터 단위 검증 통과 (금액 파싱 / 키워드 매칭 / 정렬 / 불릿 포맷)
  - 실제 실행 전 사용자 준비 필요: `DART_API_KEY`, `NOTION_DB_B` 시크릿 등록
- [ ] **Step 4 — Reddit 스캔 (노션 A)** — Reddit 키 필요, 스냅샷 누적 방식
- [ ] **Step 5 — Claude 종합 분석 (노션 D)** — Claude 키는 여기서만 사용
- [ ] **Step 6 — (선택) WordPress 임시저장**

---

## 5. 변경 이력 (Changelog)

### 2026-06-14 (Step 3)
- **Step 3 구현**: `scripts/dart_scan.py` + `.github/workflows/dart_scan.yml`
  - DART `/list.json` (주요사항보고 B + 기타공시 E) 페이지네이션 + `rcept_no` 중복 제거
  - 상장사 필터(`stock_code` 존재), 키워드 필터(수주·계약체결·특허·기술이전)
  - 수주·계약 타입 → `/order.json` / `/cntrwk.json` 으로 금액 조회 → 시총 대비 10%+ 필터
  - 금액 미파악(특허·기술이전 등)은 비율 필터 미적용, 목록에 "금액 미파악" 태그로 포함
  - 노션 B: 🎯대형계약 섹션 + 전체 공시 섹션. 텔레그램 성공 알림
  - 시총 캐시: pykrx KOSPI+KOSDAQ 한 번에 로드(KRX 로그인 필요, 기존 시크릿 재사용)
  - 가짜 데이터 단위 테스트 통과(금액 파싱 / 키워드 매칭 / 정렬 / 불릿 포맷)
- **Step 3 사용자 준비 필요**: `DART_API_KEY`(opendart.fss.or.kr 무료 발급) + `NOTION_DB_B` 등록 후 Actions 수동 실행으로 검증

### 2026-06-14 (Step 2)
- **Step 1 구현**: `scripts/common/`(config·logger·notify·notion_client) + `test_connection` 워크플로 + README.
- **버그 수정**: 첫 실행 시 `KeyError: 'properties'` 발생 → 원인은 `notion-client` 최신 API가
  DB를 'data sources' 구조로 반환. **해결**: 노션 클라이언트를 `notion_version="2022-06-28"` 로 고정.
- **텔레그램 연동**: 봇 생성·시크릿 등록 후 알림 정상 동작 확인.
- **Step 1 검증 완료**: 노션 테스트 행 생성 + 텔레그램 ✅ 알림 확인.
- **Step 2 구현**: `scripts/pykrx_scan.py` + `pykrx_scan.yml` + requirements에 `pykrx` 추가.
  가짜 데이터로 판정·정렬·금액변환 단위검증 통과.
- **Step 2 이슈/수정 (KRX 로그인 필수화)**: 첫 실행 시 `get_nearest_business_day_in_a_week` 에서
  `IndexError`(빈 응답). 원인은 **pykrx 1.2.8부터 KRX 회원 로그인(KRX_ID/KRX_PW)이 필수**가 됨
  (인증 없으면 데이터가 빈값). **해결**: `KRX_ID`/`KRX_PW` 시크릿 추가 + 워크플로 env 주입 +
  `config.require`에 추가 + `requirements`에 `pykrx>=1.2.8` 고정. → "키 불필요" 가정은 무효화됨.
- **Step 2 검증 완료 + 기준 완화**: KRX 로그인 성공, 시총 200종목 스캔→노션 C 저장→텔레그램 정상.
  첫 검증(6/12 기준)은 0종목. 한국시장에서 외인·기관은 보통 반대로 매매해 '둘 다 3일 연속'이 과도하게 엄격.
  → 기준을 **(외인+기관) 합산 순매수 3일 연속**으로 완화(`analyze_ticker`). 검증: 36종목 포착.
- **Step 2 추가 지표 부착**: 수급 통과 종목에 **거래량 급증(20일 평균 2배+)** 과 **52주 신고가권(97%+)**
  계산을 추가. 셋 다 충족하면 **🔥3중 신호**로 노션 상단·텔레그램에 강조(`check_extra_signals`). 가짜데이터 검증 통과.
- **Step 2 신호 등급화 + 완화**: 검증일(6/12) 3중 신호 0종목 — 대형주는 거래량 2배·신고가 동시가 드묾.
  → 문턱 완화(거래량 **1.5배**·신고가 **90%**) + **⭐2중 신호(수급 + 거래량/신고가 중 1)** 등급 추가. 가짜데이터로 3중/2중 분류 검증.
  - ✅ 실제 검증(6/12 기준): 수급 36종목 / 🔥3중 3 / ⭐2중 11. **Step 2 완료.**
- **Step 2 수급 로직 '기관 중심'으로 변경**: 외국인 순매수는 패시브·차익거래 등 비방향성 노이즈가 많다는 점 반영.
  채택 기준을 '합산'에서 **'기관 3거래일 연속 순매수(필수)'** 로 변경, 외국인 동반 매수는 `외인 동반` 태그+정렬 가점으로 처리(`analyze_ticker`). 가짜데이터 검증 통과.

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
| `KRX_ID` / `KRX_PW` | Step 2 KRX 회원 로그인(데이터 조회 필수) | ⏳ 사용자 등록 예정 |
| `NOTION_DB_B` | Step 3 DART 표 | ⏳ 사용자 등록 예정 |
| `DART_API_KEY` | Step 3 DART 공시 (opendart.fss.or.kr 무료 발급) | ⏳ 사용자 등록 예정 |
| `NOTION_DB_A` | Step 4 Reddit 표 | ⏳ 예정 |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Step 4 Reddit | ⏳ 예정 |
| `NOTION_DB_D` / `ANTHROPIC_API_KEY` | Step 5 Claude 요약 | ⏳ 예정 |

---

## 7. 나중에 바꾸고 싶을 때 (조정 가능한 값)

| 무엇 | 현재값 | 파일 위치 |
|---|---|---|
| 수급: 대상 종목 수 | `TOP_N = 200` | `scripts/pykrx_scan.py` 상단 |
| 수급: 연속 순매수 일수 | `CONSECUTIVE_DAYS = 3` | `scripts/pykrx_scan.py` 상단 |
| 수급: 판정 방식 | **기관 3일 연속 순매수**(필수) + 외인 동반 가점 | `scripts/pykrx_scan.py` `analyze_ticker` |
| 거래량 급증 배수 | `VOL_SURGE_MULT = 1.5` | `scripts/pykrx_scan.py` 상단 |
| 52주 신고가권 비율 | `HIGH_NEAR_RATIO = 0.90` | `scripts/pykrx_scan.py` 상단 |
| 수급: 조회 구간(달력일) | `LOOKBACK_DAYS = 15` | `scripts/pykrx_scan.py` 상단 |
| 실행 시각 (수급) | cron `"30 7 * * *"` (=16:30 KST) | `.github/workflows/pykrx_scan.yml` |
| 실행 시각 (DART) | cron `"0 7 * * *"` (=16:00 KST) | `.github/workflows/dart_scan.yml` |
| DART: 키워드 | `KEYWORDS = ["수주","계약체결","특허","기술이전"]` | `scripts/dart_scan.py` 상단 |
| DART: 대형계약 기준 | `RATIO_THRESHOLD = 0.10` (시총 대비 10%) | `scripts/dart_scan.py` 상단 |
| DART: 공시 유형 | `PBLNTF_TYPES = ["B","E"]` (주요사항보고+기타공시) | `scripts/dart_scan.py` 상단 |
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
  dart_scan.py         # Step 3 DART 공시 스캔
.github/workflows/
  test_connection.yml  # 수동 실행
  pykrx_scan.yml       # 16:30 KST 자동 + 수동
  dart_scan.yml        # 16:00 KST 자동 + 수동
docs/
  PROJECT_LOG.md       # ← 이 문서
requirements.txt
README.md
```
