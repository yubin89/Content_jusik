"""X(트위터) FinTwit 화제종목 스캔 → Claude 심리분석 → Notion A.

큐레이션한 미국 FinTwit 계정(scripts/accounts.py)의 최근 24시간 트윗을 Apify로 수집해
스팸·노이즈를 정제하고, 가중 언급수를 일별 스냅샷으로 누적한다(data/x_snapshots/).
'급상승'은 하이브리드로 판정한다:
  - 워밍업(과거 < MIN_HISTORY일): 단순 배수(평소의 SURGE_MULT배↑) → 2~3일째부터 결과
  - 성숙(과거 ≥ MIN_HISTORY일): z-score(μ+Z·σ↑) → '평소 1→2회' 같은 노이즈 자동 차단
급상승/신규 종목만 Claude에 넘겨 시장심리(강세/약세/중립)와 연관 파생 키워드를 도출하고,
결과를 Notion A + Telegram으로 출력한다.
"""
import json
import os
import re
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

from scripts import accounts
from scripts.common import config, notify, notion_client
from scripts.common.logger import get_logger

log = get_logger("x_scan")

STAGE = "X 화제종목 스캔"
SNAPSHOTS_DIR = "data/x_snapshots"

# ---- Apify ----
APIFY_ACTOR = "apidojo~tweet-scraper"   # 교체 가능(kaitoeasyapi 등). URL용 ~ 표기.
APIFY_BASE = "https://api.apify.com/v2/acts"
APIFY_MAX_ITEMS = 600                   # 비용 상한(트윗 수). 계정수×하루치 대비 여유.
LOOKBACK_HOURS = 24

# ---- 정제 ----
MAX_CASHTAGS = 8        # 캐시태그 8개 초과 = 태그 스터핑(스팸) → 스킵
MIN_WEIGHTED = 3        # 당일 가중 언급수 바닥(미만 제거)
_PROMO = re.compile(
    r"\b(join|dm me|free signals?|my discord|telegram\.me|t\.me/|referral|"
    r"link in bio|sign ?up|promo ?code|join my)\b", re.I)
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
_UPPER = re.compile(r"(?<![\$\w])([A-Z]{2,5})(?![\w])")
# 캐시태그가 아닌 맨대문자에서 거를 일반어/약어(보조 추출 노이즈 방지)
_BLACKLIST = {
    "A", "I", "AI", "IT", "IS", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IN",
    "NO", "OF", "ON", "OR", "SO", "TO", "UP", "WE", "AM", "PM", "US", "UK", "EU",
    "ARE", "BUT", "FOR", "GET", "HAS", "HOW", "ITS", "NEW", "NOW", "ONE", "OUR",
    "OUT", "THE", "TOO", "TWO", "USE", "WAS", "WHO", "WHY", "YOU", "CEO", "CFO",
    "IPO", "ETF", "ATH", "DD", "YOLO", "FOMO", "HODL", "IMO", "EPS", "PE", "FED",
    "GDP", "CPI", "SEC", "FDA", "NYSE", "USA", "WSB", "TLDR", "EV", "AH", "PR",
    "Q1", "Q2", "Q3", "Q4", "YTD", "EOD", "BTC", "ETH",
}

# ---- 급상승 판정(하이브리드) ----
HISTORY_DAYS = 14       # z-score 산출용 과거 일수
MIN_HISTORY = 5         # 과거가 이 미만이면 z-score 대신 배수 폴백(워밍업)
SURGE_MULT = 3.0        # 워밍업/σ≈0 폴백: 평소 평균의 N배↑
SURGE_Z = 2.0           # 성숙: today ≥ μ + Z·σ
NEW_MIN = 5             # 신규 등장(과거 0)으로 분류할 당일 가중수 바닥
SIGMA_EPS = 1e-6

# ---- Claude 심리분석 ----
CLAUDE_MODEL = "claude-sonnet-4-6"   # 비용효율 선택(품질 더 원하면 claude-opus-4-8)
MAX_ANALYZE = 12          # 분석할 급상승 종목 상한(토큰/비용 절약)
MAX_TWEETS_PER_TICKER = 15
SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": ["강세", "약세", "중립"]},
        "confidence": {"type": "number"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["sentiment", "confidence", "keywords", "reason"],
    "additionalProperties": False,
}


