"""Pillow로 '디자인된 수급 데이터 카드'(PNG)를 만든다 — 본문 데이터 시각자료.

data/pykrx_picks/YYYYMMDD.json 에서 글의 대표 종목을 찾아
종목명·티커·연속 순매수 배지·핵심 스탯 3칸·미니 막대(상위 순매수, 대상 강조)를
한 장의 카드로 렌더링한다. 폰트는 레포 번들(assets/fonts/NanumGothic).
브라우저·외부 API 불필요(가벼움). 대상 종목이 데이터에 없으면 (None, None).
"""
import glob
import io
import json
import os

from PIL import Image, ImageDraw, ImageFont

from .logger import get_logger

log = get_logger("cards")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_ROOT, "data", "pykrx_picks")
_FONT_R = os.path.join(_ROOT, "assets", "fonts", "NanumGothic-Regular.ttf")
_FONT_B = os.path.join(_ROOT, "assets", "fonts", "NanumGothic-Bold.ttf")

# 색상
_BG = (15, 23, 42)
_CARD = (30, 41, 59)
_ACCENT = (220, 38, 38)   # 강조(대상 종목)
_BLUE = (96, 165, 250)
_BAR = (147, 197, 253)
_WHITE = (241, 245, 249)
_GRAY = (148, 163, 184)


def _font(bold, size):
    return ImageFont.truetype(_FONT_B if bold else _FONT_R, size)


def _recent_picks_files(limit=6):
    files = sorted(glob.glob(os.path.join(_DATA_DIR, "*.json")))
    files = [f for f in files if "_evaluated" not in os.path.basename(f)]
    return list(reversed(files))[:limit]


def _norm(t):
    return str(t or "").zfill(6)


def _find_snapshot(ticker=None, name=None, limit=6):
    """대상 종목이 포함된 스냅샷(dict)을 최근 파일에서 탐색."""
    for path in _recent_picks_files(limit):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for p in data.get("picks", []):
            if ticker and _norm(p.get("ticker")) == _norm(ticker):
                return data
            if name and p.get("name") == name:
                return data
    return None


def _fmt_date(yyyymmdd):
    s = str(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s


def supply_demand_card(ticker=None, name=None):
    """수급 데이터 카드. (png_bytes, alt) 반환. 대상 종목이 데이터에 없으면 (None, None)."""
    data = _find_snapshot(ticker, name)
    if not data:
        log.info("카드 대상 종목 데이터 없음 — 생략 (ticker=%s, name=%s)", ticker, name)
        return None, None

    picks = sorted(data.get("picks", []), key=lambda p: p.get("inst_sum", 0) or 0, reverse=True)

    def _is_t(p):
        return (ticker and _norm(p.get("ticker")) == _norm(ticker)) or (name and p.get("name") == name)

    target = next((p for p in picks if _is_t(p)), None)
    if not target:
        return None, None

    date_str = _fmt_date(data.get("date", ""))
    t_name = target.get("name") or _norm(target.get("ticker"))
    t_ticker = _norm(target.get("ticker"))
    inst_eok = (target.get("inst_sum", 0) or 0) / 1e8
    close = target.get("entry_close", 0) or 0
    streak = target.get("streak", 0) or 0

    # 미니 막대: 상위 4개(+ 대상 강제 포함)
    top = picks[:4]
    if not any(_is_t(p) for p in top):
        top = picks[:3] + [target]
        top = sorted(top, key=lambda p: p.get("inst_sum", 0) or 0, reverse=True)

    W, H = 1200, 630
    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)

    d.text((60, 44), f"오늘의 수급 브리핑  |  {date_str}", font=_font(False, 26), fill=_GRAY)
    d.text((60, 88), t_name, font=_font(True, 66), fill=_WHITE)
    d.text((60, 166), t_ticker, font=_font(False, 28), fill=_BLUE)

    if streak:
        badge = f"기관 {streak}일 연속 순매수"
        bw = d.textlength(badge, font=_font(True, 28))
        d.rounded_rectangle((60, 212, 60 + bw + 40, 264), radius=26, fill=_ACCENT)
        d.text((80, 223), badge, font=_font(True, 28), fill=_WHITE)

    # 스탯 3칸
    stats = [
        ("기관 순매수", f"{inst_eok:,.0f}억"),
        ("종가", f"{close:,.0f}원"),
        ("연속 순매수", f"{streak}일"),
    ]
    x0, y0, cw, ch, gap = 60, 296, 340, 122, 30
    for i, (k, v) in enumerate(stats):
        x = x0 + i * (cw + gap)
        d.rounded_rectangle((x, y0, x + cw, y0 + ch), radius=16, fill=_CARD)
        d.text((x + 26, y0 + 22), k, font=_font(False, 24), fill=_GRAY)
        d.text((x + 26, y0 + 58), v, font=_font(True, 42), fill=_WHITE)

    # 미니 막대
    names = [p.get("name") or _norm(p.get("ticker")) for p in top]
    vals = [(p.get("inst_sum", 0) or 0) / 1e8 for p in top]
    mx = max(vals) or 1
    bx, by = 60, 444
    d.text((bx, by), "기관 순매수 상위 종목 (억원)", font=_font(True, 23), fill=_WHITE)
    for i, (p, n, v) in enumerate(zip(top, names, vals)):
        yy = by + 32 + i * 27
        col = _ACCENT if _is_t(p) else _BAR
        d.text((bx, yy - 2), n[:8], font=_font(False, 18), fill=_GRAY)
        x_start = bx + 170
        bar_w = int((W - 60 - x_start - 90) * v / mx)
        d.rectangle((x_start, yy, x_start + bar_w, yy + 16), fill=col)
        d.text((x_start + bar_w + 10, yy - 3), f"{v:,.0f}", font=_font(False, 17), fill=_WHITE)

    d.text((60, H - 34), "moneyshot.co.kr  ·  자료: 한국거래소(KRX)", font=_font(False, 19), fill=_GRAY)

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    bars_txt = ", ".join(f"{n} {v:,.0f}억원" for n, v in zip(names, vals))
    alt = (
        f"{date_str} {t_name} 수급 카드 — 기관 순매수 {inst_eok:,.0f}억원, "
        f"{streak}일 연속. 상위 종목: {bars_txt}"
    )
    return buf.getvalue(), alt
