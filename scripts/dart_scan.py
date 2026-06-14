"""Step 3 — DART 공시 스캔 (Notion B).

opendart.fss.or.kr 에서 오늘 게시된 공시 중 수주·계약체결·특허·기술이전 키워드 포함,
상장 종목(stock_code 존재), 계약금액 ≥ 시총 10% 인 건을 추출해 Notion B에 저장한다.
금액을 파악할 수 없는 건(특허·기술이전 등)은 비율 필터를 적용하지 않고 목록에 포함한다.

실행: python -m scripts.dart_scan
필요 환경변수: NOTION_TOKEN, NOTION_DB_B, DART_API_KEY, KRX_ID, KRX_PW
선택 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from pykrx import stock

from scripts.common import config, notify, notion_client
from scripts.common.logger import get_logger

STAGE = "DART 공시"
log = get_logger("dart_scan")

DART_BASE = "https://opendart.fss.or.kr/api"

# ---- 조정 가능한 설정 ----
KEYWORDS = ["수주", "계약체결", "특허", "기술이전"]  # report_nm 포함 여부 검사
RATIO_THRESHOLD = 0.10   # 계약금액 / 시총 ≥ 10% 이상만 대형계약으로 분류
DART_PAGE_COUNT = 100    # 페이지당 최대(DART API 한도)
SLEEP_BETWEEN = 0.2      # DART API 호출 간격(초)
# 공시 유형: B=주요사항보고, E=기타공시 두 가지 모두 조회
PBLNTF_TYPES = ["B", "E"]
# 키워드 우선순위 (낮을수록 먼저)
KEYWORD_PRIORITY = {"수주": 0, "계약체결": 0, "특허": 1, "기술이전": 2}
EOK = 100_000_000  # 원 → 억원


def _retry(fn, *args, **kwargs):
    """지수 백오프로 재시도(2s/4s/8s, 3회). 외부 API 호출 공용."""
    delay = 2
    last = None
    for attempt in range(1, 4):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("호출 재시도 (%s/3): %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
    raise last


# ---- DART API ----

def fetch_dart_list(api_key, bgn_de, end_de, pblntf_ty):
    """DART /list.json 을 페이지네이션하며 전체 공시 목록을 가져온다.

    status="010" (데이터 없음)은 정상 상황(휴장 등)이므로 빈 리스트 반환.
    그 외 오류 status는 RuntimeError → 상위 _retry 에서 재시도.
    """
    results = []
    page_no = 1
    while True:
        resp = requests.get(
            f"{DART_BASE}/list.json",
            params={
                "crtfc_key": api_key,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": pblntf_ty,
                "page_no": page_no,
                "page_count": DART_PAGE_COUNT,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status == "010":
            log.info("DART status=010 (데이터 없음) pblntf_ty=%s", pblntf_ty)
            break
        if status != "000":
            raise RuntimeError(f"DART API 오류: status={status}, msg={data.get('message')}")
        items = data.get("list", [])
        results.extend(items)
        log.info("DART 목록 page=%d, %d건 (누적 %d)", page_no, len(items), len(results))
        if len(items) < DART_PAGE_COUNT:
            break
        page_no += 1
        time.sleep(SLEEP_BETWEEN)
    return results


def _parse_amount(raw):
    """금액 문자열 → int(원 단위). "-", "N/A", "" 등 파싱 불가 → None."""
    if not raw or str(raw).strip() in ("-", "N/A", "없음", "해당없음", "0"):
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return None


def fetch_contract_amount(api_key, corp_code, bgn_de, end_de, report_nm):
    """공시 유형별 구조화 API로 계약금액(원 단위 int)을 가져온다. 파싱 불가 시 None.

    - "수주" 포함 → /order.json, ord_amt 필드
    - "계약" 포함 → /cntrwk.json, sal_amt 필드
    - 그 외(특허·기술이전) → None (구조화 API 없음)
    """
    if "수주" in report_nm:
        url, field = f"{DART_BASE}/order.json", "ord_amt"
    elif "계약" in report_nm:
        url, field = f"{DART_BASE}/cntrwk.json", "sal_amt"
    else:
        return None

    try:
        resp = requests.get(
            url,
            params={"crtfc_key": api_key, "corp_code": corp_code,
                    "bgn_de": bgn_de, "end_de": end_de},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "000":
            return None
        items = data.get("list", [])
        return _parse_amount(items[0].get(field)) if items else None
    except Exception as exc:  # noqa: BLE001
        log.warning("계약금액 조회 실패 corp_code=%s: %s", corp_code, exc)
        return None


# ---- pykrx 시가총액 캐시 ----

def build_marketcap_cache(date):
    """KOSPI+KOSDAQ 전체 시가총액을 {ticker: int(원)} dict로 반환.

    pykrx 1.2.8+ 는 KRX 회원 로그인(KRX_ID/KRX_PW)이 필수. 없으면 빈 dict.
    """
    cache = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = _retry(stock.get_market_cap_by_ticker, date, market=market)
            if df is None or df.empty:
                continue
            cap_col = "시가총액" if "시가총액" in df.columns else next(
                (c for c in df.columns if "시가총액" in c), None
            )
            if cap_col is None:
                continue
            for ticker, row in df.iterrows():
                cap = int(row[cap_col])
                if cap > 0:
                    cache[ticker] = cap
        except Exception as exc:  # noqa: BLE001
            log.warning("시가총액 조회 실패 market=%s: %s", market, exc)
    log.info("시가총액 캐시: %d종목", len(cache))
    return cache


# ---- 필터링 & Enrich ----

def _match_keyword(report_nm):
    """KEYWORDS 중 첫 번째 매칭 키워드 반환. 없으면 None."""
    for kw in KEYWORDS:
        if kw in report_nm:
            return kw
    return None


def filter_and_enrich(raw_items, api_key, bgn_de, end_de, marketcap_cache):
    """공시 목록을 필터링하고 계약금액·시총비율을 보강한다.

    필터 순서:
    1. stock_code 없으면 스킵 (비상장)
    2. report_nm 키워드 매칭 없으면 스킵
    3. 수주/계약 타입 → 구조화 API로 금액 조회
    4. 금액 + 시총 둘 다 파악 → ratio 계산
       - ratio < RATIO_THRESHOLD → 스킵 (소형 계약 제외)
       - ratio >= RATIO_THRESHOLD → 통과, is_large=True
    5. 금액 미파악 또는 시총 미파악 → 무조건 통과 (has_amount=False)
    """
    enriched = []
    for item in raw_items:
        stock_code = item.get("stock_code", "").strip()
        if not stock_code:
            continue

        report_nm = item.get("report_nm", "")
        keyword = _match_keyword(report_nm)
        if keyword is None:
            continue

        corp_code = item.get("corp_code", "")
        amount = None
        if keyword in ("수주", "계약체결"):
            time.sleep(SLEEP_BETWEEN)
            amount = fetch_contract_amount(api_key, corp_code, bgn_de, end_de, report_nm)

        marketcap = marketcap_cache.get(stock_code)
        has_amount = False
        ratio = None
        if amount and marketcap:
            ratio = amount / marketcap
            if ratio < RATIO_THRESHOLD:
                log.info("소형 계약 스킵: %s ratio=%.1f%%", item.get("corp_name"), ratio * 100)
                continue
            has_amount = True

        enriched.append({
            **item,
            "keyword": keyword,
            "has_amount": has_amount,
            "amount": amount,
            "marketcap": marketcap,
            "ratio": ratio,
            "is_large": has_amount and ratio is not None and ratio >= RATIO_THRESHOLD,
        })
    return enriched


def sort_results(items):
    """정렬: has_amount 있는 것 먼저 → ratio 내림차순 → 키워드 우선순위 → 회사명."""
    return sorted(
        items,
        key=lambda x: (
            not x["has_amount"],
            -(x["ratio"] or 0),
            KEYWORD_PRIORITY.get(x["keyword"], 99),
            x.get("corp_name", ""),
        ),
    )


# ---- Notion 블록 빌더 ----

def _fmt_amount(amount):
    """원 단위 정수 → 억원 문자열. None이면 빈 문자열."""
    if not amount:
        return ""
    eok = amount / EOK
    return f"{eok:,.0f}억" if eok >= 1 else f"{amount / 10_000:,.0f}만"


def _bullet_text(item):
    """공시 1건 → Notion 불릿 텍스트."""
    corp = item.get("corp_name", "")
    code = item.get("stock_code", "")
    report = item.get("report_nm", "")[:40]
    base = f"{corp}({code}) — {report}"
    if item["has_amount"] and item["amount"]:
        pct = (item["ratio"] or 0) * 100
        tag = " 🎯 대형계약" if item.get("is_large") else ""
        return f"{base} — {_fmt_amount(item['amount'])} (시총 대비 {pct:.1f}%){tag}"
    return f"{base} (금액 미파악)"


def build_notion_blocks(results, kst_date_str):
    """Notion 페이지 본문 블록 구성."""
    large = [r for r in results if r.get("is_large")]
    blocks = [
        notion_client.heading(f"오늘 주요 공시 — {len(results)}건", 2),
        notion_client.paragraph(
            f"기준일: {kst_date_str} / 키워드: {', '.join(KEYWORDS)} / 대형계약: 시총 10%+"
        ),
        notion_client.heading(f"🎯 대형계약 (시총 10%+) — {len(large)}건", 3),
    ]
    if large:
        blocks.extend(notion_client.bullet(_bullet_text(r)) for r in large)
    else:
        blocks.append(notion_client.paragraph("해당 없음"))
    blocks.append(notion_client.heading("전체 주요 공시", 3))
    if results:
        blocks.extend(notion_client.bullet(_bullet_text(r)) for r in results)
    else:
        blocks.append(notion_client.paragraph("조건 충족 공시 없음"))
    return blocks


def _telegram_summary(results, kst_date_str):
    """텔레그램 성공 알림 요약 문자열."""
    large = [r for r in results if r.get("is_large")]
    top = large[:5] if large else results[:5]
    names = [
        f"{'🎯' if r.get('is_large') else ''}{r.get('corp_name','')}({r.get('keyword','')})"
        for r in top
    ]
    header = f"{kst_date_str} 공시 {len(results)}건 (대형 {len(large)})"
    return header + ("\n" + " ".join(names) if names else "")


# ---- 메인 ----

def main():
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    kst_date_str = now_kst.strftime("%Y-%m-%d")
    bgn_de = end_de = now_kst.strftime("%Y%m%d")

    # pykrx 1.2.8+ 는 KRX 회원 로그인이 필수(시가총액 조회용)
    config.require(["NOTION_TOKEN", "NOTION_DB_B", "DART_API_KEY", "KRX_ID", "KRX_PW"])
    db_id = config.get("NOTION_DB_B")
    api_key = config.get("DART_API_KEY")

    log.info("DART 스캔 시작: %s", kst_date_str)

    # 시가총액 캐시 (pykrx nearest_business_day 기준)
    try:
        krx_date = _retry(stock.get_nearest_business_day_in_a_week)
    except Exception as exc:  # noqa: BLE001
        log.warning("KRX 거래일 조회 실패, 오늘 날짜로 fallback: %s", exc)
        krx_date = bgn_de
    marketcap_cache = build_marketcap_cache(krx_date)

    # DART 공시 목록 수집 (pblntf_ty B + E 병합)
    all_raw = []
    for pblntf_ty in PBLNTF_TYPES:
        try:
            items = _retry(fetch_dart_list, api_key, bgn_de, end_de, pblntf_ty)
            all_raw.extend(items)
            time.sleep(SLEEP_BETWEEN)
        except Exception as exc:  # noqa: BLE001
            log.warning("DART 목록 조회 실패 pblntf_ty=%s: %s", pblntf_ty, exc)

    # rcept_no 기준 중복 제거 (B+E 동일 공시 중복 가능)
    seen: set = set()
    deduped = []
    for item in all_raw:
        rno = item.get("rcept_no")
        if rno not in seen:
            seen.add(rno)
            deduped.append(item)
    all_raw = deduped
    log.info("DART 원본 공시 수 (중복 제거 후): %d", len(all_raw))

    # 휴장·API 미발행 → "데이터 없음" 기록 후 정상 종료
    if not all_raw:
        title = f"📋 DART 공시 {bgn_de} — 데이터 없음"
        notion_client.create_page_in_database(db_id, title, [
            notion_client.paragraph("DART 공시 데이터 없음 (휴장 또는 API 미발행). 정상 처리."),
        ])
        notify.send_message(f"ℹ️ [{STAGE}] {kst_date_str} 데이터 없음(휴장/지연) — 스킵")
        log.info("데이터 없음 — 정상 종료")
        return

    # 필터링 + enrichment + 정렬
    results = filter_and_enrich(all_raw, api_key, bgn_de, end_de, marketcap_cache)
    results = sort_results(results)
    large_count = sum(1 for r in results if r.get("is_large"))
    log.info("필터 후 공시: %d건 (대형계약 %d)", len(results), large_count)

    # Notion B 저장
    title = f"📋 DART 공시 {bgn_de} (KST {kst_date_str})"
    blocks = build_notion_blocks(results, kst_date_str)
    notion_client.create_page_in_database(db_id, title, blocks)
    log.info("Notion 저장 완료: %d건", len(results))

    # Telegram 알림
    if not results:
        notify.notify_success(STAGE, f"{kst_date_str} 조건 충족 공시 없음")
    else:
        notify.notify_success(STAGE, _telegram_summary(results, kst_date_str))

    log.info("DART 스캔 완료 ✅")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log.exception("DART 스캔 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)
