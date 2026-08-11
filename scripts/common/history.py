"""콘텐츠 발행 이력 — 같은 종목이 계속 반복 선정되는 것을 막기 위한 기록.

data/content_history.json에 (날짜·티커·종목명)을 남긴다. pykrx_scan.yml이
스냅샷을 레포에 커밋하는 것과 같은 패턴 — 워크플로가 실행 후 git commit한다.
"""
import json
import os

from .logger import get_logger

log = get_logger("history")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PATH = os.path.join(_ROOT, "data", "content_history.json")
_KEEP = 60   # 파일이 무한정 커지지 않도록 최근 N건만 보관


def _load_all():
    if not os.path.exists(_PATH):
        return []
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("이력 파일 읽기 실패(빈 이력으로 시작): %s", exc)
        return []


def recent_tickers(days=14):
    """최근 days일 내 다룬 (티커, 종목명) 리스트. 최신순."""
    from datetime import datetime, timedelta
    import pytz

    kst = pytz.timezone("Asia/Seoul")
    cutoff = datetime.now(kst) - timedelta(days=days)
    out = []
    for entry in reversed(_load_all()):
        try:
            entry_date = kst.localize(datetime.strptime(entry["date"], "%Y-%m-%d"))
        except Exception:
            continue
        if entry_date >= cutoff:
            out.append((entry.get("ticker", ""), entry.get("name", "")))
    return out


def record(date_str, ticker, name, source):
    """오늘 다룬 종목을 이력에 추가하고 파일에 저장(최근 _KEEP건만 유지)."""
    if not ticker and not name:
        return
    entries = _load_all()
    entries.append({"date": date_str, "ticker": ticker or "", "name": name or "", "source": source})
    entries = entries[-_KEEP:]
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    log.info("콘텐츠 이력 기록: %s(%s)", name, ticker)
