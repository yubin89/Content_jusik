"""선취매 후보 성과 추적 (1·2주 forward 검증).

pykrx_scan 이 발굴한 '선취매 후보'를 매일 `data/pykrx_picks/`에 스냅샷으로 저장하고,
1주(5거래일)·2주(10거래일) 뒤 각 종목이 실제로 올랐는지 자동 평가한다.
적중 기준 = 진입가(발굴 당일 종가) 대비 '기간 내 고가'가 +10% 이상 터치.
리포트는 Notion C + Telegram(요약 1줄)로 출력한다.

스냅샷/평가기록을 레포에 커밋(워크플로)함으로써 컨테이너가 사라져도 누적된다.
"""
import json
import os
import time
from datetime import datetime, timedelta

from pykrx import stock

from scripts.common import notify, notion_client
from scripts.common.logger import get_logger

log = get_logger("track_picks")

PICKS_DIR = "data/pykrx_picks"
HORIZONS = {"1주": 5, "2주": 10}   # 라벨 → 평가 시점(거래일)
HIT_THRESHOLD = 0.10               # 적중 = 기간 내 고가가 진입가 대비 +10%↑ 터치
SLEEP = 0.3                        # 종목 간 호출 간격(초)
KOSPI_INDEX = "1001"               # 거래일 달력 산출용 코스피 지수 코드


def _retry(fn, *args, **kwargs):
    """KRX 호출 지수 백오프 재시도."""
    delay, last = 2, None
    for attempt in range(1, 4):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("KRX 호출 재시도 (%s/3): %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
    raise last


# ---- 파일 I/O ----

def _path(name):
    return os.path.join(PICKS_DIR, name)


def save_snapshot(date, picks):
    """오늘 선취매 후보를 JSON 스냅샷으로 저장(진입가 있는 종목만)."""
    os.makedirs(PICKS_DIR, exist_ok=True)
    clean = [p for p in picks if p.get("entry_close")]
    with open(_path(f"{date}.json"), "w", encoding="utf-8") as f:
        json.dump({"date": date, "picks": clean}, f, ensure_ascii=False, indent=2)
    log.info("선취매 스냅샷 저장: %s (%d종목)", date, len(clean))


def _load_snapshot(d):
    p = _path(f"{d}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _load_evaluated():
    p = _path("_evaluated.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save_evaluated(keys):
    os.makedirs(PICKS_DIR, exist_ok=True)
    with open(_path("_evaluated.json"), "w", encoding="utf-8") as f:
        json.dump(keys, f, ensure_ascii=False, indent=2)


# ---- 평가 ----

def _business_days(date, lookback_days=40):
    """기준일까지의 최근 거래일 리스트(코스피 지수 인덱스 = 실제 개장일)."""
    frm = (datetime.strptime(date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")
    idf = _retry(stock.get_index_ohlcv_by_date, frm, date, KOSPI_INDEX)
    if idf is None or idf.empty:
        return []
    return [d.strftime("%Y%m%d") for d in idf.index]


def _forward(ticker, entry_date, todate, entry_close):
    """진입일 이후 고가 최대 상승률 / 마지막 종가 수익률."""
    if not entry_close or entry_close <= 0:
        return None
    df = _retry(stock.get_market_ohlcv_by_date, entry_date, todate, ticker)
    if df is None or df.empty or "고가" not in df.columns or "종가" not in df.columns:
        return None
    after = df.iloc[1:]   # 진입일(첫 행) 이후
    if after.empty:
        return None
    high_ret = float(after["고가"].max()) / entry_close - 1
    close_ret = float(df["종가"].iloc[-1]) / entry_close - 1
    return {"high_ret": high_ret, "close_ret": close_ret}


def evaluate_and_report(date, db_id):
    """5/10거래일 도달한(미평가) cohort를 평가해 Notion+Telegram으로 리포트."""
    bdays = _business_days(date)
    if not bdays or bdays[-1] != date:
        log.info("성과추적: 기준일 %s 가 거래일 달력에 없음 — 평가 건너뜀", date)
        return

    evaluated = _load_evaluated()
    last = len(bdays) - 1
    sections = []

    for label, h in HORIZONS.items():
        if last - h < 0:
            continue
        entry_date = bdays[last - h]
        key = f"{entry_date}@{h}"
        if key in evaluated:
            continue
        snap = _load_snapshot(entry_date)
        evaluated.append(key)   # 스냅샷 유무와 무관하게 1회만 처리
        if not snap or not snap.get("picks"):
            continue

        results = []
        for p in snap["picks"]:
            fr = _forward(p["ticker"], entry_date, date, p.get("entry_close"))
            time.sleep(SLEEP)
            if fr is None:
                continue
            results.append({**p, **fr, "hit": fr["high_ret"] >= HIT_THRESHOLD})
        if results:
            sections.append((label, h, entry_date, results))

    _save_evaluated(evaluated)
    if sections:
        _write_report(date, db_id, sections)
    else:
        log.info("성과추적: 평가할 신규 cohort 없음(%s)", date)


def _write_report(date, db_id, sections):
    blocks = [notion_client.heading(f"선취매 성과추적 {date} (적중=기간내 고가 +10% 터치)", 2)]
    tg_parts = []

    for label, h, entry_date, results in sections:
        n = len(results)
        hits = sum(1 for r in results if r["hit"])
        rate = hits / n * 100
        avg_high = sum(r["high_ret"] for r in results) / n
        avg_close = sum(r["close_ret"] for r in results) / n

        blocks.append(notion_client.heading(
            f"{label}({h}거래일) {entry_date} — {n}종목 / 적중 {hits} ({rate:.0f}%) "
            f"/ 평균 고가 {avg_high * 100:+.1f}% · 종가 {avg_close * 100:+.1f}%", 3))
        for r in sorted(results, key=lambda x: x["high_ret"], reverse=True):
            mark = "적중✅" if r["hit"] else "—"
            blocks.append(notion_client.bullet(
                f"{r.get('name', r['ticker'])}({r['ticker']}) ★{r.get('score', '?')} — "
                f"고가 {r['high_ret'] * 100:+.1f}% / 종가 {r['close_ret'] * 100:+.1f}%  {mark}"))

        # 별점별 평균 고가수익(스크리너 튜닝 단서)
        by = {}
        for r in results:
            by.setdefault(r.get("score"), []).append(r["high_ret"])
        comp = ", ".join(
            f"★{s} {sum(v) / len(v) * 100:+.1f}%({len(v)})"
            for s, v in sorted(by.items(), key=lambda kv: (kv[0] is None, kv[0]), reverse=True)
            if s is not None
        )
        if comp:
            blocks.append(notion_client.paragraph("별점별 평균 고가수익: " + comp))

        tg_parts.append(f"{label} {n}개 적중 {hits}({rate:.0f}%) 고가평균 {avg_high * 100:+.1f}%")

    notion_client.create_page_in_database(db_id, f"\U0001f4ca 선취매 성과추적 {date}", blocks)
    notify.send_message("\U0001f4ca 성과추적 " + " · ".join(tg_parts))
    log.info("성과추적 리포트 완료: %s", " / ".join(tg_parts))
