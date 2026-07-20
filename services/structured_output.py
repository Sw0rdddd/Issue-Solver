from typing import TypeVar

from langchain_core.runnables import Runnable


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")

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
