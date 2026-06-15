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
TOP_N = 300            # 시총 상위 몇 종목을 대상으로 볼지
CONSECUTIVE_DAYS = 3   # 최근 며칠 연속 순매수여야 채택
LOOKBACK_DAYS = 15     # 달력일 기준 조회 구간(최소 3거래일 확보용 여유)
SLEEP_BETWEEN = 0.3    # 종목 간 호출 간격(초) — KRX 부담 완화
EOK = 100_000_000      # 원 → 억원 변환

# ---- 추가 지표 설정 (수급 통과 종목에만 적용) ----
VOL_SURGE_MULT = 1.5       # 최근 거래량 ≥ 최근 20거래일 평균의 N배 → 거래량 급증
VOL_AVG_DAYS = 20          # 거래량 평균 기준 거래일 수
HIGH_52W_DAYS = 252        # 52주 ≈ 252거래일
HIGH_NEAR_RATIO = 0.90     # 52주 최고 종가의 N% 이상이면 신고가권(돌파/근접)
OHLCV_LOOKBACK_DAYS = 400  # OHLCV 조회 구간(달력일, 52주 확보용 여유)

# ---- 가격 추세 필터 설정 ----
# 수급(기관 매집)이 실제 가격으로 발현되기 시작한 종목만 채택하기 위함.
# 당일 종가가 MA_TREND(중기)선 위 = 상승추세로 보고 채택. 아래면 '분산 의심'으로 제외.
MA_SHORT = 5    # 단기 추세선(거래일) — 위에 있으면 '단기 상승' 강세 태그
MA_TREND = 20   # 중기 추세선(거래일) — 당일 종가가 이 위여야 채택(하락추세 제외)

# ---- 선취매(매집 초기) 탐지 설정 ----
# 3중 신호(거래량 급증+신고가권)는 '이미 터진' 확인 신호 → 추격 매수가 늦을 수 있음.
# 반대로 '기관은 매집 중인데 아직 가격엔 안 터진' 종목(거래량 잠잠 + 고점까지 여유 +
# 당일 미급등)을 '선취매 후보'로 따로 surfaced 해서 폭등 전에 미리 포착한다.
EARLY_HIGH_MIN = 0.55       # 52주 고점 대비 현재가 ≥ 55% (바닥권 소외주 제외)
EARLY_HIGH_MAX = 0.88       # 52주 고점 대비 현재가 ≤ 88% (돌파 여지 충분 = 아직 신고가권 아님)
EARLY_MAX_DAY_CHANGE = 0.06 # 당일 등락률 < +6% (오늘 아직 급등 안 함 = 추격 아님)
SURGED_DAY_CHANGE = 0.12    # 당일 등락률 ≥ +12% → '이미 급등(추격주의)' 경고 태그

# ---- 매물대(오버헤드 공급) 설정 ----
# 고점대비 위치가 같아도, '현재가 위 가격대'에서 거래량이 많았으면 물린 물량(매물대)이
# 두꺼워 수급이 좋아도 잘 안 오른다. 최근 1년 거래량을 가격대별로 나눠, 현재가보다 높은
# 가격에서 거래된 비중(overhead_ratio)으로 매물대 부담을 측정한다. 작을수록 '소화됨'(긍정).
OVERHEAD_LIGHT = 0.15  # 현재가 위 거래량 비중 ≤ 15% → 매물대 가벼움(소화됨, 긍정 신호)
OVERHEAD_HEAVY = 0.40  # 현재가 위 거래량 비중 ≥ 40% → 위에 물린 물량 많음 → 선취매에서 제외


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
    """최근 CONSECUTIVE_DAYS 거래일 매일 기관 순매수(>0)면 채택(기관 중심). 외인 동반은 가점용."""
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

    # 기관 중심: 최근 CONSECUTIVE_DAYS 거래일 매일 기관이 순매수(>0)여야 채택
    inst = df[inst_col]
    if not (inst.tail(CONSECUTIVE_DAYS) > 0).all():
        return None

    # 끝에서부터 기관이 순매수인 연속 일수(streak) 계산
    streak = 0
    for i in range(len(inst) - 1, -1, -1):
        if inst.iloc[i] > 0:
            streak += 1
        else:
            break

    window = df.tail(streak)
    fore = df[fore_col]
    # 외국인 동반 매수(가점): 최근 CONSECUTIVE_DAYS일 모두 외인도 순매수
    foreign_accompany = bool((fore.tail(CONSECUTIVE_DAYS) > 0).all())
    return {
        "ticker": ticker,
        "streak": streak,
        "inst_sum": float(window[inst_col].sum()),
        "fore_sum": float(window[fore_col].sum()),
        "foreign_accompany": foreign_accompany,
    }


