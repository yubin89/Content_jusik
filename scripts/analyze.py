"""
scripts/analyze.py — Claude 콘텐츠 기획 분석 (Step 5)

노션 A(소셜)·B(공시)·C(수급)의 최신 페이지를 읽어
Claude에게 콘텐츠 기획서 요약을 생성시키고 노션 D에 저장한다.

필요 환경변수(GitHub Secrets):
  NOTION_TOKEN, NOTION_DB_A, NOTION_DB_B, NOTION_DB_C, NOTION_DB_D
  ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import sys
import traceback
from datetime import datetime

import anthropic
import pytz

from scripts.common import config, notify
from scripts.common.logger import get_logger
import scripts.common.notion_client as nc

log = get_logger("analyze")
STAGE = "콘텐츠 기획 분석"
MODEL = "claude-sonnet-4-6"   # 비용효율 우선. 품질이 더 필요하면 claude-opus-4-8로 교체.
KST = pytz.timezone("Asia/Seoul")
MAX_TEXT_LEN = 6000  # DB당 Claude 입력 최대 길이


def _read_db(db_env_key, label):
    """DB 최신 페이지 텍스트 반환. 실패 시 None(graceful degradation)."""
    try:
        db_id = config.get(db_env_key)
        page = nc.query_latest_page(db_id)
        if not page:
            log.info("%s: 최신 페이지 없음", label)
            return None
        text = nc.read_page_text(page["id"])
        log.info("%s: %d자 읽음", label, len(text))
        return text[:MAX_TEXT_LEN] if len(text) > MAX_TEXT_LEN else text
    except Exception as exc:
        log.warning("%s 읽기 실패 (graceful 처리): %s", label, exc)
        return None


_SYSTEM_PROMPT = """\
당신은 주식 콘텐츠 기획 전문가입니다.
오늘의 소셜 화제(A)·공시(B)·수급(C) 수집 결과를 교차검증해
콘텐츠 작성용 기획서를 한국어로 요약해주세요.

[우선순위]
1. 소셜·공시·수급 세 곳에 동시에 등장하는 종목 (교집합)
2. 두 곳 이상에 등장하는 종목
3. 단독으로 특히 눈에 띄는 종목

[응답 형식] 반드시 아래 JSON만 출력하세요. 한국어로, 다른 텍스트 없이:
{
  "headline": "오늘의 한 줄 핵심 요약 (50자 이내)",
  "top_picks": [
    {
      "ticker": "티커 (한국주식 6자리 또는 미국 영문)",
      "name": "종목명",
      "why": "왜 주목하는지 근거 2~3줄",
      "sources": ["소셜", "공시", "수급"]
    }
  ],
  "content_angles": [
    "콘텐츠 제목/앵글 제안 (블로그·SNS 포스팅 주제)"
  ],
  "market_note": "오늘 시장 전반 특이사항 (없으면 빈 문자열)"
}
"""


def _call_claude(a_text, b_text, c_text):
    """Claude에게 기획서 생성 요청 후 파싱된 dict 반환."""
    def section(label, text):
        return f"=== {label} ===\n{text or '(데이터 없음)'}\n"

    user_msg = "\n".join([
        section("A. 소셜 화제 종목 (X/FinTwit)", a_text),
        section("B. 공시 (DART 수주·계약체결 등)", b_text),
        section("C. 수급 (기관·외국인 순매수)", c_text),
    ])

    api_client = anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))
    response = api_client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    # ```json ... ``` 블록으로 감싸인 경우 대응
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _build_blocks(plan, date_str):
    """기획서 dict → Notion 본문 블록 리스트."""
    blocks = [
        nc.paragraph(f"📅 {date_str} | 모델: {MODEL}"),
        nc.heading("💡 오늘의 핵심", 2),
        nc.paragraph(plan.get("headline", "")),
    ]

    top_picks = plan.get("top_picks", [])
    if top_picks:
        blocks.append(nc.heading("🎯 주목 종목", 2))
        for p in top_picks:
            src_tag = " · ".join(p.get("sources", []))
            title = f"{p.get('name', '')}({p.get('ticker', '')})  [{src_tag}]"
            blocks.append(nc.bullet(title))
            blocks.append(nc.paragraph(p.get("why", "")))

    angles = plan.get("content_angles", [])
    if angles:
        blocks.append(nc.heading("✍️ 콘텐츠 앵글 제안", 2))
        for angle in angles:
            blocks.append(nc.bullet(angle))

    note = plan.get("market_note", "")
    if note:
        blocks.append(nc.heading("📊 시장 특이사항", 2))
        blocks.append(nc.paragraph(note))

    return blocks


def main():
    config.require([
        "NOTION_TOKEN", "NOTION_DB_A", "NOTION_DB_B",
        "NOTION_DB_C", "NOTION_DB_D", "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ])

    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    log.info("=== %s 시작 — %s ===", STAGE, date_str)

    try:
        a_text = _read_db("NOTION_DB_A", "소셜(A)")
        b_text = _read_db("NOTION_DB_B", "공시(B)")
        c_text = _read_db("NOTION_DB_C", "수급(C)")

        if not any([a_text, b_text, c_text]):
            msg = f"{date_str} — A·B·C 모두 데이터 없음. 기획서 생략."
            log.warning(msg)
            notify.notify_success(STAGE, msg)
            return

        log.info("Claude 기획서 생성 중... (모델: %s)", MODEL)
        plan = _call_claude(a_text, b_text, c_text)

        db_id = config.get("NOTION_DB_D")
        title = f"📝 콘텐츠 기획 요약 {date_str}"
        blocks = _build_blocks(plan, date_str)
        nc.create_page_in_database(db_id, title, blocks)
        log.info("Notion D 저장 완료: %s", title)

        top = plan.get("top_picks", [])[:2]
        top_str = "  ".join(
            f"{p['name']}({p['ticker']})" for p in top
        ) if top else "—"
        tg_msg = "\n".join([
            f"{date_str} 기획서 완성",
            f"💡 {plan.get('headline', '')}",
            f"🎯 주목: {top_str}",
            "📝 전체 내용은 노션 D 참고",
        ])
        notify.notify_success(STAGE, tg_msg)

    except Exception as exc:
        log.exception("분석 실패")
        notify.notify_error(STAGE, f"{exc}\n\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