def _retry(fn, *args, **kwargs):
    """네트워크 호출 지수 백오프 재시도."""
    delay, last = 2, None
    for attempt in range(1, 5):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("호출 재시도 (%s/4): %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
    raise last


# ---- Apify 수집 ----

def _fetch_tweets(token, since_dt):
    """Apify 액터를 동기 실행해 트윗 데이터셋을 받는다."""
    since_date = since_dt.strftime("%Y-%m-%d")
    # apidojo 액터는 twitterHandles(핸들 배열)를 네이티브 지원 → from:검색어보다 안정적.
    # 날짜 하한(start)도 함께 주되, 최종 24h 컷은 aggregate()의 in-code 필터가 담당.
    payload = {
        "twitterHandles": [h.lstrip("@") for h in accounts.handles()],
        "sort": "Latest",
        "maxItems": APIFY_MAX_ITEMS,
        "start": since_date,
    }
    url = f"{APIFY_BASE}/{APIFY_ACTOR}/run-sync-get-dataset-items?token={token}"

    def _call():
        r = requests.post(url, json=payload, timeout=300)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Apify 인증 실패({r.status_code}) — APIFY_TOKEN 확인")
        if r.status_code == 402:
            raise RuntimeError("Apify 크레딧 소진(402) — Apify 콘솔에서 크레딧 확인")
        r.raise_for_status()
        return r.json()

    data = _retry(_call)
    if isinstance(data, list):
        return data
    # run-info dict 등으로 올 때 데이터셋 항목 키 폴백
    for key in ("items", "data", "results"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return []


def _tweet_field(t, *names):
    for n in names:
        v = t.get(n)
        if v:
            return v
    return None


def _tweet_text(t):
    return _tweet_field(t, "fullText", "full_text", "text", "rawContent") or ""


def _tweet_handle(t):
    author = t.get("author") or t.get("user") or {}
    if isinstance(author, dict):
        h = author.get("userName") or author.get("username") or author.get("screen_name")
        if h:
            return h
    return _tweet_field(t, "username", "userName", "screen_name")


def _tweet_time(t):
    raw = _tweet_field(t, "createdAt", "created_at", "date", "timestamp")
    if not raw:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


# ---- 정제 + 집계 ----

def _is_spam(text):
    if _PROMO.search(text):
        return True
    if len(_CASHTAG.findall(text)) > MAX_CASHTAGS:
        return True
    return False


def aggregate(tweets, since_dt):
    """트윗 → 가중/원시 언급수. 캐시태그가 그날의 유효 티커 집합을 정의하고,
    맨대문자는 그 집합에 한해 보조 카운트(노이즈 차단)."""
    fresh = []
    for t in tweets:
        ts = _tweet_time(t)
        if ts is not None and ts < since_dt:
            continue
        text = _tweet_text(t)
        if not text or _is_spam(text):
            continue
        fresh.append((text, accounts.weight_for(_tweet_handle(t))))

    # 1차: 캐시태그로 유효 티커 집합 산출
    universe = set()
    for text, _w in fresh:
        for m in _CASHTAG.findall(text):
            universe.add(m.upper())

    weighted, raw = {}, {}
    for text, w in fresh:
        upper = text.upper()
        seen = set()
        for m in _CASHTAG.findall(text):
            seen.add(m.upper())
        # 맨대문자: 유효 티커 집합에 있고 블랙리스트 아닌 것만
        for m in _UPPER.findall(text):
            if m in universe and m not in _BLACKLIST:
                seen.add(m)
        for tk in seen:
            weighted[tk] = weighted.get(tk, 0) + w
            raw[tk] = raw.get(tk, 0) + 1

    return weighted, raw, len(fresh)


# ---- 스냅샷 I/O ----

def _path(date_str):
    return os.path.join(SNAPSHOTS_DIR, f"{date_str}.json")


def save_snapshot(date_str, weighted, raw, n_tweets):
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    with open(_path(date_str), "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "weighted": weighted, "raw": raw,
                   "tweets": n_tweets}, f, ensure_ascii=False, indent=2)
    log.info("스냅샷 저장: %s (티커 %d, 트윗 %d)", date_str, len(weighted), n_tweets)


def _load_history(today_str, max_days=HISTORY_DAYS):
    """today 이전의 스냅샷 weighted 맵 리스트(존재하는 것만, 최신→과거)."""
    out = []
    base = datetime.strptime(today_str, "%Y%m%d")
    for back in range(1, max_days * 3):  # 휴일 갭 대비 넉넉히 거슬러
        d = (base - timedelta(days=back)).strftime("%Y%m%d")
        p = _path(d)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f).get("weighted", {}))
        if len(out) >= max_days:
            break
    return out


