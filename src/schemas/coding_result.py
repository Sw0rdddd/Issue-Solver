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
    changed_files: list[str] = Field(
        description="实际修改且已由 inspect_changes 确认的仓库相对文件列表。"
    )
    summary: str = Field(
        min_length=1,
        description="对已完成修改及其直接原因的简要事实说明。",
    )
    diff_path: str | None = Field(
        default=None,
        description="最终 Git Patch 路径；Coding 阶段必须为 null",
    )
    validation: list[str] = Field(
        description="本次实际执行过的读取、写入和累计 Diff 检查。"
    )
    remaining_risks: list[str] = Field(
        description="即使修改成功仍未确认或可能影响方案的问题。"
    )
    failure: FailureInfo | None = Field(
        default=None,
        description="仅 success=false 时提供的结构化失败事实与下一步建议。",
    )

    @model_validator(mode="after")
    def validate_failure_matches_success(self) -> "CodingResult":
        if self.success and self.failure is not None:
            raise ValueError("成功的 CodingResult 不能包含 failure。")
        if not self.success and self.failure is None:
            raise ValueError("失败的 CodingResult 必须包含 failure。")
        return self
