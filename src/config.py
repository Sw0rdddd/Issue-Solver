import os
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _optional_text(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"配置 {name} 必须是整数。") from exc
    if value < 1:
        raise ValueError(f"配置 {name} 必须大于 0。")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"配置 {name} 必须是数字。") from exc
    if value <= 0:
        raise ValueError(f"配置 {name} 必须大于 0。")
    return value


def _reasoning_history_mode() -> Literal["auto", "true", "false"]:
    value = os.environ.get("REASONING_HISTORY", "auto").strip().lower()
    if value not in {"auto", "true", "false"}:
        raise ValueError(
            "配置 REASONING_HISTORY 必须是 auto、true 或 false。"
        )
    return cast(Literal["auto", "true", "false"], value)


class Setting:
    """统一读取本项目 .env 覆盖后的运行配置。"""

    def __init__(self) -> None:
        self.API_KEY = _optional_text("API_KEY")
        self.BASE_URL = _optional_text("BASE_URL")
        self.MODEL_NAME = _optional_text("MODEL_NAME")
        self.REASONING_HISTORY = _reasoning_history_mode()
        self.GITHUB_TOKEN = _optional_text("GITHUB_TOKEN")
        self.MAX_CYCLES = _positive_int("MAX_CYCLES", 5)
        self.TEST_TIMEOUT = _positive_float("TEST_TIMEOUT", 300.0)
        self.TEST_TAIL_LINES = _positive_int("TEST_TAIL_LINES", 100)
        run_root = os.environ.get("RUN_ROOT", ".issue-solver-runs").strip()
        if not run_root:
            raise ValueError("配置 RUN_ROOT 不能为空。")
        self.RUN_ROOT = Path(run_root).expanduser()
