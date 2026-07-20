import pytest
from langchain_core.runnables import RunnableLambda

from services.structured_output import with_structured_output_retry


def test_structured_output_retries_until_third_attempt() -> None:
    attempts = 0

    def invoke(value: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("结构化输出无效")
        return value

    runnable = with_structured_output_retry(RunnableLambda(invoke))

    assert runnable.invoke("成功") == "成功"
    assert attempts == 3


def test_structured_output_stops_after_three_attempts() -> None:
    attempts = 0

    def invoke(value: str) -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError(f"第 {attempts} 次失败")

    runnable = with_structured_output_retry(RunnableLambda(invoke))

    with pytest.raises(ValueError, match="第 3 次失败"):
        runnable.invoke("失败")

    assert attempts == 3


def test_structured_output_does_not_retry_runtime_errors() -> None:
    attempts = 0

    def invoke(value: str) -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("模型调用失败")

    runnable = with_structured_output_retry(RunnableLambda(invoke))

    with pytest.raises(RuntimeError, match="模型调用失败"):
        runnable.invoke("失败")

    assert attempts == 1
