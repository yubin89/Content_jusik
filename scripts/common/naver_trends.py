"""네이버 데이터랩 검색어트렌드 API — 종목명 검색 관심도를 실제 데이터로 채점.

NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 미설정 시 조용히 (None, "") 반환하고
호출부가 Claude의 정성적 판단으로 대체하게 한다(graceful degradation).

무료 API, 일 25,000회 한도 — 후보 몇 개만 조회하므로 충분히 여유롭다.
"""
from datetime import datetime, timedelta

import requests

from . import config
from .logger import get_logger

log = get_logger("naver_trends")

_API = "https://openapi.naver.com/v1/datalab/search"
_TIMEOUT = 15
_RECENT_DAYS = 2     # '최근'으로 볼 구간
_BASELINE_DAYS = 12  # 비교 기준(베이스라인) 구간
_SPIKE_RATIO = 1.5   # 이 배수 이상이면 급상승(2점)
_RISE_RATIO = 1.1    # 이 배수 이상이면 완만한 상승(1점)


def configured():
    return bool(config.get_optional("NAVER_CLIENT_ID") and config.get_optional("NAVER_CLIENT_SECRET"))


def trend_score(keyword):
    """검색 관심도 점수(0~2, 최근 vs 베이스라인 비교) + 근거 문자열.
    미설정/실패 시 (None, "") — 호출부가 Claude 정성 판단으로 대체."""
    if not keyword or not configured():
        return None, ""

    client_id = config.get("NAVER_CLIENT_ID")
    client_secret = config.get("NAVER_CLIENT_SECRET")
    today = datetime.now()
    start = (today - timedelta(days=_RECENT_DAYS + _BASELINE_DAYS)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    payload = {
        "startDate": start,
        "endDate": end,
        "timeUnit": "date",
        "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
    }
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(_API, json=payload, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        points = data.get("results", [{}])[0].get("data", [])
        if not points:
            return None, ""

        ratios = [p.get("ratio", 0) for p in points]
        recent = ratios[-_RECENT_DAYS:]
        baseline = ratios[:-_RECENT_DAYS] if len(ratios) > _RECENT_DAYS else []

        recent_avg = sum(recent) / len(recent) if recent else 0
        baseline_avg = sum(baseline) / len(baseline) if baseline else 0

        if baseline_avg <= 0.01:
            # 평소 검색량이 거의 없던 종목 — 최근 절대치로만 판단
            if recent_avg >= 5:
                return 2, f"평소 무관심 → 최근 급증(비율 {recent_avg:.1f})"
            if recent_avg > 0:
                return 1, f"평소 무관심 → 소폭 유입(비율 {recent_avg:.1f})"
            return 0, "검색 관심 없음"

        change = recent_avg / baseline_avg
        if change >= _SPIKE_RATIO:
            return 2, f"검색량 급상승(최근 {recent_avg:.1f} vs 평소 {baseline_avg:.1f}, {change:.1f}배)"
        if change >= _RISE_RATIO:
            return 1, f"검색량 완만 상승({change:.1f}배)"
        return 0, f"검색량 평이({change:.1f}배)"
    except Exception as exc:
        log.warning("네이버 검색트렌드 조회 실패(%s, 건너뜀): %s", keyword, exc)
        return None, ""
