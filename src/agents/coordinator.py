from langchain_core.language_models import BaseChatModel

from schemas.coordinator_decision import CoordinatorDecision
from services.structured_output import with_structured_output_retry


def build_coordinator_agent(model: BaseChatModel):
    """创建无工具、只返回结构化决策的 Coordinator Agent。"""

    return with_structured_output_retry(
        model.with_structured_output(
            CoordinatorDecision,
            method="function_calling",
            strict=None,
        )
    )
