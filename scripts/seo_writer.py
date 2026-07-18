"""
scripts/seo_writer.py — 노션 D → SEO 블로그 글 작성 → 워드프레스 초안 저장 (Step 6)

최근 며칠간의 노션 D(콘텐츠 기획 요약)들을 모아, 그중 콘텐츠로 가장 좋은 소재
'하나'를 골라 구글 SEO에 최적화된 한국어 블로그 글을 작성하고, 워드프레스에
'초안(draft)'으로 저장한다. 초안이므로 사람이 검토·수정한 뒤 직접 발행한다.

주 3회(월·수·금)만 실행한다 — 매일 대량 발행은 구글에 '양산형'으로 찍혀
오히려 불리하므로, 며칠 치 중 최고를 골라 품질에 집중한다.

글 생성은 3단계 비평-반영(reflection) 파이프라인:
  1) Sonnet 초안 → 2) Opus 검수(개선 가이드만) → 3) Sonnet 최종본

필요 환경변수(GitHub Secrets):
  NOTION_TOKEN, NOTION_DB_D, ANTHROPIC_API_KEY
  WP_URL, WP_USER, WP_APP_PASSWORD
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import sys
from datetime import datetime

import anthropic
import pytz
import requests

from scripts.common import config, notify
from scripts.common.logger import get_logger
import scripts.common.notion_client as nc

log = get_logger("seo_writer")
STAGE = "SEO 글 작성"

# 3단계 비평-반영 파이프라인의 모델.
# Sonnet-only로 비용을 더 줄이려면 _review(Opus) 단계를 건너뛰면 됨.
MODEL_DRAFT = "claude-sonnet-4-6"   # 1·3단계: 초안/최종 작성(저렴)
MODEL_REVIEW = "claude-opus-4-8"    # 2단계: 검수·개선 가이드(짧은 출력이라 저렴)

KST = pytz.timezone("Asia/Seoul")
RECENT_COUNT = 4            # 최근 노션 D 몇 개를 놓고 소재를 고를지
MAX_SOURCE_LEN = 4000       # 기획 요약 1개당 최대 길이
HTTP_TIMEOUT = 60


# ---- 노션 D 읽기 ----

def _read_recent_sources(n):
    """노션 D 최근 n개 기획 요약 텍스트를 최신순 리스트로 반환. 없으면 빈 리스트."""
    db_id = config.get("NOTION_DB_D")
    pages = nc.query_recent_pages(db_id, n)
    texts = []
    for page in pages:
        try:
            text = nc.read_page_text(page["id"])
            if text:
                texts.append(text[:MAX_SOURCE_LEN])
        except Exception as exc:
            log.warning("기획 요약 1건 읽기 실패(건너뜀): %s", exc)
    return texts


def _format_sources(sources):
    """여러 기획 요약을 '최근① / 최근②...' 형태의 한 덩어리로 묶는다."""
    blocks = []
    for i, text in enumerate(sources, 1):
        blocks.append(f"=== 기획 요약 {i} (최신순) ===\n{text}")
    return "\n\n".join(blocks)


# ---- Claude 3단계 파이프라인 ----

_DRAFT_SYSTEM = """\
당신은 주식 초보 개인투자자를 위한 블로그 작가입니다.
아래는 최근 며칠간의 콘텐츠 기획 요약들입니다. 이 중 블로그 콘텐츠로 가장 매력적인
종목/주제 '하나'만 골라, 그 주제에 집중한 글 한 편을 작성하세요.

[독자] 주식 초보 개인투자자 — 쉽고 친근한 말투, 어려운 용어는 풀어서 설명
[분량] 본문 1500~2000자
[구조] <h2> 소제목 3~5개, <p> 문단, 필요 시 <ul><li>
[SEO] 제목 30~60자(핵심 키워드 앞쪽), 메타설명 70~155자, 도입부에 키워드 자연 포함
[주의] 과장·단정 표현 금지("무조건 오른다" 류 X).
       글 마지막에 반드시 아래 문단 추가:
       <p><em>본 콘텐츠는 정보 제공 목적이며 투자 권유가 아닙니다. 투자 판단과 책임은 본인에게 있습니다.</em></p>

[응답 형식] 반드시 아래 JSON만 출력. 다른 텍스트 없이:
{
  "title": "제목",
  "meta_description": "메타설명",
  "tags": ["태그1", "태그2"],
  "content_html": "<h2>...</h2><p>...</p>"
}
"""

_REVIEW_SYSTEM = """\
당신은 한국 주식/투자 블로그의 SEO 편집장입니다.
아래 블로그 초안을 검수하세요. 글을 직접 다시 쓰지 말고, 개선점만 한국어 불릿으로
구체적으로 지적하세요(무엇을, 어떻게 고칠지).

