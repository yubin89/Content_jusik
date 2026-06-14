"""Step 1 연결 테스트.

GitHub Actions에서 수동 실행하면:
  1) Notion 테스트 DB에 페이지 1개를 생성하고
  2) Telegram으로 '성공' 알림을 보낸다.
실패하면 로그 + Telegram 에러 알림 후 비정상 종료(Actions 빨간불).

실행: python -m scripts.test_connection
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.common import config, notify, notion_client
from scripts.common.logger import get_logger

STAGE = "연결 테스트"
log = get_logger("test_connection")


def main():
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    log.info("연결 테스트 시작 (KST %s)", now_kst)

    # 1) 필수 키 확인 (Telegram은 선택 — 없으면 알림만 건너뜀)
    config.require(["NOTION_TOKEN", "NOTION_TEST_DB"])
    db_id = config.get("NOTION_TEST_DB")

    # 2) Notion에 테스트 페이지 작성
    title = f"\U0001f527 연결 테스트 {now_kst} KST"
    blocks = [
        notion_client.heading("파이프라인 연결 테스트", 2),
        notion_client.paragraph(f"이 페이지는 GitHub Actions가 자동으로 생성했습니다. (KST {now_kst})"),
        notion_client.bullet("Notion 쓰기 정상"),
        notion_client.bullet("GitHub Actions 실행 정상"),
    ]
    page = notion_client.create_page_in_database(db_id, title, blocks)
    page_url = page.get("url", "(url 없음)")
    log.info("Notion 페이지 생성 완료: %s", page_url)

    # 3) Telegram 성공 알림
    sent = notify.notify_success(STAGE, f"Notion 테스트 페이지 생성됨\n{page_url}")
    if sent:
        log.info("Telegram 알림 전송 완료")
    else:
        log.info("Telegram 미설정 또는 전송 실패 — 노션 페이지는 정상 생성됨")

    log.info("연결 테스트 완료 ✅")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 최상위에서 모든 예외를 잡아 알림
        log.exception("연결 테스트 실패")
        notify.notify_error(STAGE, exc)
        sys.exit(1)
