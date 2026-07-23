from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.graph.message import RemoveMessage


def _completed_tool_batches(
    messages: Sequence[AnyMessage],
) -> list[list[AnyMessage]]:
    """返回已完成且连续的工具调用消息批次。"""

    batches: list[list[AnyMessage]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue

        tool_call_ids = {
            tool_call.get("id") for tool_call in message.tool_calls
        }
        if None in tool_call_ids:
            continue

        tool_messages: list[ToolMessage] = []
        next_index = index + 1
        while next_index < len(messages):
            next_message = messages[next_index]
            if not isinstance(next_message, ToolMessage):
                break
            tool_messages.append(next_message)
            next_index += 1

        returned_ids = {
            tool_message.tool_call_id for tool_message in tool_messages
        }
        if tool_call_ids == returned_ids:
            batches.append([message, *tool_messages])

    return batches


class ToolHistoryWindowMiddleware(AgentMiddleware):
    """仅清除超出窗口的已完成工具调用批次，保留系统提示词和上游任务。"""

    def __init__(self, recursion_limit: int) -> None:
        """保留约四分之三的工具调用预算，且不截断完整批次。"""

        self.retained_batches = max(1, (recursion_limit * 3 + 3) // 4)

    def before_model(
        self,
        state: AgentState[Any],
        runtime: Any,
    ) -> dict[str, Any] | None:
        batches = _completed_tool_batches(state["messages"])
        expired_batches = batches[: -self.retained_batches]
        if not expired_batches:
            return None

        removable_batches = [
            batch
            for batch in expired_batches
            if all(message.id is not None for message in batch)
        ]
        if not removable_batches:
            return None

        removals: list[RemoveMessage] = []
        for batch in removable_batches:
            for message in batch:
                message_id = message.id
                if message_id is not None:
                    removals.append(RemoveMessage(id=message_id))
        return {"messages": removals}
