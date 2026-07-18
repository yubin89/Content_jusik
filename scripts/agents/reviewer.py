"""검수 에이전트 — SEO·컨텐츠 맥락 검수.

review(article, spec) → 구조화 비평 dict:
  {"score":0~100, "pass":bool, "seo":[...], "content":[...],
   "structure":[...], "rewrite_guide":"..."}

체크리스트는 scripts/agents/prompts/reviewer_rubric.md + style/rubric.md 에서 읽어온다.
"""
import json

from scripts.common.logger import get_logger

from . import _common

log = get_logger("reviewer")


def _build_system():
    tmpl = _common.load_prompt("reviewer_rubric.md")
    rubric = _common.load_style("rubric.md") or "(별도 루브릭 없음 — 일반 SEO/품질 기준 적용)"
    return tmpl.replace("{rubric}", rubric)


def review(article, spec, pass_score=80):
    """초안을 검수하고 비평 dict 반환. pass는 score 기준으로 보정."""
    user_msg = (
        f"키워드: {spec.get('keyword','')}\n"
        f"컨텐츠 유형: {spec.get('ctype','실사용후기')}\n"
        f"통과 기준 점수: {pass_score}점 이상\n\n"
        f"[검수할 초안 JSON]\n{json.dumps(article, ensure_ascii=False)}"
    )
    log.info("검수 중 — '%s'", spec.get("keyword", ""))
    critique = _common.complete_json(_build_system(), user_msg, max_tokens=1500)

    # score 기준으로 pass를 안전하게 보정(모델이 pass만 후하게 주는 것 방지)
    score = critique.get("score")
    if isinstance(score, (int, float)):
        critique["pass"] = bool(score >= pass_score) and critique.get("pass", True)
    return critique
