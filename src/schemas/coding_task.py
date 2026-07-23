from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from schemas.path_validation import (
    normalize_pytest_targets,
    normalize_repo_relative_paths,
)


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class CodingTask(BaseModel):
    """Coordinator 交给 Coding Agent 的结构化修改任务。"""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyText = Field(
        description="仅为解决当前 Issue 而执行的最小修改目标。"
    )
    acceptance_criteria: list[NonEmptyText] = Field(
        min_length=1,
        description="从 Issue 原始验收条件复述的可验证条件，不得扩展或改写需求。",
    )
    relevant_files: list[NonEmptyText] = Field(
        min_length=1,
        description="有探索证据支持、建议重点读取的仓库相对文件路径；可包含测试文件。",
    )
    root_cause: NonEmptyText = Field(
        description="基于 ExploreReport 代码证据确认的根因，不得编造。"
    )
    allowed_scope: list[NonEmptyText] = Field(
        min_length=1,
        description=(
            "唯一允许修改的仓库相对文件或目录范围；必须覆盖已有修改，"
            "不得包含测试文件。"
        ),
    )
    test_targets: list[NonEmptyText] = Field(
        min_length=1,
        max_length=10,
        description=(
            "经 ExploreReport 证据确认的既有仓库相对 .py 测试文件或 pytest node ID；"
            "由 Test 节点执行，不得是命令或自然语言。"
        ),
    )

    @field_validator("relevant_files", "allowed_scope")
    @classmethod
    def validate_repo_relative_paths(cls, values: list[str]) -> list[str]:
        return normalize_repo_relative_paths(values)

    @field_validator("test_targets")
    @classmethod
    def validate_test_targets(cls, values: list[str]) -> list[str]:
        return normalize_pytest_targets(values)
