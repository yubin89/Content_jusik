"""Reddit 멀티서브레딧 티커 언급 급증 스캔 → Notion A.

5개 서브레딧(wallstreetbets, stocks, investing, SecurityAnalysis, pennystocks)에서
최근 24시간 포스트 제목+본문을 수집해 종목 티커 언급 횟수를 집계한다.
전일 스냅샷과 비교해 300%+ 급증 또는 신규 등장(당일 ≥10회) 종목을 Notion A에 저장한다.
스냅샷은 data/reddit_snapshots/에 레포 커밋으로 누적된다(소급 불가이므로 2~3일치 후 의미 발생).
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import praw

from scripts.common import config, notify, notion_client
from scripts.common.logger import get_logger

log = get_logger("reddit_scan")

STAGE = "Reddit 스캔"
SNAPSHOTS_DIR = "data/reddit_snapshots"

# ---- 스캔 대상 서브레딧 ----
SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "investing",
    "SecurityAnalysis",
    "pennystocks",
]

# ---- 티커 추출 설정 ----
LOOKBACK_HOURS = 24          # 최근 N시간 포스트
MIN_COUNT = 5                # 최소 언급 횟수 (0→N 폭증 노이즈 방지)
SURGE_THRESHOLD = 3.0        # 전일 대비 300%+ = 급증
NEW_MIN_COUNT = 10           # 신규 등장으로 표시할 최소 언급 횟수

# ---- 오탐 방지 블랙리스트 (영어 일반 단어 / 금융 약어) ----
_BLACKLIST = {
    # 일반 단어
    "A", "I", "IS", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IN", "IT",
    "NO", "OF", "ON", "OR", "SO", "TO", "UP", "WE",
    "AM", "PM", "UK", "US", "EU",
    "ARE", "BUT", "FOR", "GET", "GOT", "HAS", "HAD", "HIM", "HIS", "HOW",
    "ITS", "LET", "LOL", "MAY", "NEW", "NOW", "OLD", "ONE", "OUR", "OUT",
    "OWN", "PUT", "RAN", "SAW", "SAY", "SET", "SHE", "THE", "TOO", "TWO",
    "USE", "WAS", "WAY", "WHO", "WHY", "YET", "YOU",
    "BACK", "BEST", "BOTH", "CALL", "COME", "DOES", "DOWN", "EACH", "EVEN",
    "EVER", "FIND", "FORM", "FROM", "GIVE", "GOOD", "HAVE", "HERE", "HIGH",
    "HOLD", "HOME", "JUST", "KEEP", "KNOW", "LAST", "LEFT", "LIKE", "LOOK",
    "LONG", "MADE", "MAIN", "MAKE", "MANY", "MORE", "MOST", "MOVE", "MUCH",
    "MUST", "NAME", "NEED", "NEXT", "ONLY", "OPEN", "OVER", "PART", "PLAY",
    "REAL", "SAME", "SAYS", "SELL", "SHOW", "SIDE", "SOME", "SUCH", "SURE",
    "TAKE", "THAN", "THAT", "THEM", "THEN", "THEY", "THIS", "TIME", "TOLD",
    "TOOK", "TRUE", "TURN", "USED", "VERY", "WANT", "WELL", "WENT", "WERE",
    "WHAT", "WHEN", "WILL", "WITH", "WORK", "YEAR", "YOUR",
    # 금융/커뮤니티 약어
    "CEO", "CFO", "COO", "CTO", "IPO", "ETF", "ATH", "ATL", "AUM",
    "DD", "DCA", "WSB", "YOLO", "FOMO", "HODL", "MOON", "BEAR", "BULL",
    "IMO", "IMHO", "TBH", "TIL", "TLDR", "OP",
    "EPS", "PE", "PB", "ROE", "ROA", "DCF", "FCF", "EV",
    "FED", "GDP", "CPI", "PPI", "PCE", "NFP",
    "SEC", "FDA", "EPA", "DOJ",
    "NYSE", "NASDAQ", "AMEX",
    "USA", "NATO", "OPEC",
    "NEWS", "BANK", "DEBT", "LOSS", "GAIN", "RATE", "CASH", "COST",
    "FUND", "LOAN", "NOTE", "RISK", "SALE", "WEEK", "DAYS", "YEAR",
    "PUTS", "CALL", "CALLS", "PUTS",
    "BIG", "NET", "TOP", "LOW", "MAX", "MIN", "EST",
    "INC", "LLC", "LTD", "CORP",
    "REIT", "SPAC", "PIPE",
    "SHORT", "LONG", "PUTS", "CALLS",
    "TODAY", "STOCK", "TRADE", "MARKET", "PRICE", "SHARE",
    "MONEY", "BUYS", "SELLS", "GAINS", "LOSS", "LOSSES",
    "GOING", "THINK", "LOOKS", "BUYING", "SELLING",
}


def _make_reddit():
    return praw.Reddit(
        client_id=config.get("REDDIT_CLIENT_ID"),
        client_secret=config.get("REDDIT_CLIENT_SECRET"),
        user_agent=config.get("REDDIT_USER_AGENT"),
        # read-only: 로그인 불필요
    )


def _extract_tickers(text):
    """포스트 텍스트에서 티커 후보를 추출."""
    counts = {}

    # $TICKER 형태 (가장 정확)
    for m in re.finditer(r"\$([A-Z]{1,5})\b", text):
        t = m.group(1)
        if t not in _BLACKLIST:
            counts[t] = counts.get(t, 0) + 1

    # 대문자 2-5글자 독립 단어 ($캐시태그 제외된 나머지)
    for m in re.finditer(r"(?<!\$)\b([A-Z]{2,5})\b", text):
        t = m.group(1)
        if t not in _BLACKLIST and not t.isdigit():
            counts[t] = counts.get(t, 0) + 1

    return counts


def collect_mentions(reddit):
    """5개 서브레딧에서 최근 LOOKBACK_HOURS 시간의 포스트 언급 집계."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    cutoff_ts = cutoff.timestamp()
    total_posts = 0
    all_counts = {}

    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)
            fetched = 0
            for post in subreddit.new(limit=500):
                if post.created_utc < cutoff_ts:
                    break
                fetched += 1
                text = f"{post.title} {post.selftext or ''}"
                for ticker, cnt in _extract_tickers(text.upper()).items():
                    all_counts[ticker] = all_counts.get(ticker, 0) + cnt
            log.info("서브레딧 r/%s: %d 포스트 수집", sub_name, fetched)
            total_posts += fetched
        except Exception as exc:  # noqa: BLE001
            log.warning("r/%s 수집 실패(건너뜀): %s", sub_name, exc)

    # 최소 MIN_COUNT 미만 제거
    filtered = {t: c for t, c in all_counts.items() if c >= MIN_COUNT}
    return filtered, total_posts


