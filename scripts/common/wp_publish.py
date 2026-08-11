"""워드프레스 발행 공통 헬퍼 — seo_writer/econ_news/weekly_calendar가 공유.

인증·미디어 업로드·이미지 삽입·초안 생성·본문 표/FAQ 스타일을 담당한다.
"""
import requests

from . import config
from .logger import get_logger

log = get_logger("wp_publish")
HTTP_TIMEOUT = 60

# 본문 표(.ms-tbl)·FAQ(.ms-faq) 스타일 — 글 상단에 주입(캐롯 블로그 느낌)
STYLE_BLOCK = """<style>
.ms-tbl{width:100%;border-collapse:collapse;margin:24px 0;font-size:15px}
.ms-tbl th{background:#f3f0ff;color:#6d28d9;text-align:left;padding:13px 16px;font-weight:700}
.ms-tbl td{padding:13px 16px;border-top:1px solid #eee}
.ms-tbl td:first-child{color:#ea580c;font-weight:700}
.ms-faq{margin:28px 0}
.ms-faq .ms-q{border-left:4px solid #2563eb;padding:6px 14px;margin-top:18px;font-weight:700;color:#1d4ed8}
.ms-faq .ms-a{padding:4px 14px 4px 18px;color:#374151;line-height:1.7}
</style>
"""


def wp_auth():
    return (config.get("WP_USER"), config.get("WP_APP_PASSWORD"))


def wp_base():
    return config.get("WP_URL").rstrip("/") + "/wp-json/wp/v2"


def resolve_tag_ids(tag_names):
    """태그 이름 → 워드프레스 태그 ID로 변환(없으면 생성). 실패해도 글 저장은 막지 않음."""
    ids = []
    base = wp_base()
    auth = wp_auth()
    for name in tag_names or []:
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


def upload_media(image_bytes, content_type, filename, alt=None):
    """워드프레스 미디어 업로드 → (media_id, source_url). alt 있으면 대체텍스트도 설정."""
    ext = "png" if "png" in content_type else "jpg"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}.{ext}"',
        "Content-Type": content_type,
    }
    resp = requests.post(
        f"{wp_base()}/media", headers=headers, data=image_bytes,
        auth=wp_auth(), timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    media = resp.json()
    mid = media["id"]
    if alt:
        try:
            r = requests.post(
                f"{wp_base()}/media/{mid}", json={"alt_text": alt, "caption": alt},
                auth=wp_auth(), timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
        except Exception as exc:
            log.warning("이미지 alt 설정 실패(건너뜀): %s", exc)
    return mid, media.get("source_url", "")


def insert_inline_image(html, img_url, alt, caption=None, before_h2=0):
    """본문의 (before_h2)번째 <h2> 앞에 이미지(+캡션)를 삽입.
    해당 <h2>가 없으면 맨 뒤에 붙인다(이미지들이 겹치지 않게 위치를 분산)."""
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    fig = f'<figure><img src="{img_url}" alt="{alt}" />{cap}</figure>'
    idx, start = -1, 0
    for _ in range(before_h2 + 1):
        idx = html.find("<h2", start)
        if idx == -1:
            break
        start = idx + 3
    if idx == -1:
        return html + fig
    return html[:idx] + fig + html[idx:]


def create_draft(title, content_html, excerpt="", tags=None, featured_media=None,
                  fallback_title=""):
    """워드프레스에 초안(draft) 글을 만들고 편집/미리보기 URL을 반환.
    STYLE_BLOCK(표·FAQ 스타일)을 본문 앞에 자동 주입한다."""
    payload = {
        "title": title or fallback_title,
        "content": STYLE_BLOCK + (content_html or ""),
        "excerpt": excerpt,
        "status": "draft",
    }
    if featured_media:
        payload["featured_media"] = featured_media
    tag_ids = resolve_tag_ids(tags)
    if tag_ids:
        payload["tags"] = tag_ids

    resp = requests.post(
        f"{wp_base()}/posts", json=payload, auth=wp_auth(), timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("link") or data.get("guid", {}).get("rendered", "")
