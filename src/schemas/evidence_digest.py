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
        description="累计合并到本摘要的 ExploreReport 精确数量，包含已有摘要和本次新增报告。",
    )
    root_cause: str = Field(
        default="",
        max_length=600,
        description="当前最可信的根因及其证据结论；证据不足时为空字符串。",
    )
    key_evidence: list[DigestText] = Field(
        default_factory=list,
        max_length=8,
        description="最关键的仓库相对 path:line 证据、冲突或已确认结论。",
    )
    relevant_files: list[DigestText] = Field(
        default_factory=list,
        max_length=12,
        description="后续探索或编码最相关的仓库相对文件，不含无关路径。",
    )
    relevant_symbols: list[DigestText] = Field(
        default_factory=list,
        max_length=12,
        description="后续探索或编码最相关的类、函数或方法。",
    )
    test_targets: list[DigestText] = Field(
        default_factory=list,
        max_length=10,
        description="已经过探索证据确认的既有仓库相对测试文件或 pytest node ID。",
    )
    unknowns: list[DigestText] = Field(
        default_factory=list,
        max_length=5,
        description="仍需探索或当前无法由证据确认的问题，不得把猜测写成结论。",
    )
