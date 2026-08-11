"""
scripts/seo_writer.py — 노션 D → SEO 블로그 글 작성 → 워드프레스 초안 저장 (Step 6)

최근 며칠간의 노션 D(콘텐츠 기획 요약)들을 모아, 그중 콘텐츠로 가장 좋은 소재
'하나'를 골라 구글 SEO에 최적화된 한국어 블로그 글을 작성하고, 워드프레스에
'초안(draft)'으로 저장한다. 초안이므로 사람이 검토·수정한 뒤 직접 발행한다.

주 2회(월·수)만 실행한다 — 매일 대량 발행은 구글에 '양산형'으로 찍혀
오히려 불리하므로, 며칠 치 중 최고를 골라 품질에 집중한다.
(화~금은 econ_news.py가, 일요일은 weekly_calendar.py가 다른 콘텐츠를 발행한다.)

글 생성은 3단계 비평-반영(reflection) 파이프라인:
  1) Sonnet 초안 → 2) Opus 검수(개선 가이드만) → 3) Sonnet 최종본

필요 환경변수(GitHub Secrets):
  NOTION_TOKEN, NOTION_DB_D, ANTHROPIC_API_KEY
  WP_URL, WP_USER, WP_APP_PASSWORD
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  NOTION_DB_SEO (선택) — 있으면 SEO 검토 결과(점수·바꾼 부분·이유)를 여기에 기록
  UNSPLASH_ACCESS_KEY (선택) — 있으면 본문에 실사 사진도 삽입
"""
import sys
import urllib.parse
from datetime import datetime

import pytz
import requests

from scripts.common import ai, config, notify, cards, stock_photo, wp_publish
from scripts.common.logger import get_logger
import scripts.common.notion_client as nc

log = get_logger("seo_writer")
STAGE = "SEO 글 작성"

KST = pytz.timezone("Asia/Seoul")
RECENT_COUNT = 4            # 최근 노션 D 몇 개를 놓고 소재를 고를지
MAX_SOURCE_LEN = 4000       # 기획 요약 1개당 최대 길이

# ---- 이미지 (best-effort: 실패해도 글은 저장) ----
IMAGE_PROVIDER = "pollinations"   # 대표 이미지 생성. 무료·키 불필요. 나중에 openai/google로 교체
IMAGE_GEN_TIMEOUT = 120           # 이미지 생성은 오래 걸릴 수 있음
FEATURED_SIZE = (1200, 630)       # 대표 이미지(og:image 표준 비율)


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
당신은 개인투자자 대상 주식 블로그 작가입니다.
아래는 최근 며칠간의 콘텐츠 기획 요약들입니다. 아래 [소재 선택 기준]에 따라
가장 좋은 종목/주제 '하나'만 골라, 그 주제에 집중한 글 한 편을 작성하세요.

[소재 선택 기준] (우선순위 순)
1. 신호 강도 — 소셜·공시·수급 중 2곳 이상에 겹쳐 등장한 종목을 최우선
2. 시의성 — 지금 다루기 좋은 따끈한 이슈인가
3. 스토리 — 왜 움직이는지 설명할 근거(수주·실적·테마 등)가 명확한가
4. 검색 수요 — 사람들이 검색해볼 법한 종목/테마인가

[독자] 주식 계좌를 갖고 기본 매매는 해본 입문~초중급 개인투자자.
       '수급·공시·테마' 같은 기본 용어는 이미 아는 사람.
       - 쉽고 명확하게 쓰되 유치한 비유나 과잉 설명은 금지
       - 주식 기초(주식이 뭔지 등)는 설명하지 말 것
       - 정말 생소한 용어(특이한 공시 유형 등)만 한 줄로 짧게 풀이
[분량] 본문 1500~2000자(표·FAQ 제외)

[필수 구성 요소] (SEO·AI인용에 강함)
- 본문 중간에 비교/요약 표 1개(HTML). 첫 열은 항목명, 3~4행:
  <table class="ms-tbl"><thead><tr><th>항목</th><th>내용</th><th>확인 포인트</th></tr></thead><tbody><tr><td>...</td><td>...</td><td>...</td></tr></tbody></table>
  글 주제에 맞는 핵심 정보(확인 포인트/종목 비교/일정 등)로 채운다.
