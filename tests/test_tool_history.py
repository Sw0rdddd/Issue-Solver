from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import RemoveMessage

from services.tool_history import ToolHistoryWindowMiddleware


def _tool_batch(index: int, call_count: int = 1) -> list[object]:
    tool_calls = [
        {
            "name": "read_file",
            "args": {"path": f"file_{index}_{call}.py"},
            "id": f"call_{index}_{call}",
            "type": "tool_call",
        }
        for call in range(call_count)
    ]
    return [
        AIMessage(id=f"ai_{index}", content="", tool_calls=tool_calls),
        *[
            ToolMessage(
                id=f"tool_{index}_{call}",
                content=f"内容 {index}-{call}",
                tool_call_id=f"call_{index}_{call}",
            )
            for call in range(call_count)
        ],
    ]


def _removed_ids(update: dict[str, object] | None) -> set[str]:
    assert update is not None
    return {
        message.id
        for message in update["messages"]
        if isinstance(message, RemoveMessage)
    }


def test_tool_history_window_uses_a_quarter_of_recursion_limit() -> None:
    assert ToolHistoryWindowMiddleware(60).retained_batches == 15
    assert ToolHistoryWindowMiddleware(20).retained_batches == 5
    assert ToolHistoryWindowMiddleware(3).retained_batches == 1


def test_tool_history_removes_only_expired_complete_batches() -> None:
    middleware = ToolHistoryWindowMiddleware(8)
    messages = [
        HumanMessage(id="task", content="修复问题"),
        *_tool_batch(1),
        *_tool_batch(2),
        *_tool_batch(3),
    ]

    update = middleware.before_model({"messages": messages}, None)

    assert _removed_ids(update) == {"ai_1", "tool_1_0"}


def test_tool_history_keeps_all_messages_in_the_latest_parallel_batch() -> None:
    middleware = ToolHistoryWindowMiddleware(4)
    messages = [
        HumanMessage(id="task", content="修复问题"),
        *_tool_batch(1, call_count=2),
        *_tool_batch(2, call_count=2),
    ]

    update = middleware.before_model({"messages": messages}, None)

    assert _removed_ids(update) == {"ai_1", "tool_1_0", "tool_1_1"}


def test_tool_history_preserves_incomplete_tool_batches() -> None:
    middleware = ToolHistoryWindowMiddleware(4)
    incomplete = [
        AIMessage(
            id="ai_incomplete",
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "missing.py"},
                    "id": "call_incomplete",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            id="tool_other",
            content="错误调用",
            tool_call_id="other_call",
        ),
    ]
    messages = [
        HumanMessage(id="task", content="修复问题"),
        *incomplete,
        *_tool_batch(1),
        *_tool_batch(2),
    ]

    update = middleware.before_model({"messages": messages}, None)

    assert _removed_ids(update) == {"ai_1", "tool_1_0"}


def test_tool_history_preserves_a_batch_without_message_ids() -> None:
    middleware = ToolHistoryWindowMiddleware(4)
    batch_without_ids = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "legacy.py"},
                    "id": "call_legacy",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="旧内容", tool_call_id="call_legacy"),
    ]
    messages = [
        HumanMessage(id="task", content="修复问题"),
        *batch_without_ids,
        *_tool_batch(1),
        *_tool_batch(2),
    ]

    update = middleware.before_model({"messages": messages}, None)

    assert _removed_ids(update) == {"ai_1", "tool_1_0"}


def test_tool_history_leaves_messages_without_expired_batches_unchanged() -> None:
    middleware = ToolHistoryWindowMiddleware(4)

    assert (
        middleware.before_model(
            {"messages": [HumanMessage(id="task", content="修复问题")]},
            None,
        )
        is None
    )
