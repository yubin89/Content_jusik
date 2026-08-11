"""
scripts/econ_news.py — 한국·미국 경제 뉴스 해설 → 워드프레스 초안 저장

월~금 매일 실행. Claude의 웹검색 도구로 오늘/어제자 한국·미국 경제 뉴스를
찾아 '원본 해설'을 작성한다(뉴스 원문 짜깁기 금지 — 중복 콘텐츠는 SEO 감점 +
저작권 위험). 여러 소스를 재료로만 쓰고 출처는 본문에 링크로 명시한다.

노션 수집 데이터와 무관한 독립 콘텐츠라 NOTION_DB_D를 쓰지 않는다.
발행량이 잦아(주 5회) Sonnet 단일 패스로 가볍게 간다 — 표·FAQ는 그대로 유지하되
Opus 검수 단계는 생략(사람이 초안을 매번 검토하므로).
언제나 워드프레스 '초안'으로만 저장 — 사람이 확인한 것만 실제 발행한다.

필요 환경변수(GitHub Secrets):
  ANTHROPIC_API_KEY, WP_URL, WP_USER, WP_APP_PASSWORD
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  UNSPLASH_ACCESS_KEY (선택) — 있으면 본문에 실사 사진도 삽입
"""
import sys
import urllib.parse
from datetime import datetime

import pytz
import requests

from scripts.common import ai, config, notify, stock_photo, wp_publish
from scripts.common.logger import get_logger

log = get_logger("econ_news")
STAGE = "경제뉴스 작성"

KST = pytz.timezone("Asia/Seoul")
IMAGE_GEN_TIMEOUT = 120
FEATURED_SIZE = (1200, 630)

_WRITE_SYSTEM = """\
당신은 개인투자자 대상 경제 뉴스 해설 블로그 작가입니다.
웹검색 도구로 '오늘 또는 어제' 발표된 한국·미국 경제/증시 관련 주요 뉴스를
조사한 뒤, 그 내용을 재료로 삼아 '원본 해설 글' 한 편을 작성하세요.

[매우 중요 — 저작권/SEO]
- 뉴스 기사를 그대로 베끼거나 짜깁기하지 마세요. 반드시 당신의 언어로
  재구성한 분석/해설이어야 합니다(중복 콘텐츠는 구글 SEO에 불리하고 저작권 위험).
- 인용한 사실에는 본문에 <a href="URL" target="_blank" rel="noopener">출처명</a> 링크로
  근거를 명시하세요(E-E-A-T 신뢰성).
- 검색 중에는 아무 텍스트도 출력하지 말고, 조사가 끝난 뒤 마지막에
  최종 JSON'만' 출력하세요.

[소재 선택] 오늘 다루기 좋은 한국 또는 미국 경제/증시 뉴스 1~2개를 골라
(둘 다 다룰 경우 자연스럽게 연결), 개인투자자에게 왜 중요한지 중심으로 작성.

[독자] 입문~초중급 개인투자자. 쉽고 명확하게, 유치한 비유 금지.
[분량] 본문 1200~1700자(표·FAQ 제외)

[필수 구성 요소]
- 본문 중간에 요약 표 1개(HTML). 첫 열은 항목명, 3~4행:
  <table class="ms-tbl"><thead><tr><th>항목</th><th>내용</th><th>확인 포인트</th></tr></thead><tbody><tr><td>...</td><td>...</td><td>...</td></tr></tbody></table>
- 글 끝(투자 유의 문구 바로 앞)에 FAQ 3~4개(HTML):
  <div class="ms-faq"><p class="ms-q">Q. 질문</p><p class="ms-a">A. 답변</p> ...반복... </div>
[구조] <h2> 소제목 3~4개
[SEO] 제목 30~60자(핵심 키워드 앞쪽), 메타설명 70~155자
[주의] 과장·단정 금지("무조건 오른다" 류 X). 글 마지막에 반드시:
       <p><em>본 콘텐츠는 정보 제공 목적이며 투자 권유가 아닙니다. 투자 판단과 책임은 본인에게 있습니다.</em></p>

[응답 형식] 검색과 사고가 끝나면 반드시 아래 JSON'만' 마지막 텍스트로 출력:
{
  "title": "제목",
  "meta_description": "메타설명",
  "tags": ["태그1", "태그2"],
  "topic_query": "본문 실사 사진 검색용 영어 키워드 1~3단어(예: stock market trading floor)",
  "content_html": "<h2>...</h2><p>...</p>"
}
"""