def check_extra_signals(ticker, todate):
    """수급 통과 종목에 대해 거래량 급증 / 52주 신고가권 여부를 계산."""
    blank = {
        "vol_surge": False, "vol_ratio": None, "new_high": False, "near_high": False,
        # 가격데이터 조회 실패 시: 추세필터는 통과(True)시켜 수급 통과 종목을 잃지 않고,
        # 단기상승 강세 태그는 데이터 없으면 미부여(False).
        "above_ma_short": False, "above_ma_trend": True,
        # 당일 등락률 / 52주 고점 대비 현재가 비율 (선취매 판정·추격주의 표시용)
        "day_change": None, "high_ratio": None,
        # 현재가보다 높은 가격대에서 거래된 거래량 비중(매물대 부담; 작을수록 소화됨)
        "overhead_ratio": None,
    }
    frm = (datetime.strptime(todate, "%Y%m%d") - timedelta(days=OHLCV_LOOKBACK_DAYS)).strftime("%Y%m%d")
    df = _retry(stock.get_market_ohlcv_by_date, frm, todate, ticker)
    if df is None or df.empty or "거래량" not in df.columns or "종가" not in df.columns:
        return blank
    df = df[df["거래량"] > 0]  # 휴장 등 거래량 0 행 제거
    if df.empty:
        return blank

    out = dict(blank)
    vol, close = df["거래량"], df["종가"]

    # 거래량 급증: 최근일 거래량 vs 직전 VOL_AVG_DAYS 거래일 평균
    if len(vol) >= VOL_AVG_DAYS + 1:
        base = vol.iloc[-(VOL_AVG_DAYS + 1):-1].mean()
        if base > 0:
            out["vol_ratio"] = float(vol.iloc[-1] / base)
            out["vol_surge"] = out["vol_ratio"] >= VOL_SURGE_MULT

    # 52주 신고가: 최근 종가 vs 최근 HIGH_52W_DAYS 거래일 최고 종가
    if len(close) >= 2:
        hi = float(close.iloc[-HIGH_52W_DAYS:].max())
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        if hi > 0:
            out["high_ratio"] = last / hi          # 고점 대비 현재가 위치(돌파 여유 판단)
            out["new_high"] = last >= hi
            out["near_high"] = last >= HIGH_NEAR_RATIO * hi
        if prev > 0:
            out["day_change"] = last / prev - 1     # 당일 등락률(이미 급등했는지 판단)

    # 가격 추세: 당일 종가가 단기(5일)/중기(20일) 이동평균선 위인지
    if len(close) >= MA_TREND:
        out["above_ma_trend"] = float(close.iloc[-1]) >= float(close.iloc[-MA_TREND:].mean())
    if len(close) >= MA_SHORT:
        out["above_ma_short"] = float(close.iloc[-1]) >= float(close.iloc[-MA_SHORT:].mean())

    # 매물대(오버헤드 공급): 최근 1년간 '현재가보다 높은 종가일'의 거래량 비중.
    # 일별 대표가격을 종가로 근사. 비중이 작을수록 위에 물린 물량이 적어 상승이 수월하다.
    if len(close) >= 2:
        last_p = float(close.iloc[-1])
        win = df.iloc[-HIGH_52W_DAYS:]
        tot_v = float(win["거래량"].sum())
        if tot_v > 0:
            overhead_v = float(win.loc[win["종가"] > last_p, "거래량"].sum())
            out["overhead_ratio"] = overhead_v / tot_v
    return out


def is_early(r):
    """선취매(매집 초기) 후보 판정.

    기관 연속 순매수(기본 조건) + 20일선 위(추세 전환 초입, 이미 필터됨)인 상태에서
    아직 '안 터진' 종목 = ① 거래량 미급증 ② 신고가권 아님(고점까지 여유 있음)
    ③ 당일 등락률 과하지 않음(오늘 추격 매수 아님).
    """
    hr = r.get("high_ratio")
    dc = r.get("day_change")
    ov = r.get("overhead_ratio")
    if r.get("vol_surge") or r.get("near_high"):
        return False
    if hr is None or not (EARLY_HIGH_MIN <= hr <= EARLY_HIGH_MAX):
        return False
    if dc is not None and dc >= EARLY_MAX_DAY_CHANGE:
        return False
    if ov is not None and ov >= OVERHEAD_HEAVY:   # 위에 물린 물량 많으면 제외
        return False
    return True


