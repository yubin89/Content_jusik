"""Unsplash 무료 스톡 사진 검색·다운로드 — 콘텐츠 관련 '실사' 이미지.

키워드로 관련 진짜 사진을 가져온다(AI 아님 → AI티 0). 무료·상업적 사용 가능하나
Unsplash 가이드라인상 '출처 표기(attribution)'가 필요하므로 캡션에 넣는다.

필요 환경변수: UNSPLASH_ACCESS_KEY (선택). 없으면 조용히 생략.
"""
import requests

from . import config
from .logger import get_logger

log = get_logger("stock_photo")

_API = "https://api.unsplash.com"
_TIMEOUT = 30


def search_photo(query):
    """키워드로 가로형 사진 1장을 반환.
    성공: {bytes, content_type, credit_name, credit_url}. 실패/미설정: None."""
    key = config.get_optional("UNSPLASH_ACCESS_KEY")
    if not key:
        log.info("UNSPLASH_ACCESS_KEY 미설정 — 실사 사진 생략")
        return None
    if not query:
        return None

    headers = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}
    try:
        r = requests.get(
            f"{_API}/search/photos",
            params={"query": query, "per_page": 1, "orientation": "landscape",
                    "content_filter": "high"},
            headers=headers, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            log.info("Unsplash 검색결과 없음: %s", query)
            return None
        photo = results[0]

        img_url = photo["urls"]["regular"]
        credit_name = photo.get("user", {}).get("name", "Unsplash")
        credit_url = photo.get("user", {}).get("links", {}).get("html", "https://unsplash.com")

        # Unsplash 가이드라인: 다운로드 트리거(집계용) — 실패해도 무시
        try:
            dl = photo.get("links", {}).get("download_location")
            if dl:
                requests.get(dl, headers=headers, timeout=_TIMEOUT)
        except Exception:
            pass

        img = requests.get(img_url, timeout=_TIMEOUT)
        img.raise_for_status()
        return {
            "bytes": img.content,
            "content_type": img.headers.get("Content-Type", "image/jpeg"),
            "credit_name": credit_name,
            "credit_url": credit_url,
        }
    except Exception as exc:
        log.warning("Unsplash 실사 사진 실패(생략): %s", exc)
        return None
