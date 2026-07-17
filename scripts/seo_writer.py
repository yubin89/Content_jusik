"""
scripts/seo_writer.py — 노션 D → SEO 블로그 글 작성 → 워드프레스 초안 저장 (Step 6)

노션 D(콘텐츠 기획 요약)의 최신 페이지를 읽어 Claude에게 구글 SEO에 최적화된
한국어 블로그 글을 작성시키고, 워드프레스(WordPress.org)에 '초안(draft)'으로 저장한다.
초안이므로 사람이 검토·수정한 뒤 직접 발행한다(자동 발행 아님).

필요 환경변수(GitHub Secrets):
  NOTION_TOKEN, NOTION_DB_D, ANTHROPIC_API_KEY
  WP_URL, WP_USER, WP_APP_PASSWORD
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import sys
import traceback
from datetime import datetime

import anthropic
import pytz
import requests

from scripts.common import config, notify
from scripts.common.logger import get_logger
import scripts.common.notion_client as nc

log = get_logger("seo_writer")
STAGE = "SEO 글 작성"
MODEL = "claude-opus-4-8"   # 발행용 콘텐츠라 품질 우선(구글 노출 목적)
KST = pytz.timezone("Asia/Seoul")
MAX_SOURCE_LEN = 6000       # 노션 D 입력 최대 길이
HTTP_TIMEOUT = 60


_SYSTEM_PROMPT = """\
당신은 한국 주식/투자 분야의 SEO 콘텐츠 전문 작가입니다.
오늘의 콘텐츠 기획 요약을 바탕으로 구글 검색 상위 노출을 노리는
한국어 블로그 글 한 편을 작성하세요.

[SEO 작성 규칙]
- 제목(title): 30~60자, 핵심 키워드를 앞쪽에 배치, 클릭을 부르되 낚시성 금지
- 메타설명(meta_description): 70~155자, 검색결과에 노출될 요약. 키워드 자연 포함
- 본문(content_html): 순수 HTML. <h2> 소제목 3~5개로 구조화, <p> 문단, 필요시 <ul><li>
  · 도입부에 핵심 키워드 자연스럽게 포함
  · 각 종목/이슈를 소제목으로 나눠 설명
  · 800~1500자 분량, 사람이 읽기 좋은 문체(AI 티 안 나게)
  · 과장·단정 표현 금지("무조건 오른다" 같은 표현 X)
  · 글 마지막에 반드시 투자 유의 문구 문단 추가:
    <p><em>본 콘텐츠는 정보 제공 목적이며 투자 권유가 아닙니다. 투자 판단과 책임은 본인에게 있습니다.</em></p>
- 태그(tags): 3~6개, 종목명·테마·키워드 위주

[응답 형식] 반드시 아래 JSON만 출력. 다른 텍스트 없이:
{
  "title": "제목",
  "meta_description": "메타설명",
  "tags": ["태그1", "태그2"],
  "content_html": "<h2>...</h2><p>...</p>"
}
"""


def _read_source():
    """노션 D 최신 기획 요약 텍스트를 반환. 없으면 None."""
    db_id = config.get("NOTION_DB_D")
    page = nc.query_latest_page(db_id)
    if not page:
        return None
    text = nc.read_page_text(page["id"])
    return text[:MAX_SOURCE_LEN] if len(text) > MAX_SOURCE_LEN else text


def _call_claude(source_text, date_str):
    """Claude에게 SEO 글 작성 요청 후 파싱된 dict 반환."""
    user_msg = (
        f"[{date_str} 콘텐츠 기획 요약]\n{source_text}\n\n"
        "위 기획을 바탕으로 SEO 블로그 글을 작성하세요."
    )
    api_client = anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))
    response = api_client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


# ---- 워드프레스 REST API ----

def _wp_auth():
    return (config.get("WP_USER"), config.get("WP_APP_PASSWORD"))


def _wp_base():
    return config.get("WP_URL").rstrip("/") + "/wp-json/wp/v2"


def _resolve_tag_ids(tag_names):
    """태그 이름 → 워드프레스 태그 ID로 변환(없으면 생성). 실패해도 글 저장은 막지 않음."""
    ids = []
    base = _wp_base()
    auth = _wp_auth()
    for name in tag_names:
        try:
            # 이미 있으면 검색으로 찾기
            r = requests.get(
                f"{base}/tags", params={"search": name}, auth=auth, timeout=HTTP_TIMEOUT
            )
            r.raise_for_status()
            found = [t for t in r.json() if t.get("name") == name]
            if found:
                ids.append(found[0]["id"])
                continue
            # 없으면 새로 생성
            c = requests.post(
                f"{base}/tags", json={"name": name}, auth=auth, timeout=HTTP_TIMEOUT
            )
            c.raise_for_status()
            ids.append(c.json()["id"])
        except Exception as exc:
            log.warning("태그 '%s' 처리 실패(건너뜀): %s", name, exc)
    return ids


def _create_draft(article, date_str):
    """워드프레스에 초안(draft) 글을 만들고 편집 URL을 반환."""
    payload = {
        "title": article.get("title", f"주목 종목 브리핑 {date_str}"),
        "content": article.get("content_html", ""),
        "excerpt": article.get("meta_description", ""),
        "status": "draft",
    }
    tag_ids = _resolve_tag_ids(article.get("tags", []))
    if tag_ids:
        payload["tags"] = tag_ids

    resp = requests.post(
        f"{_wp_base()}/posts", json=payload, auth=_wp_auth(), timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("link") or data.get("guid", {}).get("rendered", "")


def main():
    config.require([
        "NOTION_TOKEN", "NOTION_DB_D", "ANTHROPIC_API_KEY",
        "WP_URL", "WP_USER", "WP_APP_PASSWORD",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ])

    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    log.info("=== %s 시작 — %s ===", STAGE, date_str)

    try:
        source = _read_source()
        if not source:
            msg = f"{date_str} — 노션 D에 기획 요약이 없습니다. 글 작성 생략."
            log.warning(msg)
            notify.notify_success(STAGE, msg)
            return

        log.info("Claude SEO 글 생성 중... (모델: %s)", MODEL)
        article = _call_claude(source, date_str)

        log.info("워드프레스 초안 저장 중...")
        link = _create_draft(article, date_str)
        log.info("초안 저장 완료: %s", link)

        tg_msg = "\n".join([
            f"{date_str} SEO 글 초안 완성",
            f"📝 {article.get('title', '')}",
            "✅ 워드프레스 초안에 저장됨 — 검토 후 발행하세요",
            link or "",
        ])
        notify.notify_success(STAGE, tg_msg)

    except Exception as exc:
        log.exception("SEO 글 작성 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
