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
        description="基于代码证据得出的关键发现",
    )

    root_cause: str = Field(
        default="",
        description="潜在根因；证据不足时明确说明",
    )

    test_targets: list[str] = Field(
        default_factory=list,
        description="需要验证或增加测试的位置和场景",
    )

    unknowns: list[str] = Field(
        default_factory=list,
        description="当前无法确认、需要继续调查的问题",
    )