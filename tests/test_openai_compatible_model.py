import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from services.openai_compatible_model import (
    NEED_REASONING_HISTORY,
    OpenAICompatibleChatModel,
    build_chat_model,
    build_non_thinking_model,
    detect_model_provider,
    resolve_reasoning_history,
)


TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取文件",
        "parameters": {"type": "object", "properties": {}},
    },
}


def make_model(
    *,
    provider: str = "deepseek",
    reasoning_history: bool = True,
) -> OpenAICompatibleChatModel:
    return OpenAICompatibleChatModel(
        model="test-model",
        api_key="test-key",
        base_url="https://example.com/v1",
        provider=provider,
        reasoning_history=reasoning_history,
        use_responses_api=False,
    )


@pytest.mark.parametrize(
    ("model_name", "base_url", "expected"),
    [
        ("deepseek-reasoner", "https://example.com/v1", "deepseek"),
        ("glm-4.5", "https://example.com/v1", "glm"),
        ("kimi-k2-thinking", "https://example.com/v1", "kimi"),
        ("mimo-v2-flash", "https://example.com/v1", "mimo"),
        ("qwen3-max", "https://example.com/v1", "qwen"),
        ("gemini-2.5-pro", "https://example.com/v1", "gemini"),
        ("gpt-5.2", "https://example.com/v1", "openai"),
        ("custom", "https://api.deepseek.com", "deepseek"),
        ("custom", "https://open.bigmodel.cn/api/paas/v4", "glm"),
        ("custom", "https://api.moonshot.cn/v1", "kimi"),
        ("custom", "https://api.xiaomimimo.com/v1", "mimo"),
        ("custom", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen"),
        ("custom", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
        ("custom", "https://api.openai.com/v1", "openai"),
        ("custom", "https://example.com/v1", "generic"),
    ],
)
def test_detect_model_provider(
    model_name: str,
    base_url: str,
    expected: str,
) -> None:
    assert detect_model_provider(model_name, base_url) == expected


def test_model_name_takes_precedence_over_shared_gateway_url() -> None:
    assert (
        detect_model_provider(
            "deepseek-r1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        == "deepseek"
    )


def test_default_reasoning_history_policy() -> None:
    assert NEED_REASONING_HISTORY == {
        "deepseek": True,
        "glm": True,
        "kimi": True,
        "mimo": True,
        "qwen": False,
        "openai": False,
        "gemini": False,
        "generic": False,
    }
    assert resolve_reasoning_history("deepseek", "auto") is True
    assert resolve_reasoning_history("openai", "auto") is False
    assert resolve_reasoning_history("deepseek", "false") is False
    assert resolve_reasoning_history("openai", "true") is True


def test_forced_tool_choice_is_omitted_for_incompatible_provider() -> None:
    bound_model = make_model().bind_tools([TOOL], tool_choice="required")

    assert "tool_choice" not in bound_model.kwargs


def test_standard_tool_choice_is_preserved_for_generic_provider() -> None:
    bound_model = make_model(provider="generic").bind_tools(
        [TOOL],
        tool_choice="required",
    )

    assert bound_model.kwargs["tool_choice"] == "required"


def test_response_reasoning_content_is_kept_in_internal_message() -> None:
    model = make_model()
    response = {
        "id": "chatcmpl-test",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "需要先读取目标文件。",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"app.py"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    result = model._create_chat_result(response)
    message = result.generations[0].message

    assert message.additional_kwargs["reasoning_content"] == (
        "需要先读取目标文件。"
    )
    assert message.response_metadata["model_provider"] == "deepseek"
    assert result.llm_output["model_provider"] == "deepseek"


def test_stream_reasoning_content_is_kept_in_internal_chunk() -> None:
    model = make_model(provider="glm")
    chunk = {
        "id": "chatcmpl-test",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "先分析。",
                },
                "finish_reason": None,
            }
        ],
    }

    result = model._convert_chunk_to_generation_chunk(
        chunk,
        AIMessageChunk,
        None,
    )

    assert result is not None
    assert result.message.additional_kwargs["reasoning_content"] == "先分析。"
    assert result.message.response_metadata["model_provider"] == "glm"


def test_reasoning_history_is_returned_with_assistant_tool_call() -> None:
    model = make_model()
    messages = [
        HumanMessage(content="读取文件"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "需要先读取目标文件。"},
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "app.py"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="文件内容", tool_call_id="call_1"),
    ]

    payload = model._get_request_payload(messages)

    assert "reasoning_content" not in payload["messages"][0]
    assert payload["messages"][1]["reasoning_content"] == (
        "需要先读取目标文件。"
    )
    assert payload["messages"][1]["content"] == ""
    assert "reasoning_content" not in payload["messages"][2]