# ---- 스냅샷 I/O ----

def _snapshot_path(date_str):
    return os.path.join(SNAPSHOTS_DIR, f"{date_str}.json")


def save_snapshot(date_str, counts, total_posts):
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    with open(_snapshot_path(date_str), "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "counts": counts, "total_posts": total_posts},
                  f, ensure_ascii=False, indent=2)
    log.info("스냅샷 저장: %s (%d 티커)", date_str, len(counts))


def _load_snapshot(date_str):
    p = _snapshot_path(date_str)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("counts", {})


# ---- 비교 ----

def compare(today_counts, prev_counts):
    """300%+ 급증 / 신규 등장 분류."""
    surge = []    # (ticker, today, prev, ratio)
    new_tickers = []  # (ticker, today)

    for t, today in sorted(today_counts.items(), key=lambda x: -x[1]):
        prev = prev_counts.get(t, 0)
        if prev == 0:
            if today >= NEW_MIN_COUNT:
                new_tickers.append((t, today))
        else:
            ratio = (today - prev) / prev
            if ratio >= SURGE_THRESHOLD:
                surge.append((t, today, prev, ratio))

    # 급증은 비율 내림차순
    surge.sort(key=lambda x: -x[3])
    return surge, new_tickers


# ---- Notion 저장 ----

