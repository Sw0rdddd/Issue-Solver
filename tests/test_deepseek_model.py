from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.deepseek_model import ReasoningChatDeepSeek


def make_model() -> ReasoningChatDeepSeek:
    return ReasoningChatDeepSeek(
        model="deepseek-v4-flash",
        api_key="test-key",
        base_url="https://api.deepseek.com",
    )


def test_tool_choice_is_omitted_for_thinking_mode() -> None:
    model = make_model()
    tool = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    bound_model = model.bind_tools([tool], tool_choice="required")

    assert "tool_choice" not in bound_model.kwargs


def test_reasoning_content_is_returned_with_assistant_tool_call() -> None:
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
