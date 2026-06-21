"""큐레이션 FinTwit 계정 + 가중치.

미국 유명 주식/금융 계정(팔로워 5만↑, 종목·차트 위주 가산). 가중치는 속보·신뢰도가
높은 네임드일수록 크게 둔다(가중 언급수 = Σ 계정 가중치). 팔로워 수/핸들은 변동 가능하므로
운영하며 자유롭게 추가·삭제·가중치 조정한다(여기 한 곳만 고치면 전체 파이프라인에 반영).
"""

# 핸들(소문자 비교) → 가중치
ACCOUNTS = {
    # 속보/플로우 (가중 3)
    "deitaone": 3,          # Walter Bloomberg, 속보
    "firstsquawk": 3,       # 속보 뉴스와이어
    "unusual_whales": 3,    # 옵션 플로우/속보
    # 차트/기술적 분석 (가중 2~3) — 종목·차트 위주
    "peterlbrandt": 3,      # 클래식 차트 레전드
    "markminervini": 2,     # 모멘텀/성장주 차트
    "alphatrends": 2,       # Brian Shannon, TA
    "allstarcharts": 2,     # JC Parets, 차트
    "hmeisler": 2,          # Helene Meisler, TA
    "northmantrader": 2,    # Sven Henrich, 차트
    "investorslive": 2,     # Nathan Michaud, 트레이딩
    "gameoftrades_": 2,     # 기술적/매크로
    "barchart": 2,          # 차트/데이터
    "timothysykes": 1,      # 단타/저가주 (노이즈 가능 → 1)
    # 매크로/분석 (가중 2)
    "kobeissiletter": 2,
    "zerohedge": 2,
    "charliebilello": 2,
    "optionshawk": 2,
    "mr_derivatives": 2,
    "stocktalkweekly": 2,
    "elerianm": 2,
    "biancoresearch": 2,
    # 일반/대중 (가중 1)
    "wsbchairman": 1,
    "stocktwits": 1,
    "marketwatch": 1,
    "rampcapitalllc": 1,
}

DEFAULT_WEIGHT = 1  # 목록에 없는 계정에서 수집된 트윗 가중치(보수적)


def weight_for(handle):
    """핸들(대소문자 무관)의 가중치를 반환."""
    if not handle:
        return DEFAULT_WEIGHT
    return ACCOUNTS.get(handle.lstrip("@").lower(), DEFAULT_WEIGHT)


def handles():
    """수집 대상 핸들 리스트."""
    return list(ACCOUNTS.keys())
