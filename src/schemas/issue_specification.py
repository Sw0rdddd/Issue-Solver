from pydantic import BaseModel, Field


class IssueSpec(BaseModel):
    """经过规范化后的 Issue 信息。"""

    title: str = Field(description="忠实概括原始 Issue 的简短标题。")
    body: str = Field(description="用于审计的原始 Issue 问题描述，不加入推测。")
    expected_behavior: str = Field(
        default="",
        description="从原始 Issue 提取的期望行为；原文未提供时为空字符串。",
    )
    actual_behavior: str = Field(
        default="",
        description="从原始 Issue 提取的当前实际行为；原文未提供时为空字符串。",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="从原始 Issue 明示内容或直接可推导预期得到的最小可验证验收条件。",
    )
