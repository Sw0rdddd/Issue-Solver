from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class EnvironmentInfo(BaseModel):
    """Initialize 选定并验证后的目标仓库 Python 环境。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["VENV", "CONDA"]
    root_path: NonEmptyText
    python_executable: NonEmptyText
    pytest_version: NonEmptyText
    source: Literal[".venv", "venv", ".conda"]