- 글 끝(투자 유의 문구 바로 앞)에 FAQ 3~5개(HTML):
  <div class="ms-faq"><p class="ms-q">Q. 질문</p><p class="ms-a">A. 답변</p> ...반복... </div>
  질문은 독자가 실제로 검색할 법한 궁금증으로.
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
  "primary_name": "이 글이 집중하는 대표 종목명 (예: 메리츠금융지주)",
  "primary_ticker": "그 종목의 6자리 코드 (모르면 빈 문자열)",
  "content_html": "<h2>...</h2><p>...</p>"
}
"""

_REVIEW_SYSTEM = """\
당신은 한국 주식/투자 블로그의 SEO 편집장입니다.
아래 블로그 초안을 구글 SEO·AI검색 관점에서 항목별로 엄격히 검수하세요.
글을 직접 다시 쓰지 말고, 항목별 문제점과 구체적 개선 방법만 지적하세요.

[검수 체크리스트]
1. 제목: 30~60자, 핵심 키워드 앞쪽, 클릭 유도(낚시 X)
2. 메타설명: 70~155자, 키워드 포함, 내용 요약력
3. 소제목(H2) 구조: 논리적 흐름, 가능하면 질문형(검색어와 일치)
4. 키워드: 도입부 포함, 자연스러운 반복(억지 X)
5. 자문자답형 문단: 핵심 질문의 답이 130~170단어의 자체 완결 문단으로 정리됐는지(AI 검색 인용 대응)
6. E-E-A-T: 근거·출처, 시의성, 신뢰성, 투자 유의 문구
7. 가독성: 문단 길이, 입문~초중급 눈높이
8. AI 티: 기계적·군더더기 표현 없이 자연스러운지

[응답 형식] 반드시 아래 JSON만 출력. 다른 텍스트 없이:
{
  "score": SEO 완성도 0~100 정수,
  "findings": [
    {"area": "항목명(예: 제목)", "issue": "무엇이 문제인지", "fix": "어떻게 고칠지"}
  ],
  "overall": "총평 한두 문장"
}
findings는 가장 중요한 3~6개만. issue·fix는 각각 한 문장으로 간결하게.
개선점이 없으면 findings를 빈 배열([])로 두세요.
"""

_REVISE_SYSTEM = """\
당신은 개인투자자 대상 주식 블로그 작가입니다.
아래 [1차 초안]과 [편집장 개선 가이드]를 참고해 최종 블로그 글을 완성하세요.
가이드의 지적을 최대한 반영하되, 원래의 독자·분량·구조·SEO 규칙은 그대로 유지하세요.

[독자] 입문~초중급 개인투자자 — 쉽고 명확하되 유치하지 않게, 기초 용어는 설명 생략
[분량] 본문 1500~2000자(표·FAQ 제외)
[구조] <h2> 소제목 3~5개
[필수 구성] 본문 중간 비교/요약 표 1개(<table class="ms-tbl">…, 첫 열=항목명, 3~4행) +
       글 끝 FAQ 3~5개(<div class="ms-faq"><p class="ms-q">Q. …</p><p class="ms-a">A. …</p>…</div>)
[주의] 과장·단정 금지, 마지막에 투자 유의 문구 문단 유지

[응답 형식] 반드시 아래 JSON만 출력. 다른 텍스트 없이:
{
  "title": "제목",
  "meta_description": "메타설명",
  "tags": ["태그1", "태그2"],
  "primary_name": "이 글이 집중하는 대표 종목명",
  "primary_ticker": "그 종목의 6자리 코드 (모르면 빈 문자열)",
  "content_html": "<h2>...</h2><p>...</p>"
}
"""


def _format_guide(audit):
    """검수 결과(dict)를 Sonnet에게 줄 개선 가이드 텍스트로 변환."""
    lines = []
    for f in audit.get("findings", []):
        lines.append(f"- [{f.get('area', '')}] {f.get('issue', '')} → {f.get('fix', '')}")
    if not lines:
        return "특이 개선점 없음(양호). 그대로 다듬어 완성하세요."
    return "\n".join(lines)


def _generate_article(sources):
    """3단계(초안→검수→최종) 파이프라인. (최종 기사 dict, 검수 결과 dict|None) 반환."""
    api = ai.client()
    src_block = _format_sources(sources)

    # 1단계: Sonnet 초안 (소재 선별 + 작성)
    log.info("1/3 초안 작성 중... (%s)", ai.MODEL_DRAFT)
    draft_raw = ai.call(
        api, ai.MODEL_DRAFT, _DRAFT_SYSTEM,
        f"[최근 기획 요약들]\n{src_block}\n\n위에서 가장 좋은 소재 하나를 골라 글을 쓰세요.",
        max_tokens=8000,   # 본문+표+FAQ가 JSON에 담겨 길어짐 → 잘림 방지
    )

    # 2단계: Opus SEO 검수 (체크리스트 기반 점수 + 개선점, 재작성 X)
    # max_tokens는 넉넉히 — 한국어 findings가 길어 잘리면 JSON 파싱이 실패한다.
    log.info("2/3 SEO 검수 중... (%s)", ai.MODEL_REVIEW)
    audit_raw = ai.call(
        api, ai.MODEL_REVIEW, _REVIEW_SYSTEM,
        f"[검수할 초안]\n{draft_raw}",
        max_tokens=3000,
    )
    try:
        audit = ai.parse_json(audit_raw)
        guide = _format_guide(audit)
    except Exception as exc:
        log.warning("검수 결과 JSON 파싱 실패 — 텍스트 그대로 사용: %s", exc)
        audit = None
        guide = audit_raw

    # 3단계: Sonnet 최종본 (가이드 반영)
    log.info("3/3 최종본 작성 중... (%s)", ai.MODEL_DRAFT)
    final_raw = ai.call(
        api, ai.MODEL_DRAFT, _REVISE_SYSTEM,
        f"[1차 초안]\n{draft_raw}\n\n[편집장 개선 가이드]\n{guide}\n\n"
        "위를 반영해 최종 글을 완성하세요.",
        max_tokens=8000,   # 본문+표+FAQ 포함 → 잘림 방지
    )
    return ai.parse_json(final_raw), audit


# ---- 대표 이미지(AI 컨셉) 생성 ----

_IMAGE_META_SYSTEM = """\
너는 한국 주식/투자 블로그 이미지용 정보를 만든다.

