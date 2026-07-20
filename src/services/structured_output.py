from typing import Any, TypeVar

from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
StructuredT = TypeVar("StructuredT", bound=BaseModel)

STRUCTURED_OUTPUT_MAX_ATTEMPTS = 3


def with_structured_output_retry(
    runnable: Runnable[InputT, OutputT],
) -> Runnable[InputT, OutputT]:
    """为结构化解析错误增加固定次数的即时重试。"""

    return runnable.with_retry(
        retry_if_exception_type=(ValueError,),
        wait_exponential_jitter=False,
        stop_after_attempt=STRUCTURED_OUTPUT_MAX_ATTEMPTS,
    )


def with_agent_structured_output_retry(
    runnable: Runnable[InputT, dict[str, Any]],
    response_type: type[StructuredT],
    *,
    agent_name: str,
) -> Runnable[InputT, dict[str, Any]]:
    """校验 Agent 的结构化响应，并在缺失或类型错误时重试。"""

    def validate(response: dict[str, Any]) -> dict[str, Any]:
        candidate = (
            response.get("structured_response")
            if isinstance(response, dict)
            else None
        )
        if not isinstance(candidate, response_type):
            raise ValueError(
                f"{agent_name} 连续 {STRUCTURED_OUTPUT_MAX_ATTEMPTS} 次"
                f"未返回有效的 {response_type.__name__}。"
            )
        return response

    return with_structured_output_retry(runnable | RunnableLambda(validate))
