import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from services.structured_output import (
    with_agent_structured_output_retry,
    with_structured_output_retry,
)


class ExampleResponse(BaseModel):
    value: str


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


def test_agent_structured_output_retries_missing_response() -> None:
    attempts = 0
    expected = ExampleResponse(value="成功")

    def invoke(_: str) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return {}
        return {"structured_response": expected}

    runnable = with_agent_structured_output_retry(
        RunnableLambda(invoke),
        ExampleResponse,
        agent_name="Example Agent",
    )

    assert runnable.invoke("输入") == {"structured_response": expected}
    assert attempts == 3


def test_agent_structured_output_accepts_first_valid_response() -> None:
    attempts = 0
    expected = ExampleResponse(value="成功")

    def invoke(_: str) -> dict:
        nonlocal attempts
        attempts += 1
        return {"structured_response": expected}

    runnable = with_agent_structured_output_retry(
        RunnableLambda(invoke),
        ExampleResponse,
        agent_name="Example Agent",
    )

    assert runnable.invoke("输入") == {"structured_response": expected}
    assert attempts == 1


def test_agent_structured_output_stops_after_three_invalid_responses() -> None:
    attempts = 0

    def invoke(_: str) -> dict:
        nonlocal attempts
        attempts += 1
        return {"structured_response": object()}

    runnable = with_agent_structured_output_retry(
        RunnableLambda(invoke),
        ExampleResponse,
        agent_name="Example Agent",
    )

    with pytest.raises(
        ValueError,
        match="Example Agent 连续 3 次未返回有效的 ExampleResponse",
    ):
        runnable.invoke("输入")

    assert attempts == 3


def test_agent_structured_output_does_not_retry_runtime_errors() -> None:
    attempts = 0

    def invoke(_: str) -> dict:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("模型不可用")

    runnable = with_agent_structured_output_retry(
        RunnableLambda(invoke),
        ExampleResponse,
        agent_name="Example Agent",
    )

    with pytest.raises(RuntimeError, match="模型不可用"):
        runnable.invoke("输入")

    assert attempts == 1
