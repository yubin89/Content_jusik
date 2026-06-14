"""Telegram 알림. 성공 요약과 에러(트레이스백)를 폰으로 보낸다.

TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 설정되지 않았으면
조용히 건너뛴다(로그 경고만) — 아직 봇을 안 만든 단계에서도 코드가 죽지 않게.
"""
import os
import time
import traceback

import requests

from .logger import get_logger

log = get_logger("notify")
TIMEOUT = 15
MAX_LEN = 4000  # Telegram 메시지 길이 제한(4096) 안전 여유


def _creds():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send_message(text):
    """텔레그램 메시지 전송. 성공 True, 미설정/실패 False."""
    token, chat_id = _creds()
    if not token or not chat_id:
        log.warning("Telegram 미설정(TELEGRAM_BOT_TOKEN/CHAT_ID) — 알림을 건너뜁니다.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:MAX_LEN],
        "disable_web_page_preview": True,
    }
    delay = 2
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT)
            if resp.status_code == 200:
                return True
            log.warning("Telegram 응답 %s: %s", resp.status_code, resp.text[:200])
        except requests.RequestException as exc:
            log.warning("Telegram 전송 실패 (%s/3): %s", attempt, exc)
        time.sleep(delay)
        delay *= 2
    return False


def notify_success(stage, summary):
    return send_message(f"✅ [{stage}] 성공\n{summary}")


def notify_error(stage, exc):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    message = f"❌ [{stage}] 실패\n{type(exc).__name__}: {exc}\n\n{tb}"
    return send_message(message)