def write_to_notion(db_id, date_str, surge, new_tickers, today_counts,
                    has_prev, total_posts):
    title = f"\U0001f525 Reddit 급등 {date_str}"
    blocks = [notion_client.heading(
        f"Reddit 티커 급등 {date_str} (최근 24h · {total_posts}포스트 · {len(today_counts)}티커)", 2)]

    if not has_prev:
        blocks.append(notion_client.paragraph(
            "첫 실행 — 베이스라인 저장 완료. 다음 실행부터 전일 대비 비교가 시작됩니다."))
    else:
        # 급증 섹션
        if surge:
            blocks.append(notion_client.heading(
                f"\U0001f525 300%+ 급증 ({len(surge)}종목)", 3))
            for t, today, prev, ratio in surge:
                blocks.append(notion_client.bullet(
                    f"{t} — {today:,}회 (전일 {prev:,} → +{ratio*100:.0f}%) \U0001f525"))
        else:
            blocks.append(notion_client.paragraph("300%+ 급증 종목 없음"))

        # 신규 등장 섹션
        if new_tickers:
            blocks.append(notion_client.heading(
                f"\U0001f331 신규 등장 ({len(new_tickers)}종목)", 3))
            for t, cnt in new_tickers:
                blocks.append(notion_client.bullet(f"{t} — {cnt:,}회 (신규 등장)"))

    # 전체 상위 20
    blocks.append(notion_client.heading("전체 언급 상위 20", 3))
    top20 = sorted(today_counts.items(), key=lambda x: -x[1])[:20]
    for t, cnt in top20:
        blocks.append(notion_client.bullet(f"{t} — {cnt:,}회"))

    notion_client.create_page_in_database(db_id, title, blocks)
    log.info("Notion 저장 완료: %s", title)


# ---- 메인 ----

def main():
    cfg = config.require([
        "NOTION_TOKEN", "NOTION_DB_A",
        "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT",
    ])
    db_id = cfg["NOTION_DB_A"]

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prev_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")

    try:
        log.info("Reddit 수집 시작 (최근 %dh, 서브레딧 %d개)", LOOKBACK_HOURS, len(SUBREDDITS))
        reddit = _make_reddit()
        today_counts, total_posts = collect_mentions(reddit)
        log.info("언급 집계 완료: %d 티커 (포스트 %d건)", len(today_counts), total_posts)

        save_snapshot(today, today_counts, total_posts)

        prev_counts = _load_snapshot(prev_date)
        has_prev = prev_counts is not None
        if not has_prev:
            log.info("전일 스냅샷 없음 — 첫 실행(베이스라인 저장)")
            prev_counts = {}

        surge, new_tickers = compare(today_counts, prev_counts)
        log.info("급증 %d종목 / 신규 %d종목", len(surge), len(new_tickers))

        write_to_notion(db_id, today, surge, new_tickers, today_counts,
                        has_prev, total_posts)

        # Telegram 알림
        if not has_prev:
            summary = f"{today} 첫 실행 — 베이스라인 저장 완료 ({len(today_counts)}티커). 내일부터 비교."
        elif not surge and not new_tickers:
            summary = f"{today} 300%+ 급증 없음 / 신규 없음 (전체 {len(today_counts)}티커)"
        else:
            parts = []
            if surge:
                top = surge[0]
                parts.append(f"\U0001f525급증 {len(surge)}종목 (1위: {top[0]} +{top[3]*100:.0f}%)")
            if new_tickers:
                parts.append(f"\U0001f331신규 {len(new_tickers)}종목")
            summary = f"{today} {' · '.join(parts)} — 자세한 목록은 Notion A"

        notify.notify_success(STAGE, summary)

    except Exception as exc:
        notify.notify_error(STAGE, exc)
        raise


if __name__ == "__main__":
    main()
