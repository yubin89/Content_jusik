"""환경변수(=GitHub Secrets) 로드 및 검증.

키는 코드에 절대 넣지 않는다. GitHub repo Settings > Secrets and variables > Actions
에 저장하고, 워크플로가 env로 주입한 값을 여기서 읽는다.
필수 키가 없으면 '시작 즉시' 친절한 메시지로 실패시켜, 어디서 막혔는지 바로 보이게 한다.
"""
import os


class ConfigError(Exception):
    """필수 환경변수가 없을 때 발생."""


def get(name, required=True, default=None):
    """환경변수 하나를 읽는다. required=True인데 비어 있으면 ConfigError."""
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise ConfigError(
            f"필수 환경변수 '{name}' 가 비어 있습니다.\n"
            f"→ GitHub repo Settings > Secrets and variables > Actions 에서 '{name}' 를 추가하세요."
        )
    return value


def get_optional(name, default=None):
    """없어도 되는 환경변수. 없으면 default 반환."""
    return os.environ.get(name) or default


def require(names):
    """여러 필수 키를 한 번에 검증. 없는 키를 모아 한 번에 알려준다."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise ConfigError(
            "다음 필수 환경변수(Secrets)가 설정되지 않았습니다: "
            + ", ".join(missing)
            + "\n→ GitHub repo Settings > Secrets and variables > Actions 에서 추가하세요."
        )
    return {n: os.environ[n] for n in names}
