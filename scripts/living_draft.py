"""scripts/living_draft.py — 리빙(생활) 리뷰 콘텐츠 오케스트레이터.

작가↔검수 에이전트를 반복시켜 초안을 다듬고(drafts/에 스테이징), 사람이 최종 컨펌하면
두 채널로 발행한다. 발행은 '생성'과 분리되어 절대 자동으로 나가지 않는다.

  ① Jekyll 포스트(site/_posts/*.md)  → 자체 사이트
  ② Notion '발행대기' DB              → 네이버 블로그 수동 복붙용

3단계 사용법:
  # 1) 생성 (작가→검수 반복, drafts/에 저장. 발행 안 함)
  python -m scripts.living_draft --keyword "에어프라이어 추천" --type 실사용후기 \
      --tone seo --photos ~/pics/af --product "에어프라이어" --max-rounds 2

  # 2) (선택) 사람 피드백으로 수동 재작성
  python -m scripts.living_draft --revise drafts/2026-07-18-airfryer.json \
      --feedback "도입부 더 짧게, 가격대 정보 추가"

  # 3) 최종 컨펌 후 발행
  python -m scripts.living_draft --publish-draft drafts/2026-07-18-airfryer.json --to site

필요 환경변수:
  ANTHROPIC_API_KEY                     (필수)
  COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY(선택 — 없으면 링크 없이)
  NOTION_TOKEN, NOTION_DB_LIVING        (--to notion|both 일 때)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (선택 — 알림)
  LIVING_MODEL                          (선택 — 기본 claude-sonnet-4-6)
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime

import pytz

from scripts.agents import reviewer, writer
from scripts.common import affiliate, config, coupang, notify
from scripts.common.logger import get_logger
import scripts.common.notion_client as nc

log = get_logger("living")
STAGE = "리빙 콘텐츠"
KST = pytz.timezone("Asia/Seoul")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_DIR = os.path.join(REPO_ROOT, "site", "_posts")
PHOTOS_ROOT = os.path.join(REPO_ROOT, "site", "assets", "photos")
DRAFTS_DIR = os.path.join(REPO_ROOT, "drafts")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
CONTENT_TYPES = ["실사용후기", "장점", "단점", "상품소개", "일상일기"]


# ---------------------------------------------------------------- 오케스트레이션

def generate(spec, product, photos, max_rounds, pass_score):
    """작가→검수 반복 루프. (최종 article, 검수 이력) 반환."""
    spec["photo_names"] = [os.path.basename(p) for p in photos]
    article = writer.write(spec)
    history = []
    for rnd in range(1, max_rounds + 1):
        critique = reviewer.review(article, spec, pass_score)
        history.append({"round": rnd, **critique})
        _log_round(rnd, max_rounds, critique)
        if critique.get("pass") or rnd == max_rounds:
            break
        article = writer.write(spec, prev_draft=article, guide=critique.get("rewrite_guide"))
    return article, history


def _log_round(rnd, total, c):
    issues = len(c.get("seo", [])) + len(c.get("content", [])) + len(c.get("structure", []))
    log.info(
        "라운드 %d/%d — 점수 %s, 통과 %s, 지적 %d건",
        rnd, total, c.get("score", "?"), c.get("pass"), issues,
    )
    for cat in ("seo", "content", "structure"):
        for item in c.get(cat, []):
            log.info("  · [%s] %s", cat, item)


# ---------------------------------------------------------------- 사진

def _slugify(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or fallback


def _collect_photos(src_dir, slug):
    """사진 폴더의 이미지를 site/assets/photos/<slug>/ 로 복사하고 웹 경로 리스트 반환."""
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
    web = []
    for f in files:
        shutil.copy2(os.path.join(src_dir, f), os.path.join(dest_dir, f))
        web.append(f"/assets/photos/{slug}/{f}")
    log.info("사진 %d장 복사 → %s", len(web), dest_dir)
    return web


def _relocate_photos(old_slug, new_slug, names):
    """임시 slug 폴더 → 확정 slug 폴더로 사진 이동, 새 웹 경로 반환."""
    if old_slug == new_slug:
        return [f"/assets/photos/{old_slug}/{n}" for n in names]
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


# ---------------------------------------------------------------- 빌더

def _yaml_escape(s):
    return '"' + str(s).replace('"', '\\"') + '"'


def _build_markdown(article, product, photos, date_str):
    """article dict → Jekyll 포스트 markdown."""
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

    extra = photos[1:] if photos else []
    for i, sec in enumerate(article.get("sections", [])):
        body.append(f"## {sec.get('heading', '')}\n")
        body.append(sec.get("body", "") + "\n")
        if i < len(extra):
            body.append(f"![{sec.get('heading','')}]({extra[i]})\n")

    if article.get("review"):
        body.append("## 실사용 후기\n")
        body.append(article["review"] + "\n")

    if article.get("faq"):
        body.append("## 자주 묻는 질문\n")
        for item in article["faq"]:
            body.append(f"**Q. {item.get('q','')}**\n")
            body.append(f"{item.get('a','')}\n")

    body.append(affiliate.markdown_block(product))
    return "\n".join(fm) + "\n" + "\n".join(body) + "\n"


def _build_notion_blocks(article, product, photos, date_str):
    """article dict → Notion 블록(네이버 수동 발행용 + 사진 배치 가이드)."""
    blocks = [
        nc.paragraph(f"📅 {date_str} · 초안 · 네이버에 복붙 후 사진 삽입"),
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


# ---------------------------------------------------------------- 스테이징(drafts/)

def _draft_paths(draft):
    base = f"{draft['date']}-{_slugify(draft.get('slug'), draft['date'].replace('-', ''))}"
    return (os.path.join(DRAFTS_DIR, base + ".json"),
            os.path.join(DRAFTS_DIR, base + ".md"))


def _save_draft(draft):
    """draft dict를 drafts/<date>-<slug>.json + .md(미리보기)로 저장. json 경로 반환."""
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    json_path, md_path = _draft_paths(draft)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(draft, fh, ensure_ascii=False, indent=2)
    md = _build_markdown(draft["article"], draft["product"], draft["photos"], draft["date"])
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    log.info("초안 저장: %s (미리보기: %s)", json_path, md_path)
    return json_path


def _load_draft(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- 발행

def _publish(draft, to):
    results = []
    article, product, photos, date_str = (
        draft["article"], draft["product"], draft["photos"], draft["date"]
    )
    if to in ("site", "both"):
        slug = _slugify(draft.get("slug"), date_str.replace("-", ""))
        os.makedirs(POSTS_DIR, exist_ok=True)
        path = os.path.join(POSTS_DIR, f"{date_str}-{slug}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_build_markdown(article, product, photos, date_str))
        log.info("Jekyll 포스트 발행: %s", path)
        results.append(f"site:{os.path.relpath(path, REPO_ROOT)}")
    if to in ("notion", "both"):
        config.require(["NOTION_TOKEN", "NOTION_DB_LIVING"])
        title = f"📝 {article.get('title', '리빙 초안')} ({date_str})"
        nc.create_page_in_database(
            config.get("NOTION_DB_LIVING"), title,
            _build_notion_blocks(article, product, photos, date_str),
        )
        log.info("Notion 발행대기 저장: %s", title)
        results.append("notion:발행대기")
    return results


# ---------------------------------------------------------------- 흐름

def _read_file(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return None


def _flow_generate(args):
    config.require(["ANTHROPIC_API_KEY"])
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    log.info("=== 생성 시작 — '%s' [%s] (%s) ===", args.keyword, args.type, date_str)

    notes = _read_file(args.notes) or args.notes
    voice = _read_file(args.voice_sample)
    product = coupang.resolve_product(args.product)
    log.info("쿠팡 링크: %s", product.get("link") or "없음(키/승인 확인)")

    tmp_slug = _slugify(args.keyword, fallback=date_str.replace("-", ""))
    photos = _collect_photos(args.photos, tmp_slug)

    spec = {
        "keyword": args.keyword, "ctype": args.type, "tone": args.tone,
        "voice_sample": voice, "notes": notes, "product": product,
    }
    article, history = generate(spec, product, photos, args.max_rounds, args.pass_score)

    final_slug = _slugify(article.get("slug"), fallback=tmp_slug)
    if photos:
        photos = _relocate_photos(tmp_slug, final_slug, [os.path.basename(p) for p in photos])
    spec.pop("photo_names", None)

    draft = {
        "date": date_str, "slug": final_slug, "spec": spec,
        "product": product, "photos": photos, "article": article, "history": history,
    }
    json_path = _save_draft(draft)
    last = history[-1] if history else {}
    summary = (
        f"'{args.keyword}' 초안 완성(점수 {last.get('score','?')}, "
        f"통과 {last.get('pass')}). 검토 후 발행:\n"
        f"  python -m scripts.living_draft --publish-draft {os.path.relpath(json_path, REPO_ROOT)} --to site"
    )
    log.info(summary)
    notify.notify_success(STAGE, summary)


def _flow_revise(args):
    config.require(["ANTHROPIC_API_KEY"])
    draft = _load_draft(args.revise)
    spec = draft["spec"]
    spec["photo_names"] = [os.path.basename(p) for p in draft.get("photos", [])]
    log.info("사람 피드백 반영 재작성 — %s", args.revise)
    article = writer.write(spec, prev_draft=draft["article"], guide=args.feedback)
    draft["article"] = article
    draft.setdefault("history", []).append({"round": "manual", "human_feedback": args.feedback})
    # slug이 바뀌면 사진 폴더도 맞춰 이동
    new_slug = _slugify(article.get("slug"), fallback=draft.get("slug"))
    if draft.get("photos") and new_slug != draft.get("slug"):
        draft["photos"] = _relocate_photos(
            draft["slug"], new_slug, [os.path.basename(p) for p in draft["photos"]]
        )
    draft["slug"] = new_slug
    spec.pop("photo_names", None)
    _save_draft(draft)
    log.info("재작성 완료 — 다시 검토 후 발행하세요.")


def _flow_publish(args):
    draft = _load_draft(args.publish_draft)
    results = _publish(draft, args.to)
    summary = f"'{draft['spec'].get('keyword','')}' 발행 완료 → " + ", ".join(results)
    log.info(summary)
    notify.notify_success(STAGE, summary)


def main(argv=None):
    ap = argparse.ArgumentParser(description="리빙 리뷰 콘텐츠 오케스트레이터(작가↔검수 루프)")
    # 생성
    ap.add_argument("--keyword", help="핵심 키워드/주제 (생성 시 필수)")
    ap.add_argument("--type", choices=CONTENT_TYPES, default="실사용후기", help="컨텐츠 유형")
    ap.add_argument("--product", default="", help="쿠팡 상품 URL 또는 검색어")
    ap.add_argument("--photos", default="", help="사진 폴더 경로")
    ap.add_argument("--notes", default="", help="실사용 메모(텍스트 또는 파일 경로)")
    ap.add_argument("--tone", choices=["my", "seo"], default="seo")
    ap.add_argument("--voice-sample", default="", help="'my' 말투 샘플 파일")
    ap.add_argument("--max-rounds", type=int, default=2, help="작가↔검수 최대 반복(기본 2)")
    ap.add_argument("--pass-score", type=int, default=80, help="검수 통과 점수(기본 80)")
    # 수동 재작성
    ap.add_argument("--revise", default="", help="재작성할 draft json 경로")
    ap.add_argument("--feedback", default="", help="--revise 시 사람 피드백")
    # 발행
    ap.add_argument("--publish-draft", default="", help="발행할 draft json 경로")
    ap.add_argument("--to", choices=["site", "notion", "both"], default="site")
    args = ap.parse_args(argv)

    try:
        if args.publish_draft:
            _flow_publish(args)
        elif args.revise:
            if not args.feedback:
                ap.error("--revise 에는 --feedback 이 필요합니다.")
            _flow_revise(args)
        else:
            if not args.keyword:
                ap.error("생성하려면 --keyword 가 필요합니다.")
            _flow_generate(args)
    except SystemExit:
        raise
    except Exception as exc:
        log.exception("실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
