from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


DigestText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=400),
]


class EvidenceDigest(BaseModel):
    """Coordinator 从 ExploreReport 提炼的有界证据摘要。"""

    model_config = ConfigDict(extra="forbid")

    source_report_count: int = Field(
        ge=1,
        description="已合并到本摘要的 ExploreReport 数量",
    )
    root_cause: str = Field(
        default="",
        max_length=600,
        description="当前最可信的根因；证据不足时为空字符串",
    )
    key_evidence: list[DigestText] = Field(
        default_factory=list,
        max_length=8,
        description="最关键的 path:line 证据、冲突或结论",
    )
    relevant_files: list[DigestText] = Field(
        default_factory=list,
        max_length=12,
        description="后续处理最相关的仓库相对文件",
    )
    relevant_symbols: list[DigestText] = Field(
        default_factory=list,
        max_length=12,
        description="后续处理最相关的符号",
    )
    test_targets: list[DigestText] = Field(
        default_factory=list,
        max_length=10,
        description="已经过探索证据确认的测试目标",
    )
    unknowns: list[DigestText] = Field(
        default_factory=list,
        max_length=5,
        description="仍需探索或无法确认的问题",
    )
