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

    repair_round: int = Field(strict=True, ge=1)
    stage_call: int = Field(strict=True, ge=1)
    item_index: int = Field(strict=True, ge=1)
    focus: NonEmptyText
    title: NonEmptyText | None = None
    status: Literal["PASSED", "FAILED"]
    duration: float = Field(strict=True, ge=0, allow_inf_nan=False)
    failure: FailureInfo | None = None

    @model_validator(mode="after")
    def validate_failure_matches_status(self) -> "ExploreExecution":
        if self.status == "PASSED" and self.failure is not None:
            raise ValueError("PASSED 的 ExploreExecution 不能包含 failure。")
        if self.status == "FAILED" and self.failure is None:
            raise ValueError("FAILED 的 ExploreExecution 必须包含 failure。")
        return self
