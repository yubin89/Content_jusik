"""Claude API 공통 호출 헬퍼 — seo_writer/econ_news/weekly_calendar가 공유."""
import json

import anthropic

from . import config

MODEL_DRAFT = "claude-sonnet-4-6"   # 초안/최종 작성용(저렴)
MODEL_REVIEW = "claude-opus-4-8"    # 검수용(짧은 출력이라 저렴)

# 서버사이드 웹검색 도구 — 최신 뉴스·일정 등 실시간 정보가 필요할 때 tools=[WEB_SEARCH_TOOL]
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}


def client():
    return anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))


def call(api, model, system, user, max_tokens, tools=None):
    """Claude 호출 후 최종 텍스트(문자열)를 반환.
    tools(예: 웹검색)를 쓰면 검색 도중 중간 설명 텍스트가 섞일 수 있어
    '마지막' 텍스트 블록만 사용한다 — 시스템 프롬프트에서 최종 답만
    마지막에 출력하도록 지시하는 것과 짝을 이룬다."""
    resp = api.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=tools or [],
    )
    texts = [b.text for b in resp.content if b.type == "text"]
    return texts[-1].strip() if texts else ""


def parse_json(raw):
    """```json ... ``` 코드블록으로 감싸인 경우까지 대응해 dict로 파싱."""
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)
