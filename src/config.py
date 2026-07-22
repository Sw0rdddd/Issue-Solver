from pathlib import Path
from typing import Annotated, Any, Literal, cast

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)


PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]


class Setting(BaseSettings):
    """统一读取本项目 .env 覆盖后的运行配置。"""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    API_KEY: str | None = None
    BASE_URL: str | None = None
    MODEL_NAME: str | None = None
    REASONING_HISTORY: Literal["auto", "true", "false"] = "auto"
    GITHUB_TOKEN: str | None = None
    MAX_CYCLES: PositiveInt = 5
    AGENT_RECURSION_LIMIT: PositiveInt = 60
    MAX_EXPLORE_BATCHES: PositiveInt = 5
    TEST_TIMEOUT: PositiveFloat = 300.0
    TEST_TAIL_LINES: PositiveInt = 100
    RUN_ROOT: Path = Path(".issue-solver-runs")

    @field_validator(
        "API_KEY",
        "BASE_URL",
        "MODEL_NAME",
        "GITHUB_TOKEN",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("配置文本必须是字符串。")
        normalized = value.strip()
        return normalized or None

    @field_validator("REASONING_HISTORY", mode="before")
    @classmethod
    def _normalize_reasoning_history(
        cls,
        value: Any,
    ) -> Literal["auto", "true", "false"]:
        if not isinstance(value, str):
            raise ValueError(
                "配置 REASONING_HISTORY 必须是 auto、true 或 false。"
            )
        normalized = value.strip().lower()
        if normalized not in {"auto", "true", "false"}:
            raise ValueError(
                "配置 REASONING_HISTORY 必须是 auto、true 或 false。"
            )
        return cast(Literal["auto", "true", "false"], normalized)

    @field_validator("RUN_ROOT", mode="before")
    @classmethod
    def _normalize_run_root(cls, value: Any) -> Path:
        if not isinstance(value, (str, Path)):
            raise ValueError("配置 RUN_ROOT 必须是路径。")
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("配置 RUN_ROOT 不能为空。")
        return Path(normalized).expanduser()