[규칙]
- featured_prompt: '영어' 대표 이미지 생성 프롬프트. 추상·개념적 금융 이미지만
  (상승 곡선 실루엣, 도시 스카이라인, 밝고 현대적인 금융 무드 등).
  글자·숫자·차트 수치·실제 기업 로고·상표·실존 인물은 절대 금지.
  깔끔한 현대적 에디토리얼/컨셉 아트 스타일.
- featured_alt: 대표 이미지의 '한국어' 대체텍스트(ALT). 핵심 키워드 포함 한 문장.
- photo_query: 본문에 넣을 '실사 스톡 사진'을 Unsplash에서 검색할 '영어' 키워드
  1~3단어. 글 주제와 관련된 보편적 장면(예: "semiconductor factory",
  "seoul financial district", "stock trading floor"). 특정 회사명·로고 금지.
- photo_alt: 그 실사 사진의 '한국어' 대체텍스트(ALT). 한 문장.

[출력] 아래 JSON만. 다른 텍스트 없이:
{"featured_prompt": "...", "featured_alt": "...", "photo_query": "...", "photo_alt": "..."}
"""


def _generate_image_meta(article):
    """대표 이미지 프롬프트/ALT + 실사 사진 검색어/ALT 생성."""
    api = ai.client()
    user = f"제목: {article.get('title', '')}\n요약: {article.get('meta_description', '')}"
    raw = ai.call(api, ai.MODEL_DRAFT, _IMAGE_META_SYSTEM, user, max_tokens=500)
    return ai.parse_json(raw)


def _pollinations_image(prompt, width, height):
    """Pollinations(무료·키 불필요)로 이미지 생성. (bytes, content_type) 반환."""
    enc = urllib.parse.quote(prompt or "abstract finance concept", safe="")
    url = (
        f"https://image.pollinations.ai/prompt/{enc}"
        f"?width={width}&height={height}&nologo=true"
    )
    resp = requests.get(url, timeout=IMAGE_GEN_TIMEOUT)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "image/jpeg")


def _generate_image(prompt, size):
    """IMAGE_PROVIDER에 따라 이미지 생성. (bytes, content_type) 반환."""
    w, h = size
    if IMAGE_PROVIDER == "pollinations":
        return _pollinations_image(prompt, w, h)
    # 나중에 유료 고품질 전환: openai(gpt-image-1) / google(imagen) 분기 추가
    raise ValueError(f"지원하지 않는 IMAGE_PROVIDER: {IMAGE_PROVIDER}")


def _attach_visuals(article, date_str):
    """본문 데이터 카드 + 실사 사진 + 대표(AI 컨셉) 이미지를 붙인다.
    featured_media id 반환(없으면 None). 각 이미지는 best-effort — 실패해도 글 저장은 계속."""
    featured_id = None
    meta = {}
    try:
        meta = _generate_image_meta(article)
    except Exception as exc:
        log.warning("이미지 메타 생성 실패(대표·실사 생략): %s", exc)

    # 1) 본문①: 글의 대표 종목 수급 데이터 카드 (없으면 생략)
    try:
        png, card_alt = cards.supply_demand_card(
            ticker=article.get("primary_ticker"), name=article.get("primary_name"),
        )
        if png:
            _, src = wp_publish.upload_media(png, "image/png", f"card-{date_str}", alt=card_alt)
            if src:
                article["content_html"] = wp_publish.insert_inline_image(
                    article.get("content_html", ""), src, card_alt,
                    caption="자료: 한국거래소(KRX) 기관 순매수 데이터", before_h2=0,
                )
                log.info("본문 데이터 카드 삽입 완료")
    except Exception as exc:
        log.warning("데이터 카드 실패(생략): %s", exc)

    # 2) 본문②: 콘텐츠 관련 실사 사진 (Unsplash, 출처 표기)
    try:
        photo = stock_photo.search_photo(meta.get("photo_query"))
        if photo:
            p_alt = meta.get("photo_alt") or article.get("title", "")
            _, src = wp_publish.upload_media(
                photo["bytes"], photo["content_type"], f"photo-{date_str}", alt=p_alt)
            if src:
                credit = f'사진: {photo["credit_name"]} / Unsplash'
                article["content_html"] = wp_publish.insert_inline_image(
                    article.get("content_html", ""), src, p_alt,
                    caption=credit, before_h2=2,
                )
                log.info("본문 실사 사진 삽입 완료")
    except Exception as exc:
        log.warning("실사 사진 실패(생략): %s", exc)

    # 3) 대표 이미지: AI 컨셉 (썸네일/CTR용)
    try:
        img, ctype = _generate_image(meta.get("featured_prompt", ""), FEATURED_SIZE)
        featured_id, _ = wp_publish.upload_media(
            img, ctype, f"featured-{date_str}", alt=meta.get("featured_alt"))
        log.info("대표 이미지 업로드 완료 (media %s)", featured_id)
    except Exception as exc:
        log.warning("대표 이미지 실패(생략): %s", exc)

    return featured_id


def _log_seo_review_to_notion(audit, article, date_str):
    """SEO 검수 결과(점수·바꾼 부분·이유)를 노션에 기록. NOTION_DB_SEO 미설정 시 생략."""
    db_id = config.get_optional("NOTION_DB_SEO")
    if not db_id:
        log.info("NOTION_DB_SEO 미설정 — SEO 검토 기록 생략")
        return
    if not audit:
        log.info("검수 결과 없음 — SEO 검토 기록 생략")
        return

    title = f"🔍 SEO 검토 {date_str} — {article.get('title', '')}"
    blocks = [
        nc.paragraph(f"SEO 점수: {audit.get('score', '-')}/100"),
        nc.paragraph(f"총평: {audit.get('overall', '')}"),
    ]
    findings = audit.get("findings", [])
    if findings:
        blocks.append(nc.heading("개선 항목 (바꾼 부분 · 이유)", 2))
        for f in findings:
            blocks.append(nc.bullet(f"[{f.get('area', '')}] {f.get('issue', '')}"))
            blocks.append(nc.paragraph(f"→ 개선: {f.get('fix', '')}"))
    else:
        blocks.append(nc.paragraph("지적 사항 없음 (양호)"))

    nc.create_page_in_database(db_id, title, blocks)
    log.info("SEO 검토 기록 저장 완료(노션)")


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

        article, audit = _generate_article(sources)

        # 본문 데이터 카드 + 실사 사진 + 대표 이미지 (best-effort: 실패해도 글은 저장)
        featured_id = _attach_visuals(article, date_str)

        log.info("워드프레스 초안 저장 중...")
        link = wp_publish.create_draft(
            title=article.get("title"),
            content_html=article.get("content_html", ""),
            excerpt=article.get("meta_description", ""),
            tags=article.get("tags", []),
            featured_media=featured_id,
            fallback_title=f"주목 종목 브리핑 {date_str}",
        )
        log.info("초안 저장 완료: %s", link)

        # SEO 검토 결과(점수·바꾼 부분·이유)를 노션에 기록 (실패해도 전체는 성공 처리)
        try:
            _log_seo_review_to_notion(audit, article, date_str)
        except Exception as exc:
            log.warning("SEO 검토 노션 기록 실패(건너뜀): %s", exc)

        tg_lines = [
            f"{date_str} SEO 글 초안 완성",
            f"📝 {article.get('title', '')}",
        ]
        if audit and audit.get("score") is not None:
            tg_lines.append(f"🔍 SEO 점수: {audit.get('score')}/100")
        if featured_id:
            tg_lines.append("🖼️ 대표 이미지 포함")
        tg_lines += [
            "✅ 워드프레스 초안에 저장됨 — 검토 후 발행하세요",
            link or "",
        ]
        notify.notify_success(STAGE, "\n".join(tg_lines))

    except Exception as exc:
        log.exception("SEO 글 작성 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
