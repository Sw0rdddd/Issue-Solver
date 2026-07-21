from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints


EnvironmentKind = Literal["VENV", "CONDA"]
EnvironmentSource = Literal[".venv", "venv", ".conda"]


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class EnvironmentInfo(BaseModel):
    """Initialize 选定并验证后的目标仓库 Python 环境。"""

    model_config = ConfigDict(extra="forbid")

    kind: EnvironmentKind
    root_path: NonEmptyText
    python_executable: NonEmptyText
    pytest_version: NonEmptyText
    source: EnvironmentSource
