from pathlib import PurePosixPath
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class CodingTask(BaseModel):
    """Coordinator 交给 Coding Agent 的结构化修改任务。"""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyText = Field(description="本次修改的具体目标")
    acceptance_criteria: list[NonEmptyText] = Field(
        min_length=1,
        description="判断本次修改完成的可验证条件",
    )
    relevant_files: list[NonEmptyText] = Field(
        min_length=1,
        description="建议重点读取的仓库相对文件路径",
    )
    root_cause: NonEmptyText = Field(description="基于探索证据确认的根因")
    allowed_scope: list[NonEmptyText] = Field(
        min_length=1,
        description="允许修改的仓库相对文件或目录范围",
    )
    validation: list[NonEmptyText] = Field(
        min_length=1,
        description="修改完成后需要由后续节点执行的验证",
    )

    @field_validator("relevant_files", "allowed_scope")
    @classmethod
    def validate_repo_relative_paths(cls, values: list[str]) -> list[str]:
        normalized_values: list[str] = []

        for value in values:
            normalized = value.replace("\\", "/")
            is_directory = normalized.endswith("/")
            normalized = normalized.rstrip("/")
            path = PurePosixPath(normalized)

            if (
                not normalized
                or normalized == "."
                or normalized.startswith("/")
                or (
                    len(normalized) >= 2
                    and normalized[0].isalpha()
                    and normalized[1] == ":"
                )
                or ".." in path.parts
            ):
                raise ValueError(f"必须是仓库内的相对路径：{value}")

            normalized_path = path.as_posix()
            if is_directory:
                normalized_path += "/"
            normalized_values.append(normalized_path)

        if len(set(normalized_values)) != len(normalized_values):
            raise ValueError("路径列表不能包含重复项。")

        return normalized_values
