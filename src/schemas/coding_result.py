from pydantic import BaseModel, Field


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
