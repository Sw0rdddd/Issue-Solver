from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class ReviewResult(BaseModel):
    """Review Agent 对当前代码修改进行审查后产生的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["APPROVE", "REQUEST_CHANGES"] = Field(
        description="审查结论：APPROVE 仅在不存在阻断问题时使用；否则为 REQUEST_CHANGES。",
    )
    issues: list[NonEmptyText] = Field(
        description="阻止当前修改通过的具体、可验证问题；APPROVE 时必须为空。"
    )
    suggestions: list[NonEmptyText] = Field(
        description="不阻断当前结论的改进建议或可能的修复方向。"
    )
    remaining_risks: list[NonEmptyText] = Field(
        description="即使 APPROVE 后仍无法完全排除的具体风险。"
    )

    @model_validator(mode="after")
    def validate_verdict_matches_issues(self) -> "ReviewResult":
        if self.verdict == "APPROVE" and self.issues:
            raise ValueError("APPROVE 时 issues 必须为空。")
        if self.verdict == "REQUEST_CHANGES" and not self.issues:
            raise ValueError("REQUEST_CHANGES 时必须至少包含一个具体 issue。")
        return self
