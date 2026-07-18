"""에이전트 공용 헬퍼 — 프롬프트/예시 로딩 + Claude JSON 호출.

프롬프트·루브릭·few-shot 예시를 '파일'에서 읽어온다. 사용자는 이 파일들만 고쳐서
에이전트를 계속 학습/고도화한다(모델 재학습 아님).
"""
import json
import os

import anthropic

from scripts.common import config
from scripts.common.logger import get_logger

log = get_logger("agents")

# 모델: 기본은 비용효율. 환경변수 LIVING_MODEL로 교체 가능(예: claude-opus-4-8).
MODEL = os.environ.get("LIVING_MODEL", "claude-sonnet-4-6")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
STYLE_DIR = os.path.join(REPO_ROOT, "style")
EXAMPLES_DIR = os.path.join(STYLE_DIR, "examples")

_MAX_EXAMPLES_CHARS = 4000  # few-shot 총 길이 상한(토큰·비용 보호)


def load_prompt(name):
    """scripts/agents/prompts/<name> 파일 내용을 반환. 없으면 빈 문자열."""
    path = os.path.join(PROMPTS_DIR, name)
    return _read(path)


def load_style(name):
    """style/<name> 파일 내용을 반환. 없으면 빈 문자열."""
    return _read(os.path.join(STYLE_DIR, name))


def load_examples():
    """style/examples/*.md 를 이어붙여 few-shot 블록으로 반환(길이 상한 적용)."""
    if not os.path.isdir(EXAMPLES_DIR):
        return ""
    chunks = []
    total = 0
    for fn in sorted(os.listdir(EXAMPLES_DIR)):
        if not fn.endswith((".md", ".txt")):
            continue
        text = _read(os.path.join(EXAMPLES_DIR, fn))
        if not text:
            continue
        chunks.append(f"### 예시: {fn}\n{text}")
        total += len(text)
        if total >= _MAX_EXAMPLES_CHARS:
            break
    return "\n\n".join(chunks)


def _read(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return ""


def parse_json(raw):
    """```json ... ``` 로 감싸였을 수 있는 응답에서 JSON을 파싱."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def complete_json(system, user_msg, max_tokens=3072, model=None):
    """Claude 호출 후 JSON dict 반환(analyze.py 패턴)."""
    api = anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))
    resp = api.messages.create(
        model=model or MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    return parse_json(raw)
