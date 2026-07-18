"""작가 에이전트 — 초안 작성/재작성.

write(spec)              → 새 초안(글 JSON)
write(spec, guide=...)   → '이전 초안 + 수정 가이드'를 반영한 재작성

spec(dict) 키:
  keyword, ctype, tone, voice_sample, notes, product(dict), photo_names(list)
"""
from scripts.common.logger import get_logger

from . import _common

log = get_logger("writer")

_TONE = {
    "my": "제공된 '말투 샘플'의 어조·문장 길이·말버릇을 최대한 흉내 내세요.",
    "seo": "친근하지만 정보 전달이 명확한 검색 최적화 어조(존댓말)로 쓰세요.",
}


def _type_guidance(ctype):
    """content_types.md 에서 '## <ctype>' 섹션만 뽑아 반환. 없으면 전체 앞부분."""
    doc = _common.load_prompt("content_types.md")
    marker = f"## {ctype}"
    if marker in doc:
        after = doc.split(marker, 1)[1]
        # 다음 '## ' 전까지가 해당 유형 블록
        block = after.split("\n## ", 1)[0]
        return block.strip()
    return f"(유형 '{ctype}'에 대한 별도 지침 없음 — 일반 리뷰 원칙을 따르세요.)"


def _build_system(spec):
    tmpl = _common.load_prompt("writer_system.md")
    tone_instr = _TONE.get(spec.get("tone"), _TONE["seo"])
    system = tmpl.replace("{tone_instruction}", tone_instr).replace(
        "{type_guidance}", _type_guidance(spec.get("ctype", "실사용후기"))
    )

    examples = _common.load_examples()
    if examples:
        system += f"\n\n[문체·품질 참고 예시 — 이 수준·톤을 지향하세요]\n{examples}\n"

    if spec.get("tone") == "my" and spec.get("voice_sample"):
        system += f"\n\n[말투 샘플 — 이 스타일을 따라 쓰세요]\n{spec['voice_sample'][:3000]}\n"
    return system


def _build_user(spec, prev_draft, guide):
    import json

    product = spec.get("product") or {}
    parts = [f"키워드: {spec.get('keyword','')}", f"컨텐츠 유형: {spec.get('ctype','실사용후기')}"]
    if product.get("name"):
        parts.append(f"소개 상품: {product['name']}")
    if spec.get("notes"):
        parts.append(f"실사용 메모(반드시 반영):\n{spec['notes']}")
    if spec.get("photo_names"):
        parts.append(
            "첨부 사진 파일명(본문 흐름상 위치를 상상하되 직접 삽입하진 마세요): "
            + ", ".join(spec["photo_names"])
        )
    if guide and prev_draft is not None:
        parts.append(
            "아래는 이전 초안과 검수자의 수정 가이드다. 가이드를 충실히 반영해 "
            "전체 글을 다시 써라(형식·JSON 스키마 동일).\n\n"
            f"[이전 초안]\n{json.dumps(prev_draft, ensure_ascii=False)}\n\n"
            f"[수정 가이드]\n{guide}"
        )
    return "\n\n".join(parts)


def write(spec, prev_draft=None, guide=None):
    """초안(글 dict) 생성 또는 재작성."""
    system = _build_system(spec)
    user_msg = _build_user(spec, prev_draft, guide)
    action = "재작성" if guide else "초안 작성"
    log.info("작가 %s 중 — '%s' (%s)", action, spec.get("keyword", ""), spec.get("ctype", ""))
    return _common.complete_json(system, user_msg, max_tokens=3072)
