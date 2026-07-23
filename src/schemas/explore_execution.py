from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from schemas.failure import FailureInfo


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class ExploreExecution(BaseModel):
    """单个并行 Explore 分支的确定性执行信息。"""

    model_config = ConfigDict(extra="forbid")

    repair_round: int = Field(
        strict=True,
        ge=1,
        description="该 Explore 分支所属的修复轮次。",
    )
    stage_call: int = Field(
        strict=True,
        ge=1,
        description="同一修复轮内的第几批 Explore 调用。",
    )
    item_index: int = Field(
        strict=True,
        ge=1,
        description="当前并行 Explore 任务在本批中的一基索引。",
    )
    focus: NonEmptyText = Field(description="本分支实际执行的调查目标。")
    title: NonEmptyText | None = Field(
        default=None,
        description="供终端展示的简洁调查标题。",
    )
    status: Literal["PASSED", "FAILED"] = Field(
        description="该 Explore 分支的确定性执行状态。"
    )
    duration: float = Field(
        strict=True,
        ge=0,
        allow_inf_nan=False,
        description="该分支从开始到结束的耗时，单位为秒。",
    )
    failure: FailureInfo | None = Field(
        default=None,
        description="仅 status=FAILED 时提供的结构化失败事实。",
    )

    @model_validator(mode="after")
    def validate_failure_matches_status(self) -> "ExploreExecution":
        if self.status == "PASSED" and self.failure is not None:
            raise ValueError("PASSED 的 ExploreExecution 不能包含 failure。")
        if self.status == "FAILED" and self.failure is None:
            raise ValueError("FAILED 的 ExploreExecution 必须包含 failure。")
        return self