# ---- 급상승 판정 ----

def detect_surges(weighted, history):
    """하이브리드(z-score + 워밍업 배수). 급상승/신규 종목 리스트 반환."""
    n_hist = len(history)
    surges = []
    for tk, today in weighted.items():
        if today < MIN_WEIGHTED:
            continue
        prior = [h.get(tk, 0) for h in history]
        avg = statistics.fmean(prior) if prior else 0.0

        if avg == 0:  # 신규 등장
            if today >= NEW_MIN:
                surges.append({"ticker": tk, "today": today, "mu": 0.0, "sigma": 0.0,
                               "ratio": None, "method": "신규", "is_new": True})
            continue

        if n_hist < MIN_HISTORY:  # 워밍업 → 배수
            if today >= SURGE_MULT * avg:
                surges.append({"ticker": tk, "today": today, "mu": avg, "sigma": 0.0,
                               "ratio": today / avg, "method": "배수", "is_new": False})
            continue

        # 성숙 → z-score (σ≈0이면 배수 폴백)
        sigma = statistics.pstdev(prior)
        if sigma < SIGMA_EPS:
            if today >= SURGE_MULT * avg:
                surges.append({"ticker": tk, "today": today, "mu": avg, "sigma": sigma,
                               "ratio": today / avg, "method": "배수", "is_new": False})
            continue
        z = (today - avg) / sigma
        if today >= avg + SURGE_Z * sigma:
            surges.append({"ticker": tk, "today": today, "mu": avg, "sigma": sigma,
                           "ratio": None, "z": z, "method": "z-score", "is_new": False})

    surges.sort(key=lambda s: s["today"], reverse=True)
    return surges


# ---- Claude 심리분석 ----

def _ticker_tweets(tweets, ticker, since_dt):
    """해당 티커를 언급한 당일 트윗 텍스트 모음."""
    tag = f"${ticker}".upper()
    out = []
    for t in tweets:
        ts = _tweet_time(t)
        if ts is not None and ts < since_dt:
            continue
        text = _tweet_text(t)
        if not text:
            continue
        up = text.upper()
        if tag in up or re.search(rf"(?<![\w$])\b{ticker}\b", up):
            out.append(" ".join(text.split()))
        if len(out) >= MAX_TWEETS_PER_TICKER:
            break
    return out


def analyze_sentiment(client, ticker, texts):
    """트윗 모음 → 시장심리 + 연관 키워드(JSON 강제)."""
    joined = "\n".join(f"- {x}" for x in texts)
    prompt = (
        f"다음은 미국 주식 ${ticker}에 대한 X(트위터) FinTwit 트윗들이다. "
        f"종합적인 시장심리와 새롭게 떠오르는 연관 파생 키워드를 도출하라.\n\n"
        f"{joined}\n\n"
        "한국어로 답하라. sentiment는 강세/약세/중립 중 하나, confidence는 0~1, "
        "keywords는 종목과 엮여 떠오르는 핵심 테마/이슈 2~4개, reason은 1줄 근거."
    )
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": SENTIMENT_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return json.loads(text)


def _run_analysis(surges, tweets, since_dt):
    """상위 급상승 종목에 대해 Claude 심리분석(실패는 종목별로 건너뜀)."""
    try:
        import anthropic
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
    except Exception as exc:  # noqa: BLE001
        log.warning("Claude 초기화 실패 — 심리분석 생략: %s", exc)
        return

    for s in surges[:MAX_ANALYZE]:
        texts = _ticker_tweets(tweets, s["ticker"], since_dt)
        if not texts:
            continue
        try:
            s["analysis"] = analyze_sentiment(client, s["ticker"], texts)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s 심리분석 실패(건너뜀): %s", s["ticker"], exc)
        time.sleep(0.5)


# ---- 출력 ----

