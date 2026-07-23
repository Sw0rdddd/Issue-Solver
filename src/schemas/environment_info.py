from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


EnvironmentKind = Literal["VENV", "CONDA"]
EnvironmentSource = Literal[".venv", "venv", ".conda"]


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class EnvironmentInfo(BaseModel):
    """Initialize 选定并验证后的目标仓库 Python 环境。"""

    model_config = ConfigDict(extra="forbid")

    kind: EnvironmentKind = Field(
        description="经预检确认的 Python 环境类型。"
    )
    root_path: NonEmptyText = Field(
        description="经预检确认的目标环境根目录。"
    )
    python_executable: NonEmptyText = Field(
        description="后续测试必须使用的目标环境 Python 可执行文件。"
    )
    pytest_version: NonEmptyText = Field(
        description="在目标环境中实际检测到的 pytest 版本。"
    )
    source: EnvironmentSource = Field(
        description="用于发现该环境的目标仓库目录标记。"
    )
