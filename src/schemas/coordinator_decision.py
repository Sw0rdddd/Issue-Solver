from pydantic import BaseModel, Field, model_validator

from graph.state import NextAction
from schemas.coding_task import CodingTask
from schemas.evidence_digest import EvidenceDigest
from schemas.failure import FailureInfo


class CoordinatorDecision(BaseModel):
    """Coordinator 对工作流下一步的结构化决策。"""

    next_action: NextAction = Field(
        description=(
            "下一步动作：EXPLORE 用于补充证据，CODE 用于执行最小修改，"
            "FINISH 仅限 Review 批准且本轮测试全通过，FAILED 用于无法安全继续。"
        )
    )
    current_summary: str = Field(
        min_length=1,
        max_length=2000,
        description="仅概括根因、已知结果和选择下一步的理由，不累积完整历史。",
    )
    explore_focuses: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "仅 EXPLORE 时填写的 1 至 3 个互不重复的调查目标；"
            "必须依据 RepositoryProfile 和独立证据缺口选择最少必要数量，"
            "小型或单一调查面默认一个；"
            "其他动作必须为空列表。"
        ),
    )
    explore_titles: list[str] = Field(
        default_factory=list,
        description=(
            "仅 EXPLORE 时填写、与 explore_focuses 同位置对应的简洁展示标题；"
            "其他动作为空列表。"
        ),
    )
    coding_task: CodingTask | None = Field(
        default=None,
        description="仅 CODE 时提供的完整、受限修改任务；其他动作必须为 null。",
    )
    evidence_digest: EvidenceDigest | None = Field(
        default=None,
        description=(
            "存在本次新 ExploreReport 时必须提供的累计证据摘要；"
            "无新报告时必须为 null。"
        ),
    )
    failure: FailureInfo | None = Field(
        default=None,
        description="仅 FAILED 时提供的结构化失败事实与下一步建议；其他动作必须为 null。",
    )

    @model_validator(mode="after")
    def validate_action_payload(self) -> "CoordinatorDecision":
        if any(not focus.strip() for focus in self.explore_focuses):
            raise ValueError("Explore 目标不能为空字符串。")

        if self.next_action == "EXPLORE":
            if self.failure is not None:
                raise ValueError("EXPLORE 决策不能包含 failure。")
            if not self.explore_focuses:
                raise ValueError("EXPLORE 决策必须包含 Explore 目标。")
            if self.coding_task is not None:
                raise ValueError("EXPLORE 决策不能包含 CodingTask。")
        elif self.next_action == "CODE":
            if self.failure is not None:
                raise ValueError("CODE 决策不能包含 failure。")
            if self.explore_focuses:
                raise ValueError("CODE 决策不能包含 Explore 目标。")
            if self.coding_task is None:
                raise ValueError("CODE 决策必须包含 CodingTask。")
        elif self.next_action == "FINISH":
            if self.failure is not None:
                raise ValueError("FINISH 决策不能包含 failure。")
            if (
                self.explore_focuses
                or self.coding_task is not None
            ):
                raise ValueError("FINISH 决策不能包含执行任务。")
        else:
            if self.failure is None:
                raise ValueError("FAILED 决策必须包含 failure。")
            if (
                self.explore_focuses
                or self.coding_task is not None
            ):
                raise ValueError("FAILED 决策不能包含执行任务。")

        return self
