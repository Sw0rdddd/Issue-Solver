from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


FailureType = Literal[
    "INPUT",
    "ENVIRONMENT",
    "MODEL",
    "SOLUTION",
    "SAFETY",
    "LIMIT",
    "INTERNAL",
]

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]

DEFAULT_SUGGESTIONS: dict[FailureType, str] = {
    "INPUT": "修正输入后重试。",
    "ENVIRONMENT": "修复运行环境或外部依赖后重试。",
    "MODEL": "检查模型服务和结构化输出后重试。",
    "SOLUTION": "根据失败证据调整修复方案。",
    "SAFETY": "检查工作区和路径边界，确认安全后重试。",
    "LIMIT": "检查当前证据并调整执行限制或缩小任务范围。",
    "INTERNAL": "检查运行日志；若问题可复现，请修复工作流实现。",
}


class FailureInfo(BaseModel):
    """面向开发者和 Agent 的统一失败信息。"""

    model_config = ConfigDict(extra="forbid")

    type: FailureType = Field(
        description="失败类别，用于决定输入、环境、模型、方案、安全、限制或内部错误的后续处理。"
    )
    message: NonEmptyText = Field(
        description="基于当前证据的具体失败事实，不包含角色指令或未验证推测。"
    )
    suggestion: NonEmptyText = Field(
        description="开发者或后续流程可执行的下一步建议。"
    )


def make_failure(
    failure_type: FailureType,
    message: str,
    suggestion: str | None = None,
) -> FailureInfo:
    return FailureInfo(
        type=failure_type,
        message=message,
        suggestion=suggestion or DEFAULT_SUGGESTIONS[failure_type],
    )


def format_failure_for_agent(failure: FailureInfo) -> str:
    """为仍以字符串返回内容的只读工具生成稳定错误格式。"""

    return (
        f"错误类型：{failure.type}\n"
        f"原因：{failure.message}\n"
        f"建议：{failure.suggestion}"
    )


class ClassifiedFailure(ValueError):
    """在内部调用栈中携带已分类失败，不建立异常子类体系。"""

    def __init__(self, failure: FailureInfo) -> None:
        super().__init__(failure.message)
        self.failure = failure


def failure_from_exception(
    exc: Exception,
    fallback_type: FailureType,
    *,
    prefix: str = "",
    suggestion: str | None = None,
) -> FailureInfo:
    if isinstance(exc, ClassifiedFailure):
        failure = exc.failure
        if not prefix:
            return failure
        return failure.model_copy(
            update={"message": f"{prefix}{failure.message}"}
        )
    return make_failure(
        fallback_type,
        f"{prefix}{exc}",
        suggestion,
    )
