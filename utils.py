from datetime import datetime

from config import HEART


def ts() -> str:
    return datetime.now().strftime("[%H:%M:%S.%f")[:-3] + "]"


def simple_toggle(current: str) -> str:
    if current.endswith(HEART):
        return current[: -len(HEART)]
    return current + HEART