def _signal_flags(r):
    """불릿에 붙일 지표 태그 문자열."""
    parts = []
    if r.get("early"):
        parts.append("🌱매집초기")
    if r.get("vol_surge"):
        parts.append(f"거래량 {r['vol_ratio']:.1f}배")
    if r.get("new_high"):
        parts.append("52주 신고가")
    elif r.get("near_high"):
        parts.append("신고가 근접")
    if r.get("above_ma_short"):
        parts.append("단기상승")
    ov = r.get("overhead_ratio")
    if ov is not None:
        if ov <= OVERHEAD_LIGHT:
            parts.append("매물대 가벼움")
        elif ov >= OVERHEAD_HEAVY:
            parts.append("⚠️매물대 부담")
    dc = r.get("day_change")
    if dc is not None and dc >= SURGED_DAY_CHANGE:
        parts.append(f"⚠️당일 +{dc * 100:.0f}% 급등(추격주의)")
    return ("  ·  " + ", ".join(parts)) if parts else ""


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

    # 채택 종목: 종목명 + 추가 지표(거래량 급증 / 52주 신고가) 계산
    for r in results:
        try:
            r["name"] = _retry(stock.get_market_ticker_name, r["ticker"])
        except Exception:  # noqa: BLE001
            r["name"] = r["ticker"]
        try:
            r.update(check_extra_signals(r["ticker"], date))
        except Exception as exc:  # noqa: BLE001 - 지표 실패해도 수급 결과는 유지
            log.warning("지표 계산 실패 %s: %s", r["ticker"], exc)
        r["sig_count"] = int(bool(r.get("vol_surge"))) + int(bool(r.get("near_high")))
        r["triple"] = r["sig_count"] == 2   # 수급+거래량+신고가
        r["double"] = r["sig_count"] == 1   # 수급 + 둘 중 1개
        r["early"] = is_early(r)            # 선취매(매집 초기) 후보
        time.sleep(SLEEP_BETWEEN)

    # 가격 추세 하드 필터: 당일 종가가 20일선 위인 종목만 채택(하락추세=분산 의심 제외)
    before = len(results)
    results = [r for r in results if r.get("above_ma_trend", True)]
    log.info("가격 추세 필터: %d → %d (하락추세 %d 제외)", before, len(results), before - len(results))

    # 신호 강도(3중>2중) → 외인 동반 → 기관 매수금액 → 연속일수 순으로 정렬
    results.sort(
        key=lambda r: (r["sig_count"], int(r["foreign_accompany"]), r["inst_sum"], r["streak"]),
        reverse=True,
    )
    triple = [r for r in results if r["triple"]]
    double = [r for r in results if r["double"]]
    # 선취매 후보: 기관 매수금액 → 연속일수 순 (이미 강세인 3중/2중과 겹치지 않음)
    early = sorted(
        (r for r in results if r.get("early")),
        key=lambda r: (r["inst_sum"], r["streak"]), reverse=True,
    )

    def _bullet(r):
        txt = f"{r['name']}({r['ticker']}) — 기관 {r['streak']}일 연속 +{r['inst_sum'] / EOK:,.0f}억"
        if r.get("foreign_accompany"):
            txt += f", 외인 동반 +{r['fore_sum'] / EOK:,.0f}억"
        dc = r.get("day_change")
        if dc is not None:
            txt += f", 당일 {dc * 100:+.1f}%"
        hr = r.get("high_ratio")
        if hr is not None:
            txt += f", 고점대비 {hr * 100:.0f}%"
        ov = r.get("overhead_ratio")
        if ov is not None:
            txt += f", 매물대 {ov * 100:.0f}%"
        return notion_client.bullet(txt + _signal_flags(r))

    def _section(blocks, head, items, empty):
        blocks.append(notion_client.heading(head, 3))
        if items:
            blocks.extend(_bullet(r) for r in items)
        else:
            blocks.append(notion_client.paragraph(empty))

    # Notion C 저장
    title = f"\U0001f4c8 수급 스캔 {date} (KST {now_kst:%Y-%m-%d})"
    blocks = [
        notion_client.heading(
            f"{CONSECUTIVE_DAYS}거래일+ 연속 기관 순매수 (외인 동반 가점) — {len(results)}종목", 2
        ),
        notion_client.paragraph(
            f"대상: 코스피·코스닥 시총 상위 {len(tickers)}종목 / 기준일 {date}"
        ),
    ]
    _section(blocks, f"\U0001f331 매집 초기 (선취매 후보: 기관 매집 + 아직 미급등) — {len(early)}종목",
             early, "선취매 후보 없음")
    _section(blocks, f"\U0001f525 3중 신호 (수급+거래량급증+신고가권, 이미 강세·추격 주의) — {len(triple)}종목",
             triple, "3중 신호 종목 없음")
    _section(blocks, f"⭐ 2중 신호 (수급 + 거래량/신고가 중 1) — {len(double)}종목",
             double, "2중 신호 종목 없음")
    _section(blocks, "전체 수급 종목", results, "조건 충족 종목 없음")

    notion_client.create_page_in_database(db_id, title, blocks)
    log.info(
        "Notion 저장 완료: 수급 %d (선취 %d / 3중 %d / 2중 %d)",
        len(results), len(early), len(triple), len(double),
    )

    # Telegram 알림 — 폭등 전에 미리 보는 게 목적이므로 '선취매'를 헤드라인 앞에 둔다.
    highlight = early + triple + double
    if not results:
        notify.notify_success(STAGE, f"{date} 조건 충족 종목 없음")
    elif highlight:
        head = ", ".join(r["name"] for r in highlight[:5])
        notify.notify_success(
            STAGE,
            f"{date} 수급 {len(results)} / \U0001f331선취 {len(early)} "
            f"\U0001f5253중 {len(triple)} ⭐2중 {len(double)}\n{head}",
        )
    else:
        head = ", ".join(f"{r['name']}({r['streak']}일)" for r in results[:5])
        notify.notify_success(
            STAGE, f"{date} 수급 {len(results)}종목 (선취·3중·2중 0)\n상위: {head}"
        )

    log.info("수급 스캔 완료 ✅")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 최상위에서 모든 예외 포착 후 알림
        log.exception("수급 스캔 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)
