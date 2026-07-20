from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


class ReasoningChatDeepSeek(ChatDeepSeek):
    """在多轮工具调用中回填 DeepSeek 的推理内容。"""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: dict | str | bool | None = None,
        **kwargs: Any,
    ):
        """绑定工具但不发送 thinking 模式不支持的 tool_choice。"""

        return super().bind_tools(tools, tool_choice=None, **kwargs)

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(
            input_,
            stop=stop,
            **kwargs,
        )

        for message, message_payload in zip(
            messages,
            payload["messages"],
            strict=True,
        ):
            if isinstance(message, AIMessage):
                if "reasoning_content" in message.additional_kwargs:
                    message_payload["reasoning_content"] = (
                        message.additional_kwargs["reasoning_content"]
                    )
                if message.tool_calls and message_payload.get("content") is None:
                    message_payload["content"] = (
                        message.content
                        if isinstance(message.content, str)
                        else ""
                    )

        return payload
