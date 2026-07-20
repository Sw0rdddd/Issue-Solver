from pydantic import BaseModel, Field, model_validator

from graph.state import NextAction
from schemas.coding_task import CodingTask


class CoordinatorDecision(BaseModel):
    """Coordinator 对工作流下一步的结构化决策。"""

    next_action: NextAction
    current_summary: str = Field(min_length=1, max_length=2000)
    explore_focuses: list[str] = Field(
        default_factory=list,
        max_length=3,
    )
    coding_task: CodingTask | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "CoordinatorDecision":
        if any(not focus.strip() for focus in self.explore_focuses):
            raise ValueError("Explore 目标不能为空字符串。")

        if self.next_action == "EXPLORE":
            if not self.explore_focuses:
                raise ValueError("EXPLORE 决策必须包含 Explore 目标。")
            if self.coding_task is not None:
                raise ValueError("EXPLORE 决策不能包含 CodingTask。")
        elif self.next_action == "CODE":
            if self.explore_focuses:
                raise ValueError("CODE 决策不能包含 Explore 目标。")
            if self.coding_task is None:
                raise ValueError("CODE 决策必须包含 CodingTask。")
        elif self.explore_focuses or self.coding_task is not None:
            raise ValueError(
                "FINISH 或 FAILED 决策不能包含执行任务。"
            )

        return self
