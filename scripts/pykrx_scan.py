"""Step 2 — pykrx 수급 스캔 (Notion C).

코스피+코스닥 시총 상위 TOP_N 종목 중, 최근 CONSECUTIVE_DAYS 거래일 동안
'외국인합계'와 '기관합계'가 매일 둘 다 순매수(>0)인 종목을 추출해 Notion C에 저장한다.

실행: python -m scripts.pykrx_scan
필요 환경변수: NOTION_TOKEN, NOTION_DB_C, (선택) TELEGRAM_BOT_TOKEN/CHAT_ID
"""
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from pykrx import stock

from scripts.common import config, notify, notion_client
from scripts.common.logger import get_logger

STAGE = "수급 스캔"
log = get_logger("pykrx_scan")

# ---- 조정 가능한 설정 ----
TOP_N = 200            # 시총 상위 몇 종목을 대상으로 볼지
CONSECUTIVE_DAYS = 3   # 최근 며칠 연속 순매수여야 채택
LOOKBACK_DAYS = 15     # 달력일 기준 조회 구간(최소 3거래일 확보용 여유)
SLEEP_BETWEEN = 0.3    # 종목 간 호출 간격(초) — KRX 부담 완화
EOK = 100_000_000      # 원 → 억원 변환


def _retry(fn, *args, **kwargs):
    """KRX 호출을 지수 백오프로 재시도(일시 차단/지연 대비)."""
    delay = 2
    last = None
    for attempt in range(1, 4):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("KRX 호출 재시도 (%s/3): %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
    raise last


def get_top_marketcap_tickers(date):
    """코스피+코스닥 시총 상위 TOP_N 종목 티커 리스트."""
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        df = _retry(stock.get_market_cap_by_ticker, date, market=market)
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return []
    allcap = pd.concat(frames)
    cap_col = "시가총액" if "시가총액" in allcap.columns else next(
        (c for c in allcap.columns if "시가총액" in c), None
    )
    if cap_col is None:
        return []
    allcap = allcap[allcap[cap_col] > 0]
    top = allcap.sort_values(cap_col, ascending=False).head(TOP_N)
    return list(top.index)


def analyze_ticker(ticker, fromdate, todate):
    """최근 CONSECUTIVE_DAYS 거래일 모두 외인>0 AND 기관>0 이면 dict, 아니면 None."""
    df = _retry(stock.get_market_trading_value_by_date, fromdate, todate, ticker)
    if df is None or df.empty or len(df) < CONSECUTIVE_DAYS:
        return None

    inst_col = "기관합계" if "기관합계" in df.columns else next(
        (c for c in df.columns if "기관" in c), None
    )
    fore_col = "외국인합계" if "외국인합계" in df.columns else next(
        (c for c in df.columns if "외국인" in c), None
    )
    if inst_col is None or fore_col is None:
        return None

    recent = df.tail(CONSECUTIVE_DAYS)
    if not ((recent[inst_col] > 0).all() and (recent[fore_col] > 0).all()):
        return None

    # 끝에서부터 둘 다 양수인 연속 일수(streak) 계산
    streak = 0
    for i in range(len(df) - 1, -1, -1):
        if df[inst_col].iloc[i] > 0 and df[fore_col].iloc[i] > 0:
            streak += 1
        else:
            break

    window = df.tail(streak)
    return {
        "ticker": ticker,
        "streak": streak,
        "fore_sum": float(window[fore_col].sum()),
        "inst_sum": float(window[inst_col].sum()),
    }


def main():
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    # pykrx 1.2.8+ 는 KRX 회원 로그인이 필수(KRX_ID/KRX_PW). 없으면 데이터가 빈값으로 옴.
    config.require(["NOTION_TOKEN", "NOTION_DB_C", "KRX_ID", "KRX_PW"])
    db_id = config.get("NOTION_DB_C")

    date = _retry(stock.get_nearest_business_day_in_a_week)
    fromdate = (datetime.strptime(date, "%Y%m%d") - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    log.info("기준 거래일 %s (조회구간 %s~%s)", date, fromdate, date)

    tickers = get_top_marketcap_tickers(date)
    log.info("시총 상위 대상 종목 수: %d", len(tickers))

    # 휴장/데이터 미발행 → 실패가 아니라 '데이터 없음'으로 정상 처리
    if not tickers:
        title = f"\U0001f4c8 수급 스캔 {date} — 데이터 없음"
        notion_client.create_page_in_database(db_id, title, [
            notion_client.paragraph("KRX 시총 데이터가 없어 스캔을 건너뜀(휴장 또는 데이터 지연 가능)."),
        ])
        notify.send_message(f"ℹ️ [{STAGE}] {date} 데이터 없음(휴장/지연) — 스킵")
        log.info("데이터 없음 — 정상 종료")
        return

    results = []
    for i, ticker in enumerate(tickers, 1):
        try:
            r = analyze_ticker(ticker, fromdate, date)
            if r:
                results.append(r)
        except Exception as exc:  # noqa: BLE001 - 개별 종목 실패는 건너뛰고 계속
            log.warning("종목 %s 조회 실패 — 건너뜀: %s", ticker, exc)
        time.sleep(SLEEP_BETWEEN)
        if i % 50 == 0:
            log.info("진행 %d/%d (채택 %d)", i, len(tickers), len(results))

    # 연속일수 → 매수금액 합 순으로 정렬
    results.sort(key=lambda r: (r["streak"], r["fore_sum"] + r["inst_sum"]), reverse=True)

    # 종목명은 채택된 것만 조회(호출 절약)
    for r in results:
        try:
            r["name"] = _retry(stock.get_market_ticker_name, r["ticker"])
        except Exception:  # noqa: BLE001
            r["name"] = r["ticker"]

    # Notion C 저장
    title = f"\U0001f4c8 수급 스캔 {date} (KST {now_kst:%Y-%m-%d})"
    blocks = [
        notion_client.heading(
            f"{CONSECUTIVE_DAYS}거래일+ 연속 외인·기관 동시 순매수 — {len(results)}종목", 2
        ),
        notion_client.paragraph(
            f"대상: 코스피·코스닥 시총 상위 {len(tickers)}종목 / 기준일 {date}"
        ),
    ]
    if results:
        for r in results:
            blocks.append(notion_client.bullet(
                f"{r['name']}({r['ticker']}) — 연속 {r['streak']}일, "
                f"외인 +{r['fore_sum'] / EOK:,.0f}억, 기관 +{r['inst_sum'] / EOK:,.0f}억"
            ))
    else:
        blocks.append(notion_client.paragraph("조건 충족 종목 없음"))

    notion_client.create_page_in_database(db_id, title, blocks)
    log.info("Notion 저장 완료: %d종목", len(results))

    # Telegram 알림
    if results:
        head = ", ".join(f"{r['name']}({r['streak']}일)" for r in results[:5])
        notify.notify_success(STAGE, f"{date} 수급 포착 {len(results)}종목\n상위: {head}")
    else:
        notify.notify_success(STAGE, f"{date} 조건 충족 종목 없음")

    log.info("수급 스캔 완료 ✅")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 최상위에서 모든 예외 포착 후 알림
        log.exception("수급 스캔 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)