[점검 항목]
- 제목·메타설명이 SEO에 맞는지(길이, 키워드 위치, 클릭 유도)
- 키워드가 자연스러운지, 억지스럽지 않은지
- 구조·가독성(소제목 흐름, 문단 길이)
- 주식 초보 눈높이에 맞는지(어려운 용어 방치 여부)
- 과장·단정 표현, 신뢰성 문제
- 투자 유의 문구 유무

핵심 위주로 500자 이내. 개선점이 거의 없으면 "양호"라고만 쓰세요.
"""

_REVISE_SYSTEM = """\
당신은 주식 초보 개인투자자를 위한 블로그 작가입니다.
아래 [1차 초안]과 [편집장 개선 가이드]를 참고해 최종 블로그 글을 완성하세요.
가이드의 지적을 최대한 반영하되, 원래의 독자·분량·구조·SEO 규칙은 그대로 유지하세요.

[독자] 주식 초보 개인투자자 — 쉽고 친근한 말투
[분량] 본문 1500~2000자
[구조] <h2> 소제목 3~5개
[주의] 과장·단정 금지, 마지막에 투자 유의 문구 문단 유지

[응답 형식] 반드시 아래 JSON만 출력. 다른 텍스트 없이:
{
  "title": "제목",
  "meta_description": "메타설명",
  "tags": ["태그1", "태그2"],
  "content_html": "<h2>...</h2><p>...</p>"
}
"""


def _client():
    return anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))


def _call(api, model, system, user, max_tokens):
    """Claude 호출 후 응답 텍스트(문자열) 반환."""
    resp = api.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


def _parse_json(raw):
    """```json ... ``` 코드블록으로 감싸인 경우까지 대응해 dict로 파싱."""
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _generate_article(sources):
    """3단계(초안→검수→최종) 파이프라인으로 최종 기사 dict를 만든다."""
    api = _client()
    src_block = _format_sources(sources)

    # 1단계: Sonnet 초안 (소재 선별 + 작성)
    log.info("1/3 초안 작성 중... (%s)", MODEL_DRAFT)
    draft_raw = _call(
        api, MODEL_DRAFT, _DRAFT_SYSTEM,
        f"[최근 기획 요약들]\n{src_block}\n\n위에서 가장 좋은 소재 하나를 골라 글을 쓰세요.",
        max_tokens=4096,
    )

    # 2단계: Opus 검수 (개선 가이드만, 재작성 X)
    log.info("2/3 검수 중... (%s)", MODEL_REVIEW)
    guide = _call(
        api, MODEL_REVIEW, _REVIEW_SYSTEM,
        f"[검수할 초안]\n{draft_raw}",
        max_tokens=1024,
    )

    # 3단계: Sonnet 최종본 (가이드 반영)
    log.info("3/3 최종본 작성 중... (%s)", MODEL_DRAFT)
    final_raw = _call(
        api, MODEL_DRAFT, _REVISE_SYSTEM,
        f"[1차 초안]\n{draft_raw}\n\n[편집장 개선 가이드]\n{guide}\n\n"
        "위를 반영해 최종 글을 완성하세요.",
        max_tokens=4096,
    )
    return _parse_json(final_raw)


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
            r = requests.get(
                f"{base}/tags", params={"search": name}, auth=auth, timeout=HTTP_TIMEOUT
            )
            r.raise_for_status()
            found = [t for t in r.json() if t.get("name") == name]
            if found:
                ids.append(found[0]["id"])
                continue
            c = requests.post(
                f"{base}/tags", json={"name": name}, auth=auth, timeout=HTTP_TIMEOUT
            )
            c.raise_for_status()
            ids.append(c.json()["id"])
        except Exception as exc:
            log.warning("태그 '%s' 처리 실패(건너뜀): %s", name, exc)
    return ids


def _create_draft(article, date_str):
    """워드프레스에 초안(draft) 글을 만들고 편집/미리보기 URL을 반환."""
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
        sources = _read_recent_sources(RECENT_COUNT)
        if not sources:
            msg = f"{date_str} — 노션 D에 기획 요약이 없습니다. 글 작성 생략."
            log.warning(msg)
            notify.notify_success(STAGE, msg)
            return
        log.info("기획 요약 %d건 확보 — 소재 선별 후 작성", len(sources))

        article = _generate_article(sources)

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
