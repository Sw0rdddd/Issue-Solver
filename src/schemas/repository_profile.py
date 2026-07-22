from pydantic import BaseModel, ConfigDict, Field


class RepositoryProfile(BaseModel):
    """目标 Git 仓库的只读规模画像。"""

    model_config = ConfigDict(extra="forbid")

    tracked_file_count: int = Field(
        ge=0,
        description="Git 跟踪的常规文件数量",
    )
    tracked_file_bytes: int = Field(
        ge=0,
        description="Git 跟踪的常规文件总字节数",
    )
    file_counts_by_extension: dict[str, int] = Field(
        default_factory=dict,
        description="按文件扩展名聚合的 Git 跟踪常规文件数量；无扩展名使用 <none>",
    )
