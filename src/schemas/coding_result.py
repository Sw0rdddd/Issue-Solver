from pydantic import BaseModel, Field, model_validator

from schemas.failure import FailureInfo


class CodingResult(BaseModel):
    """Coding Agent 完成修改后返回的结构化结果"""

    success: bool = Field(
        strict=True,
        description=(
            "Coding Agent 是否完成允许范围内的修改和 Diff 自检；"
            "不代表 Review 或测试通过"
        ),
    )
    changed_files: list[str] = Field(description="实际修改的文件列表")
    summary: str = Field(min_length=1, description="修改内容的简要说明")
    diff_path: str | None = Field(
        default=None,
        description="最终 Git Patch 路径；Coding 阶段必须为 null",
    )
    validation: list[str] = Field(description="已执行的读取、修改和差异检查")
    remaining_risks: list[str] = Field(description="仍未确认或可能存在的问题")
    failure: FailureInfo | None = None

    @model_validator(mode="after")
    def validate_failure_matches_success(self) -> "CodingResult":
        if self.success and self.failure is not None:
            raise ValueError("成功的 CodingResult 不能包含 failure。")
        if not self.success and self.failure is None:
            raise ValueError("失败的 CodingResult 必须包含 failure。")
        return self
