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


def _latest_picks_file():
    files = sorted(glob.glob(os.path.join(_DATA_DIR, "*.json")))
    files = [f for f in files if "_evaluated" not in os.path.basename(f)]
    return files[-1] if files else None


def _fmt_date(yyyymmdd):
    if len(yyyymmdd) == 8:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return yyyymmdd


def supply_demand_bar_chart(top_n=6):
    """기관 순매수 상위 종목 막대차트. (png_bytes, alt_text) 반환. 데이터 없으면 (None, None)."""
    path = _latest_picks_file()
    if not path:
        log.info("pykrx_picks 데이터 없음 — 차트 생략")
        return None, None

    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        log.warning("차트 데이터 로드 실패: %s", exc)
        return None, None

    picks = data.get("picks", [])
    if not picks:
        return None, None

    picks = sorted(picks, key=lambda p: p.get("inst_sum", 0) or 0, reverse=True)[:top_n]
    names = [p.get("name") or p.get("ticker", "") for p in picks]
    vals = [(p.get("inst_sum", 0) or 0) / 1e8 for p in picks]  # 억원 단위
    date_str = _fmt_date(str(data.get("date", "")))

    _set_korean_font()
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=130)
    # 큰 값이 위로 오게 역순 배치
    ax.barh(names[::-1], vals[::-1], color="#2563eb")
    ax.set_xlabel("기관 순매수 (억원)")
    ax.set_title(f"{date_str} 기관 순매수 상위 종목", fontsize=13, fontweight="bold")
    for i, v in enumerate(vals[::-1]):
        ax.text(v, i, f" {v:,.0f}", va="center", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)

    alt = (
        f"{date_str} 기관 순매수 상위 종목 막대그래프. "
        + ", ".join(f"{n} {v:,.0f}억원" for n, v in zip(names, vals))
    )
    return buf.getvalue(), alt
