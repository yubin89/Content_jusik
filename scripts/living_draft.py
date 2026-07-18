"""scripts/living_draft.py — 리빙(생활) 리뷰 SEO 초안 생성기.

트렌드/지정 키워드 + (선택)내 사진 + (선택)쿠팡 상품 → Claude가 SEO 리뷰 글을 쓰고,
글 하단에 쿠팡 파트너스 링크 + 법정 고지문구를 붙인 뒤 두 채널로 내보낸다.

  ① Jekyll 포스트(site/_posts/*.md)  → 자체 사이트 자동 발행
  ② Notion '발행대기' DB              → 네이버 블로그 수동 복붙용(+사진 배치 가이드)

사용 예:
  python -m scripts.living_draft --keyword "에어프라이어 추천" --tone seo --publish site
  python -m scripts.living_draft --keyword "무선 청소기 후기" --product "https://www.coupang.com/vp/products/123" \
      --photos ~/pics/cleaner --tone my --voice-sample my_voice.txt --publish both

필요 환경변수(채널에 따라):
  ANTHROPIC_API_KEY                     (필수)
  COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY(선택 — 없으면 링크 없이 초안만)
  NOTION_TOKEN, NOTION_DB_LIVING        (--publish notion|both 일 때)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (선택 — 알림)
"""
import argparse
import json
import os
import re
import shutil
import sys
import traceback
from datetime import datetime

import anthropic
import pytz

from scripts.common import affiliate, config, coupang, notify
from scripts.common.logger import get_logger
import scripts.common.notion_client as nc

log = get_logger("living")
STAGE = "리빙 초안 생성"
MODEL = "claude-sonnet-4-6"   # 비용효율 우선. 품질이 더 필요하면 claude-opus-4-8로 교체.
KST = pytz.timezone("Asia/Seoul")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_DIR = os.path.join(REPO_ROOT, "site", "_posts")
PHOTOS_ROOT = os.path.join(REPO_ROOT, "site", "assets", "photos")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


# ---------------------------------------------------------------- Claude

_SYSTEM_TMPL = """\
당신은 한국어 리빙(생활·리뷰) 블로그 SEO 콘텐츠 작가입니다.
주어진 키워드로 검색 유입이 잘 되는 실사용 리뷰 글을 씁니다.

[작성 원칙]
- 검색 의도를 만족시키는 정보성 + 진솔한 후기. 과장·허위 금지.
- 제목과 본문에 핵심 키워드를 자연스럽게 반복(키워드 스터핑 아님).
- 소제목(H2)으로 구조화. 각 섹션은 2~4문단.
- 실사용 후기 섹션은 반드시 1인칭 경험담으로. {tone_instruction}
- 사용자가 제공한 '실사용 메모'가 있으면 그 내용을 후기에 자연스럽게 녹여라.

[응답 형식] 아래 JSON만 출력하세요. 다른 텍스트·설명 없이:
{{
  "title": "SEO 제목 (32자 내외, 키워드 포함)",
  "slug": "english-hyphenated-slug (영문 소문자, 하이픈)",
  "description": "메타 설명 (검색결과에 뜨는 요약, 120자 이내)",
  "tags": ["태그1", "태그2", "태그3"],
  "intro": "도입 문단 (독자 공감 유발, 2~3문장)",
  "sections": [
    {{"heading": "소제목", "body": "본문 (여러 문단, \\n\\n 으로 구분)"}}
  ],
  "review": "실사용 후기 문단 (1인칭, 지정 말투, 메모 반영)",
  "faq": [{{"q": "자주 묻는 질문", "a": "답변"}}]
}}
"""

_TONE = {
    "my": "제공된 '말투 샘플'의 어조·문장 길이·말버릇을 최대한 흉내 내세요.",
    "seo": "친근하지만 정보 전달이 명확한 검색 최적화 어조(존댓말)로 쓰세요.",
}


def _build_system(tone, voice_sample):
    base = _SYSTEM_TMPL.format(tone_instruction=_TONE.get(tone, _TONE["seo"]))
    if tone == "my" and voice_sample:
        base += f"\n\n[말투 샘플 — 이 스타일을 따라 쓰세요]\n{voice_sample[:3000]}\n"
    return base


