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
        description="审查结论",
    )
    issues: list[NonEmptyText] = Field(description="阻止当前修改通过的具体问题")
    suggestions: list[NonEmptyText] = Field(description="建议的修复方式或非阻断改进")
    remaining_risks: list[NonEmptyText] = Field(description="即使通过仍可能存在的风险")

    @model_validator(mode="after")
    def validate_verdict_matches_issues(self) -> "ReviewResult":
        if self.verdict == "APPROVE" and self.issues:
            raise ValueError("APPROVE 时 issues 必须为空。")
        if self.verdict == "REQUEST_CHANGES" and not self.issues:
            raise ValueError("REQUEST_CHANGES 时必须至少包含一个具体 issue。")
        return self
