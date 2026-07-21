from pydantic import BaseModel, Field


class ExploreReport(BaseModel):
    """Explore Agent 对仓库进行调查后生成的报告。"""

    focus: str = Field(
        description="本次探索的具体目标",
    )

    relevant_files: list[str] = Field(
        default_factory=list,
        description="与 Issue 相关的文件路径",
    )

    relevant_symbols: list[str] = Field(
        default_factory=list,
        description="相关类、函数、方法或变量",
    )

    findings: list[str] = Field(
        default_factory=list,
        description="基于代码证据得出的关键发现；每条包含仓库相对 path:line",
    )

    root_cause: str = Field(
        default="",
        description="引用仓库相对 path:line 的潜在根因；证据不足时明确说明",
    )

    test_targets: list[str] = Field(
        default_factory=list,
        description="经工具验证的现有测试文件或 pytest node ID",
    )

    unknowns: list[str] = Field(
        default_factory=list,
        description="当前无法确认、需要继续调查的问题",
    )
