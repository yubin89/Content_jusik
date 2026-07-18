"""쿠팡 파트너스 Open API 클라이언트.

- deeplink 생성: 원본 상품 URL → 내 추천코드(트래킹 코드)가 박힌 단축 링크
- 상품 검색: 키워드 → 대표 상품 이미지(productImage) URL 취득
  (⚠️ 합법 경로: 파트너스 API가 제공하는 '대표 이미지'만 사용.
   판매자 상세페이지 사진을 임의로 긁어 쓰는 것은 저작권 위험이므로 하지 않는다.)

키(COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY)가 없으면 조용히 비활성(graceful).
→ 링크 없이 초안만 생성되도록 해서, 파트너스 승인 전에도 파이프라인이 죽지 않게 한다.
   (notify.py의 '미설정이면 건너뛴다' 패턴과 동일 철학)
"""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone

import requests

from .logger import get_logger

log = get_logger("coupang")

_DOMAIN = "https://api-gateway.coupang.com"
_DEEPLINK_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
_SEARCH_PATH = "/v2/providers/affiliate_open_api/apis/openapi/products/search"
_TIMEOUT = 15


def is_enabled():
    """파트너스 키가 둘 다 설정되어 있으면 True."""
    return bool(os.environ.get("COUPANG_ACCESS_KEY") and os.environ.get("COUPANG_SECRET_KEY"))


def _signed_headers(method, path, query=""):
    """쿠팡 파트너스 HMAC(HmacSHA256) 인증 헤더를 만든다.

    서명 대상 메시지 = datetime + method + path + query
    (datetime 형식은 파트너스 규격인 'yyMMdd'T'HHmmss'Z' = GMT).
    """
    access_key = os.environ["COUPANG_ACCESS_KEY"]
    secret_key = os.environ["COUPANG_SECRET_KEY"]

    signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method + path + query
    signature = hmac.new(
        secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        "CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={signed_date}, "
        f"signature={signature}"
    )
    return {"Authorization": authorization, "Content-Type": "application/json;charset=UTF-8"}


def _post(path, body):
    headers = _signed_headers("POST", path)
    resp = requests.post(_DOMAIN + path, headers=headers, data=json.dumps(body), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def create_deeplink(urls):
    """원본 쿠팡 URL 리스트 → 추천코드 박힌 단축 링크 리스트.

    반환: [{"originalUrl": ..., "shortenUrl": ..., "landingUrl": ...}, ...]
    키 미설정/오류 시 빈 리스트(graceful).
    """
    if not is_enabled():
        log.warning("쿠팡 파트너스 키 미설정 — deeplink 생성을 건너뜁니다(링크 없이 초안 생성).")
        return []
    if isinstance(urls, str):
        urls = [urls]
    try:
        result = _post(_DEEPLINK_PATH, {"coupangUrls": urls})
        return result.get("data", []) or []
    except Exception as exc:  # noqa: BLE001 — 수익 링크 실패가 글 생성 전체를 막지 않게
        log.warning("deeplink 생성 실패(graceful): %s", exc)
        return []


def search_product(keyword, limit=1):
    """키워드로 상품을 검색해 대표 이미지/링크가 담긴 상품 dict 리스트를 반환.

    각 항목 주요 필드: productName, productPrice, productImage(대표 이미지 URL),
    productUrl, productId. 대표 이미지는 파트너스가 공식 제공하는 값이라 사용 가능.
    키 미설정/오류 시 빈 리스트(graceful).
    """
    if not is_enabled():
        log.warning("쿠팡 파트너스 키 미설정 — 상품 검색을 건너뜁니다.")
        return []
    path = f"{_SEARCH_PATH}?keyword={requests.utils.quote(keyword)}&limit={limit}"
    query = path.split("?", 1)[1]
    try:
        headers = _signed_headers("GET", _SEARCH_PATH, query)
        resp = requests.get(_DOMAIN + path, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return data.get("productData", []) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("상품 검색 실패(graceful): %s", exc)
        return []


def resolve_product(keyword_or_url):
    """편의 함수: 입력이 쿠팡 URL이면 deeplink만, 키워드면 검색+deeplink.

    반환: {"link": <추천링크 or None>, "image": <대표이미지 or None>,
           "name": <상품명 or None>, "price": <가격 or None>} — 없으면 값이 None.
    """
    out = {"link": None, "image": None, "name": None, "price": None}
    if not keyword_or_url:
        return out

    is_url = keyword_or_url.startswith("http")
    if is_url:
        links = create_deeplink(keyword_or_url)
        if links:
            out["link"] = links[0].get("shortenUrl") or links[0].get("landingUrl")
        return out

    # 키워드 → 대표 상품 1건 검색 후 그 상품 URL로 deeplink
    products = search_product(keyword_or_url, limit=1)
    if not products:
        return out
    p = products[0]
    out["image"] = p.get("productImage")
    out["name"] = p.get("productName")
    out["price"] = p.get("productPrice")
    product_url = p.get("productUrl")
    if product_url:
        links = create_deeplink(product_url)
        if links:
            out["link"] = links[0].get("shortenUrl") or links[0].get("landingUrl")
        else:
            out["link"] = product_url  # deeplink 실패 시 원본 URL이라도(추천코드 없음 주의)
    return out