_IMAGE_SYSTEM = """\
너는 한국 경제뉴스 블로그 '대표 이미지'용 정보를 만든다.

[규칙]
- featured_prompt: '영어' 이미지 생성 프롬프트. 추상·개념적 금융/경제 이미지만
  (도시 스카이라인, 주식시세판 실루엣, 밝고 현대적인 금융 무드 등).
  글자·숫자·실제 기업 로고·상표·실존 인물은 절대 금지.
- featured_alt: 대표 이미지의 '한국어' 대체텍스트(ALT). 핵심 키워드 포함 한 문장.

[출력] 아래 JSON만. 다른 텍스트 없이:
{"featured_prompt": "...", "featured_alt": "..."}
"""


def _generate_article():
    """웹검색 기반 경제뉴스 해설 1편 생성. article dict 반환."""
    api = ai.client()
    log.info("경제뉴스 조사·작성 중... (%s, 웹검색)", ai.MODEL_DRAFT)
    raw = ai.call(
        api, ai.MODEL_DRAFT, _WRITE_SYSTEM,
        "오늘 다룰 만한 한국·미국 경제 뉴스를 검색해서 해설 글을 작성하세요.",
        max_tokens=8000,
        tools=[ai.WEB_SEARCH_TOOL],
    )
    return ai.parse_json(raw)


def _generate_image_meta(article):
    api = ai.client()
    user = f"제목: {article.get('title', '')}\n요약: {article.get('meta_description', '')}"
    raw = ai.call(api, ai.MODEL_DRAFT, _IMAGE_SYSTEM, user, max_tokens=400)
    return ai.parse_json(raw)


def _pollinations_image(prompt, width, height):
    enc = urllib.parse.quote(prompt or "abstract finance concept", safe="")
    url = f"https://image.pollinations.ai/prompt/{enc}?width={width}&height={height}&nologo=true"
    resp = requests.get(url, timeout=IMAGE_GEN_TIMEOUT)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "image/jpeg")


def _attach_visuals(article, date_str):
    """실사 사진 + 대표 이미지를 붙인다. featured_media id 반환(없으면 None).
    각 단계 best-effort — 실패해도 글은 저장."""
    featured_id = None

    # 본문: 콘텐츠 관련 실사 사진 (Unsplash, 출처 표기)
    try:
        photo = stock_photo.search_photo(article.get("topic_query"))
        if photo:
            _, src = wp_publish.upload_media(
                photo["bytes"], photo["content_type"], f"photo-{date_str}",
                alt=article.get("title", ""))
            if src:
                credit = f'사진: {photo["credit_name"]} / Unsplash'
                article["content_html"] = wp_publish.insert_inline_image(
                    article.get("content_html", ""), src, article.get("title", ""),
                    caption=credit, before_h2=1,
                )
                log.info("본문 실사 사진 삽입 완료")
    except Exception as exc:
        log.warning("실사 사진 실패(생략): %s", exc)

    # 대표 이미지: AI 컨셉
    try:
        meta = _generate_image_meta(article)
        img, ctype = _pollinations_image(meta.get("featured_prompt", ""), *FEATURED_SIZE)
        featured_id, _ = wp_publish.upload_media(
            img, ctype, f"featured-{date_str}", alt=meta.get("featured_alt"))
        log.info("대표 이미지 업로드 완료 (media %s)", featured_id)
    except Exception as exc:
        log.warning("대표 이미지 실패(생략): %s", exc)

    return featured_id


def main():
    config.require([
        "ANTHROPIC_API_KEY", "WP_URL", "WP_USER", "WP_APP_PASSWORD",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ])

    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    log.info("=== %s 시작 — %s ===", STAGE, date_str)

    try:
        article = _generate_article()
        featured_id = _attach_visuals(article, date_str)

        log.info("워드프레스 초안 저장 중...")
        link = wp_publish.create_draft(
            title=article.get("title"),
            content_html=article.get("content_html", ""),
            excerpt=article.get("meta_description", ""),
            tags=article.get("tags", []),
            featured_media=featured_id,
            fallback_title=f"경제뉴스 브리핑 {date_str}",
        )
        log.info("초안 저장 완료: %s", link)

        tg_lines = [
            f"{date_str} 경제뉴스 초안 완성",
            f"📰 {article.get('title', '')}",
            "✅ 워드프레스 초안에 저장됨 — 검토 후 발행하세요",
            link or "",
        ]
        notify.notify_success(STAGE, "\n".join(tg_lines))

    except Exception as exc:
        log.exception("경제뉴스 작성 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