def _surge_line(s):
    a = s.get("analysis") or {}
    senti = f" · {a['sentiment']}({a.get('confidence', 0):.1f})" if a else ""
    kw = " · 키워드: " + ", ".join(a["keywords"]) if a.get("keywords") else ""
    if s["method"] == "신규":
        stat = "신규 등장"
    elif s["method"] == "z-score":
        stat = f"μ{s['mu']:.0f}±{s['sigma']:.0f} → z+{s.get('z', 0):.1f}"
    else:
        stat = f"평소 {s['mu']:.0f} → {s['ratio']:.1f}배"
    reason = f' / "{a["reason"]}"' if a.get("reason") else ""
    return f"${s['ticker']} — 가중 {s['today']:.0f} ({stat}){senti}{kw}{reason}"


def write_notion(db_id, date_str, surges, weighted, n_tweets, has_history):
    title = f"\U0001f525 X 화제종목 {date_str}"
    blocks = [notion_client.heading(
        f"X FinTwit 화제종목 {date_str} (최근 24h · 트윗 {n_tweets} · 티커 {len(weighted)})", 2)]

    if not has_history:
        blocks.append(notion_client.paragraph(
            "첫 실행 — 베이스라인 저장 완료. 다음 실행부터 급상승 비교(워밍업 배수→z-score)가 시작됩니다."))
    elif surges:
        blocks.append(notion_client.heading(f"\U0001f680 급상승 ({len(surges)}종목)", 3))
        for s in surges:
            blocks.append(notion_client.bullet(_surge_line(s)))
    else:
        blocks.append(notion_client.paragraph("급상승 종목 없음(평소 수준)"))

    blocks.append(notion_client.heading("전체 가중 언급 상위 20", 3))
    for tk, w in sorted(weighted.items(), key=lambda x: -x[1])[:20]:
        blocks.append(notion_client.bullet(f"${tk} — 가중 {w:.0f}"))

    notion_client.create_page_in_database(db_id, title, blocks)
    log.info("Notion 저장 완료: %s", title)


# ---- 메인 ----

def main():
    cfg = config.require([
        "NOTION_TOKEN", "NOTION_DB_A", "APIFY_TOKEN", "ANTHROPIC_API_KEY",
    ])
    db_id = cfg["NOTION_DB_A"]

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y%m%d")
    since_dt = now - timedelta(hours=LOOKBACK_HOURS)

    try:
        log.info("Apify 수집 시작 (계정 %d, 최근 %dh)", len(accounts.handles()), LOOKBACK_HOURS)
        tweets = _fetch_tweets(cfg["APIFY_TOKEN"], since_dt)
        log.info("트윗 %d건 수집", len(tweets))

        weighted, raw, n_tweets = aggregate(tweets, since_dt)
        log.info("집계 완료: 티커 %d (유효 트윗 %d)", len(weighted), n_tweets)

        # 진단: 수집이 비정상적으로 적거나 유효 트윗 0이면 실제 응답 스키마를 남긴다
        if tweets and (len(tweets) < len(accounts.handles()) or n_tweets == 0):
            sample = tweets[0]
            keys = list(sample.keys()) if isinstance(sample, dict) else type(sample).__name__
            log.info("진단: 응답 적음 — 첫 항목 키=%s", keys)
            log.info("진단: 샘플 text=%r time=%r handle=%r",
                     _tweet_text(sample)[:120], _tweet_time(sample), _tweet_handle(sample))
        save_snapshot(today, weighted, raw, n_tweets)

        history = _load_history(today)
        has_history = len(history) > 0
        surges = detect_surges(weighted, history) if has_history else []
        log.info("급상승 %d종목 (과거 %d일)", len(surges), len(history))

        if surges:
            _run_analysis(surges, tweets, since_dt)

        write_notion(db_id, today, surges, weighted, n_tweets, has_history)

        # Telegram
        if not has_history:
            summary = f"{today} 첫 실행 — 베이스라인 저장({len(weighted)}티커). 내일부터 급상승 비교."
        elif not surges:
            summary = f"{today} 급상승 없음 (트윗 {n_tweets} · 티커 {len(weighted)})"
        else:
            mode = "z-score" if len(history) >= MIN_HISTORY else "워밍업(배수)"
            head = surges[0]
            ha = head.get("analysis") or {}
            tip = f" {ha['sentiment']}" if ha else ""
            summary = (f"{today} \U0001f680급상승 {len(surges)}종목 [{mode}] "
                       f"1위 ${head['ticker']}{tip} — 자세한 목록은 Notion A")
        notify.notify_success(STAGE, summary)

    except Exception as exc:
        notify.notify_error(STAGE, exc)
        raise


if __name__ == "__main__":
    main()
