"""Notion 쓰기/읽기 헬퍼.

- 데이터베이스에 페이지(행) 생성
- 페이지에 블록(본문) 추가 (100개/요청 한도에 맞춰 청크 분할)
- DB 최신 페이지 조회 / 페이지 텍스트 읽기 (requests 직접 호출 — SDK 버전 무관)
- rate-limit(429)/일시 오류(5xx)에 지수 백오프 재시도
"""
import time

import requests as _req
from notion_client import Client
from notion_client.errors import (
    APIResponseError,
    HTTPResponseError,
    RequestTimeoutError,
)

from . import config
from .logger import get_logger

log = get_logger("notion")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_client = None


def client():
    global _client
    if _client is None:
        # Notion API 버전을 안정적인 2022-06-28로 고정한다.
        # (최신 버전은 데이터베이스를 'data sources' 구조로 반환해
        #  databases.retrieve 응답에 properties가 최상위로 오지 않음)
        _client = Client(auth=config.get("NOTION_TOKEN"), notion_version="2022-06-28")
    return _client


def _retry(fn, *args, **kwargs):
    """Notion API 호출을 재시도. 재시도 불가 오류는 즉시 올린다."""
    delay = 2
    last_exc = None
    for attempt in range(1, 5):
        try:
            return fn(*args, **kwargs)
        except APIResponseError as exc:
            last_exc = exc
            if getattr(exc, "status", None) not in _RETRYABLE_STATUS:
                raise
            log.warning("Notion 재시도 (%s/4, status=%s): %s", attempt, exc.status, exc)
        except (HTTPResponseError, RequestTimeoutError) as exc:
            last_exc = exc
            log.warning("Notion 재시도 (%s/4): %s", attempt, exc)
        time.sleep(delay)
        delay *= 2
    raise last_exc


def _title_prop_name(database_id):
    """데이터베이스의 title 속성 이름을 찾는다(DB마다 이름이 다를 수 있음)."""
    db = _retry(client().databases.retrieve, database_id=database_id)
    for name, prop in db["properties"].items():
        if prop["type"] == "title":
            return name
    raise RuntimeError("이 데이터베이스에 title 속성이 없습니다. 노션 DB 설정을 확인하세요.")


def create_page_in_database(database_id, title, blocks=None):
    """DB에 새 페이지(행)를 만들고, 있으면 본문 블록을 추가한다."""
    title_prop = _title_prop_name(database_id)
    props = {title_prop: {"title": [{"text": {"content": title[:2000]}}]}}
    page = _retry(
        client().pages.create,
        parent={"database_id": database_id},
        properties=props,
    )
    if blocks:
        append_blocks(page["id"], blocks)
    return page


def append_blocks(page_id, blocks):
    """본문 블록을 100개씩 나눠서 추가."""
    for i in range(0, len(blocks), 100):
        chunk = blocks[i : i + 100]
        _retry(client().blocks.children.append, block_id=page_id, children=chunk)


# ---- 블록 생성 도우미 ----

def paragraph(text):
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def heading(text, level=2):
    key = f"heading_{level}"
    return {
        "object": "block",
        "type": key,
        key: {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def bullet(text):
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
        },
    }


# ---- 읽기 헬퍼 (Step 5 analyze.py용) — requests 직접 호출로 SDK 버전 무관 ----

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _http_headers():
    return {
        "Authorization": f"Bearer {config.get('NOTION_TOKEN')}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _http_retry(fn):
    """requests 호출 전용 재시도."""
    delay = 2
    last_exc = None
    for attempt in range(1, 5):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            log.warning("Notion HTTP 재시도 (%s/4): %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
    raise last_exc


def query_latest_page(database_id):
    """DB에서 가장 최근에 생성된 페이지 1개를 반환. 없으면 None."""
    url = f"{_NOTION_API}/databases/{database_id}/query"
    payload = {
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        "page_size": 1,
    }

    def _call():
        resp = _req.post(url, headers=_http_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    result = _http_retry(_call)
    pages = result.get("results", [])
    return pages[0] if pages else None


def read_page_text(page_id):
    """페이지 블록을 모두 읽어 plain text로 이어 붙인다(페이지네이션 지원)."""
    texts = []
    cursor = None
    while True:
        url = f"{_NOTION_API}/blocks/{page_id}/children"
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor

        def _call(u=url, p=params):
            resp = _req.get(u, headers=_http_headers(), params=p, timeout=30)
            resp.raise_for_status()
            return resp.json()

        data = _http_retry(_call)
        for block in data.get("results", []):
            btype = block.get("type", "")
            bdata = block.get(btype, {})
            rich_text = bdata.get("rich_text", [])
            line = "".join(rt.get("plain_text", "") for rt in rich_text)
            if line:
                texts.append(line)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return "\n".join(texts)
