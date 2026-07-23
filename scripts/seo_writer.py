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
  NOTION_DB_SEO (선택) — 있으면 SEO 검토 결과(점수·바꾼 부분·이유)를 여기에 기록
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
    api = _client()
    src_block = _format_sources(sources)

    # 1단계: Sonnet 초안 (소재 선별 + 작성)
    log.info("1/3 초안 작성 중... (%s)", MODEL_DRAFT)
    draft_raw = _call(
        api, MODEL_DRAFT, _DRAFT_SYSTEM,
        f"[최근 기획 요약들]\n{src_block}\n\n위에서 가장 좋은 소재 하나를 골라 글을 쓰세요.",
        max_tokens=4096,
    )

    # 2단계: Opus SEO 검수 (체크리스트 기반 점수 + 개선점, 재작성 X)
    # max_tokens는 넉넉히 — 한국어 findings가 길어 잘리면 JSON 파싱이 실패한다.
    log.info("2/3 SEO 검수 중... (%s)", MODEL_REVIEW)
    audit_raw = _call(
        api, MODEL_REVIEW, _REVIEW_SYSTEM,
        f"[검수할 초안]\n{draft_raw}",
        max_tokens=3000,
    )
    try:
        audit = _parse_json(audit_raw)
        guide = _format_guide(audit)
    except Exception as exc:
        log.warning("검수 결과 JSON 파싱 실패 — 텍스트 그대로 사용: %s", exc)
        audit = None
        guide = audit_raw

    # 3단계: Sonnet 최종본 (가이드 반영)
    log.info("3/3 최종본 작성 중... (%s)", MODEL_DRAFT)
    final_raw = _call(
        api, MODEL_DRAFT, _REVISE_SYSTEM,
        f"[1차 초안]\n{draft_raw}\n\n[편집장 개선 가이드]\n{guide}\n\n"
        "위를 반영해 최종 글을 완성하세요.",
        max_tokens=4096,
    )
    return _parse_json(final_raw), audit


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

        log.info("워드프레스 초안 저장 중...")
        link = _create_draft(article, date_str)
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