def test_reasoning_history_is_not_returned_when_disabled() -> None:
    model = make_model(reasoning_history=False)
    message = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "内部推理"},
        tool_calls=[
            {
                "name": "read_file",
                "args": {},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )

    payload = model._get_request_payload([message])

    assert "reasoning_content" not in payload["messages"][0]


def test_qwen_explicit_reasoning_history_enables_preserve_thinking() -> None:
    model = build_chat_model(
        model="qwen3-max",
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        reasoning_history_mode="true",
    )

    assert model.provider == "qwen"
    assert model.reasoning_history is True
    assert model.extra_body == {"preserve_thinking": True}
    assert model.use_responses_api is False


def test_qwen_auto_does_not_enable_preserve_thinking() -> None:
    model = build_chat_model(
        model="qwen3-max",
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    assert model.reasoning_history is False
    assert model.extra_body is None


@pytest.mark.parametrize(
    (
        "model_name",
        "base_url",
        "expected_extra_body",
        "expected_reasoning_effort",
    ),
    [
        (
            "deepseek-v4-flash",
            "https://api.deepseek.com",
            {"thinking": {"type": "disabled"}},
            None,
        ),
        (
            "glm-4.5",
            "https://open.bigmodel.cn/api/paas/v4",
            {"thinking": {"type": "disabled"}},
            None,
        ),
        (
            "qwen3-max",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            {"enable_thinking": False},
            None,
        ),
        (
            "gemini-2.5-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            None,
            "none",
        ),
        (
            "gpt-5.2",
            "https://api.openai.com/v1",
            None,
            "none",
        ),
    ],
)
def test_build_non_thinking_model_uses_supported_provider_parameter(
    model_name: str,
    base_url: str,
    expected_extra_body: dict[str, object] | None,
    expected_reasoning_effort: str | None,
) -> None:
    model = build_chat_model(
        model=model_name,
        api_key="test-key",
        base_url=base_url,
        reasoning_history_mode="true",
    )

    disabled_model = build_non_thinking_model(model)

    assert isinstance(disabled_model, OpenAICompatibleChatModel)
    assert disabled_model is not model
    assert model.reasoning_history is True
    assert disabled_model.reasoning_history is False
    assert disabled_model.extra_body == expected_extra_body
    assert disabled_model.reasoning_effort == expected_reasoning_effort
    assert model.extra_body == (
        {"preserve_thinking": True}
        if model.provider == "qwen"
        else None
    )

    payload = disabled_model._get_request_payload(
        [HumanMessage(content="测试关闭思考")]
    )
    if expected_extra_body is None:
        assert "extra_body" not in payload
    else:
        assert payload["extra_body"] == expected_extra_body
    assert payload.get("reasoning_effort") == expected_reasoning_effort


@pytest.mark.parametrize(
    ("model_name", "base_url"),
    [
        ("deepseek-chat", "https://api.deepseek.com"),
        ("glm-4.4", "https://open.bigmodel.cn/api/paas/v4"),
        ("kimi-k2", "https://api.moonshot.cn/v1"),
        ("mimo-v2-flash", "https://api.xiaomimimo.com/v1"),
        ("qwq-32b", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        (
            "gemini-2.5-pro",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ),
        ("gpt-5-pro", "https://api.openai.com/v1"),
        ("custom", "https://example.com/v1"),
    ],
)
def test_build_non_thinking_model_keeps_unsupported_model_unchanged(
    model_name: str,
    base_url: str,
) -> None:
    model = build_chat_model(
        model=model_name,
        api_key="test-key",
        base_url=base_url,
        reasoning_history_mode="true",
    )

    assert build_non_thinking_model(model) is model
