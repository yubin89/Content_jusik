"""실제 수집 데이터로 차트(PNG)를 만든다 — SEO/E-E-A-T용 원본 시각자료.

data/pykrx_picks/YYYYMMDD.json (기관 순매수 상위 종목 스냅샷)을 읽어
'오늘의 기관 순매수 상위 종목' 막대그래프를 그린다.
matplotlib은 이미 의존성(pykrx)으로 설치돼 있다.

한글 폰트: CI(ubuntu)엔 기본 한글 폰트가 없어 라벨이 깨질 수 있다.
워크플로에서 fonts-nanum 설치 후 NanumGothic을 사용한다(없으면 조용히 폴백).
"""
import glob
import io
import json
import os

import matplotlib
matplotlib.use("Agg")  # 서버(비GUI)에서 렌더링
import matplotlib.pyplot as plt
from matplotlib import font_manager

from .logger import get_logger

log = get_logger("charts")

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "pykrx_picks",
)


def _set_korean_font():
    """사용 가능한 한글 폰트를 matplotlib에 설정. 없으면 폴백(라벨 깨질 수 있음)."""
    plt.rcParams["axes.unicode_minus"] = False
    for name in ("NanumGothic", "NanumBarunGothic", "Malgun Gothic", "AppleGothic"):
        try:
            path = font_manager.findfont(name, fallback_to_default=False)
            if path:
                plt.rcParams["font.family"] = name
                return True
        except Exception:
            continue
    log.warning("한글 폰트를 찾지 못함 — 라벨이 깨질 수 있음")
    return False


def _recent_picks_files(limit=6):
    """최근 pykrx_picks 파일들을 최신순으로 반환(_evaluated 제외)."""
    files = sorted(glob.glob(os.path.join(_DATA_DIR, "*.json")))
    files = [f for f in files if "_evaluated" not in os.path.basename(f)]
    return list(reversed(files))[:limit]


def _fmt_date(yyyymmdd):
    if len(yyyymmdd) == 8:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return yyyymmdd


def _find_stock_snapshot(ticker=None, name=None, limit=6):
    """최근 파일들에서 해당 종목이 포함된 스냅샷(dict)을 찾는다. 못 찾으면 None."""
    ticker = (ticker or "").strip()
    name = (name or "").strip()
    for path in _recent_picks_files(limit):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for p in data.get("picks", []):
            if ticker and str(p.get("ticker", "")).zfill(6) == ticker.zfill(6):
                return data
            if name and p.get("name") == name:
                return data
    return None


def stock_focus_chart(ticker=None, name=None, top_n=6):
    """글의 대표 종목이 포함된 '기관 순매수 상위' 막대차트(해당 종목 강조).
    (png_bytes, alt_text) 반환. 대상 종목이 최근 수급 데이터에 없으면 (None, None)."""
    data = _find_stock_snapshot(ticker, name)
    if not data:
        log.info("차트 대상 종목 데이터 없음 — 차트 생략 (ticker=%s, name=%s)", ticker, name)
        return None, None

    picks = data.get("picks", [])
    picks = sorted(picks, key=lambda p: p.get("inst_sum", 0) or 0, reverse=True)
    # 대상 종목이 top_n 밖이면 강제로 포함
    def _is_target(p):
        return (
            (ticker and str(p.get("ticker", "")).zfill(6) == (ticker or "").zfill(6))
            or (name and p.get("name") == name)
        )

    top = picks[:top_n]
    if not any(_is_target(p) for p in top):
        target = next((p for p in picks if _is_target(p)), None)
        if target:
            top = top[: top_n - 1] + [target]
            top = sorted(top, key=lambda p: p.get("inst_sum", 0) or 0, reverse=True)

    names = [p.get("name") or p.get("ticker", "") for p in top]
    vals = [(p.get("inst_sum", 0) or 0) / 1e8 for p in top]  # 억원
    colors = ["#dc2626" if _is_target(p) else "#93c5fd" for p in top]  # 대상=빨강 강조
    date_str = _fmt_date(str(data.get("date", "")))
    target_name = name or next(
        (p.get("name") for p in top if _is_target(p)), ""
    )

    _set_korean_font()
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=130)
    ax.barh(names[::-1], vals[::-1], color=colors[::-1])
    ax.set_xlabel("기관 순매수 (억원)")
    ax.set_title(
        f"{date_str} 기관 순매수 상위 종목 (강조: {target_name})",
        fontsize=13, fontweight="bold",
    )
    for i, v in enumerate(vals[::-1]):
        ax.text(v, i, f" {v:,.0f}", va="center", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)

    alt = (
        f"{date_str} 기관 순매수 상위 종목 막대그래프({target_name} 강조). "
        + ", ".join(f"{n} {v:,.0f}억원" for n, v in zip(names, vals))
    )
    return buf.getvalue(), alt