def _call_claude(keyword, notes, product, photo_names, tone, voice_sample):
    """Claude에게 리뷰 글 생성을 요청하고 파싱된 dict 반환."""
    parts = [f"키워드: {keyword}"]
    if product.get("name"):
        parts.append(f"소개 상품: {product['name']}")
    if notes:
        parts.append(f"실사용 메모(반드시 후기에 반영):\n{notes}")
    if photo_names:
        parts.append(
            "첨부 사진 파일명(본문 흐름에 맞게 위치를 상상하되, 직접 삽입하진 마세요): "
            + ", ".join(photo_names)
        )
    user_msg = "\n\n".join(parts)

    api = anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))
    resp = api.messages.create(
        model=MODEL,
        max_tokens=3072,
        system=_build_system(tone, voice_sample),
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


# ---------------------------------------------------------------- 사진

def _slugify(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or fallback


def _collect_photos(src_dir, slug):
    """사진 폴더의 이미지들을 site/assets/photos/<slug>/ 로 복사하고 웹 경로 리스트 반환."""
    if not src_dir or not os.path.isdir(src_dir):
        return []
    files = sorted(
        f for f in os.listdir(src_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
    )
    if not files:
        return []
    dest_dir = os.path.join(PHOTOS_ROOT, slug)
    os.makedirs(dest_dir, exist_ok=True)
    web_paths = []
    for f in files:
        shutil.copy2(os.path.join(src_dir, f), os.path.join(dest_dir, f))
        web_paths.append(f"/assets/photos/{slug}/{f}")
    log.info("사진 %d장 복사 → %s", len(web_paths), dest_dir)
    return web_paths


# ---------------------------------------------------------------- 빌더

def _yaml_escape(s):
    return '"' + str(s).replace('"', '\\"') + '"'


def _build_markdown(article, product, photos, date_str):
    """article dict → Jekyll 포스트 markdown 문자열."""
    hero = photos[0] if photos else product.get("image")
    fm = [
        "---",
        "layout: post",
        f"title: {_yaml_escape(article.get('title', ''))}",
        f"date: {date_str} 09:00:00 +0900",
        f"description: {_yaml_escape(article.get('description', ''))}",
        "tags: [" + ", ".join(_yaml_escape(t) for t in article.get("tags", [])) + "]",
    ]
    if hero:
        fm.append(f"image: {_yaml_escape(hero)}")
    fm.append("---")

    body = ["", article.get("intro", ""), ""]
    if hero:
        body.append(f"![{article.get('title','')}]({hero})\n")

    extra_photos = photos[1:] if photos else []
    for i, sec in enumerate(article.get("sections", [])):
        body.append(f"## {sec.get('heading', '')}\n")
        body.append(sec.get("body", "") + "\n")
        if i < len(extra_photos):  # 남은 사진을 섹션마다 하나씩 배치
            body.append(f"![{sec.get('heading','')}]({extra_photos[i]})\n")

    review = article.get("review")
    if review:
        body.append("## 실사용 후기\n")
        body.append(review + "\n")

    faq = article.get("faq", [])
    if faq:
        body.append("## 자주 묻는 질문\n")
        for item in faq:
            body.append(f"**Q. {item.get('q','')}**\n")
            body.append(f"{item.get('a','')}\n")

    body.append(affiliate.markdown_block(product))
    return "\n".join(fm) + "\n" + "\n".join(body) + "\n"


def _build_notion_blocks(article, product, photos, date_str):
    """article dict → Notion 블록(네이버 수동 발행용 + 사진 배치 가이드)."""
    blocks = [
        nc.paragraph(f"📅 {date_str} · 말투/키워드 기반 초안 · 네이버에 복붙 후 사진 삽입"),
        nc.heading(article.get("title", ""), 1),
        nc.paragraph(article.get("intro", "")),
    ]
    if photos:
        blocks.append(nc.paragraph(f"🖼 [사진1 삽입: {os.path.basename(photos[0])}]"))

    extra = photos[1:] if photos else []
    for i, sec in enumerate(article.get("sections", [])):
        blocks.append(nc.heading(sec.get("heading", ""), 2))
        for para in sec.get("body", "").split("\n\n"):
            if para.strip():
                blocks.append(nc.paragraph(para.strip()))
        if i < len(extra):
            blocks.append(nc.paragraph(f"🖼 [사진{i+2} 삽입: {os.path.basename(extra[i])}]"))

    if article.get("review"):
        blocks.append(nc.heading("실사용 후기", 2))
        blocks.append(nc.paragraph(article["review"]))

    if article.get("faq"):
        blocks.append(nc.heading("자주 묻는 질문", 2))
        for item in article["faq"]:
            blocks.append(nc.bullet(f"Q. {item.get('q','')}"))
            blocks.append(nc.paragraph(item.get("a", "")))

    blocks.extend(affiliate.notion_blocks(product))
    tags = article.get("tags", [])
    if tags:
        blocks.append(nc.paragraph("🏷 추천 태그: " + " ".join(f"#{t}" for t in tags)))
    return blocks


# ---------------------------------------------------------------- 출력

def _write_post(article, markdown, date_str):
    slug = _slugify(article.get("slug"), fallback=date_str.replace("-", ""))
    os.makedirs(POSTS_DIR, exist_ok=True)
    path = os.path.join(POSTS_DIR, f"{date_str}-{slug}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    log.info("Jekyll 포스트 저장: %s", path)
    return path


def _write_notion(article, blocks, date_str):
    db_id = config.get("NOTION_DB_LIVING")
    title = f"📝 {article.get('title', '리빙 초안')} ({date_str})"
    nc.create_page_in_database(db_id, title, blocks)
    log.info("Notion 발행대기 저장: %s", title)


# ---------------------------------------------------------------- main

def _read_file(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="리빙 리뷰 SEO 초안 생성기")
    ap.add_argument("--keyword", required=True, help="핵심 키워드/주제")
    ap.add_argument("--product", default="", help="쿠팡 상품 URL 또는 상품 검색어")
    ap.add_argument("--photos", default="", help="사진 폴더 경로(선택)")
    ap.add_argument("--notes", default="", help="실사용 메모(선택). 파일 경로면 파일 내용 사용")
    ap.add_argument("--tone", choices=["my", "seo"], default="seo")
    ap.add_argument("--voice-sample", default="", help="'my' 말투 샘플 텍스트 파일(선택)")
    ap.add_argument("--publish", choices=["site", "notion", "both"], default="site")
    args = ap.parse_args(argv)

    config.require(["ANTHROPIC_API_KEY"])
    if args.publish in ("notion", "both"):
        config.require(["NOTION_TOKEN", "NOTION_DB_LIVING"])

    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    log.info("=== %s 시작 — '%s' (%s) ===", STAGE, args.keyword, date_str)

    try:
        notes = _read_file(args.notes) or args.notes
        voice = _read_file(args.voice_sample)

        product = coupang.resolve_product(args.product)
        if product.get("link"):
            log.info("쿠팡 추천 링크 확보: %s", product["link"])
        else:
            log.info("쿠팡 링크 없음 — 링크 없이 초안 생성(파트너스 승인/키 확인).")

        # 사진 파일명을 먼저 알아야 Claude에 힌트를 줄 수 있어, 임시 slug로 수집한 뒤
        # 실제 slug 확정 후 이동이 복잡하므로: 사진은 keyword 기반 임시 slug로 수집.
        tmp_slug = _slugify(args.keyword, fallback=date_str.replace("-", ""))
        photos = _collect_photos(args.photos, tmp_slug)
        photo_names = [os.path.basename(p) for p in photos]

        article = _call_claude(args.keyword, notes, product, photo_names, args.tone, voice)

        # Claude가 준 slug로 사진 폴더를 정리(임시 slug와 다르면 이동)
        final_slug = _slugify(article.get("slug"), fallback=tmp_slug)
        if photos and final_slug != tmp_slug:
            photos = _relocate_photos(tmp_slug, final_slug, photo_names)
        article["slug"] = final_slug

        results = []
        if args.publish in ("site", "both"):
            md = _build_markdown(article, product, photos, date_str)
            path = _write_post(article, md, date_str)
            results.append(f"site:{os.path.relpath(path, REPO_ROOT)}")
        if args.publish in ("notion", "both"):
            blocks = _build_notion_blocks(article, product, photos, date_str)
            _write_notion(article, blocks, date_str)
            results.append("notion:발행대기")

        summary = f"'{args.keyword}' 초안 완성 → " + ", ".join(results)
        log.info(summary)
        notify.notify_success(STAGE, summary)

    except Exception as exc:
        log.exception("초안 생성 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)


def _relocate_photos(old_slug, new_slug, names):
    """임시 slug 폴더 → 확정 slug 폴더로 사진 이동, 새 웹 경로 반환."""
    old_dir = os.path.join(PHOTOS_ROOT, old_slug)
    new_dir = os.path.join(PHOTOS_ROOT, new_slug)
    os.makedirs(new_dir, exist_ok=True)
    web = []
    for n in names:
        src = os.path.join(old_dir, n)
        if os.path.exists(src):
            shutil.move(src, os.path.join(new_dir, n))
        web.append(f"/assets/photos/{new_slug}/{n}")
    if os.path.isdir(old_dir) and not os.listdir(old_dir):
        os.rmdir(old_dir)
    return web


if __name__ == "__main__":
    main()
