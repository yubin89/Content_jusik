"""구조화 로깅. 모든 스크립트가 동일한 형식으로 로그를 남겨,
GitHub Actions 로그에서 '언제/어느 단계/무슨 일'이 바로 보이게 한다.
"""
import logging
import sys


def get_logger(name="pipeline"):
    logger = logging.getLogger(name)
    if logger.handlers:  # 중복 핸들러 방지
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger
