from pydantic import BaseModel,Field

class IssueSpec(BaseModel):
    """经过规范化后的 Issue 信息。"""

    title: str = Field(description="Issue 的简短标题")
    body: str = Field(description="Issue 的原始问题描述")
    expected_behavior: str = Field(
        default="",
        description="期望的程序行为，原文未提供时为空字符串",
    )
    actual_behavior: str = Field(
        default="",
        description="当前实际行为，原文未提供时为空字符串",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="判断 Issue 是否修复成功的验收条件",
    )