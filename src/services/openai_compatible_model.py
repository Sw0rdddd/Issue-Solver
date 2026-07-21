from collections.abc import Callable, Sequence
from typing import Any, Literal

import openai
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr


ModelProvider = Literal[
    "deepseek",
    "glm",
    "kimi",
    "mimo",
    "qwen",
    "openai",
    "gemini",
    "generic",
]
ReasoningHistoryMode = Literal["auto", "true", "false"]


NEED_REASONING_HISTORY: dict[ModelProvider, bool] = {
    "deepseek": True,
    "glm": True,
    "kimi": True,
    "mimo": True,
    "qwen": False,
    "openai": False,
    "gemini": False,
    "generic": False,
}

_MODEL_MARKERS: tuple[tuple[ModelProvider, tuple[str, ...]], ...] = (
    ("deepseek", ("deepseek",)),
    ("glm", ("glm-", "chatglm")),
    ("kimi", ("kimi", "moonshot")),
    ("mimo", ("mimo",)),
    ("qwen", ("qwen", "qwq")),
    ("gemini", ("gemini",)),
)
_BASE_URL_MARKERS: tuple[tuple[ModelProvider, tuple[str, ...]], ...] = (
    ("deepseek", ("deepseek.com",)),
    ("glm", ("bigmodel.cn", "zhipuai",)),
    ("kimi", ("moonshot.cn", "kimi.com",)),
    ("mimo", ("mimo.mi.com", "xiaomimimo",)),
    ("qwen", ("dashscope", "aliyuncs.com",)),
    ("gemini", ("generativelanguage.googleapis.com", "gemini",)),
    ("openai", ("api.openai.com",)),
)
_OPENAI_MODEL_PREFIXES = (
    "gpt-",
    "chatgpt-",
    "o1",
    "o3",
    "o4",
    "codex-",
)
_FORCED_TOOL_CHOICE_UNSUPPORTED: frozenset[ModelProvider] = frozenset(
    {"deepseek", "kimi", "mimo", "qwen"}
)


def detect_model_provider(model_name: str, base_url: str) -> ModelProvider:
    """优先根据模型名、其次根据 API 地址推断供应商。"""

    normalized_model = model_name.strip().lower()
    for provider, markers in _MODEL_MARKERS:
        if any(marker in normalized_model for marker in markers):
            return provider
    if normalized_model.startswith(_OPENAI_MODEL_PREFIXES):
        return "openai"

    normalized_url = base_url.strip().lower()
    for provider, markers in _BASE_URL_MARKERS:
        if any(marker in normalized_url for marker in markers):
            return provider
    return "generic"


def resolve_reasoning_history(
    provider: ModelProvider,
    mode: ReasoningHistoryMode,
) -> bool:
    """将自动或显式配置解析为是否回填推理历史。"""

    if mode == "true":
        return True
    if mode == "false":
        return False
    return NEED_REASONING_HISTORY[provider]


def _reasoning_from_message(message: Any) -> Any | None:
    if isinstance(message, dict):
        for name in ("reasoning_content", "reasoning"):
            if message.get(name) is not None:
                return message[name]
        model_extra = message.get("model_extra")
    else:
        for name in ("reasoning_content", "reasoning"):
            value = getattr(message, name, None)
            if value is not None:
                return value
        model_extra = getattr(message, "model_extra", None)

    if isinstance(model_extra, dict):
        for name in ("reasoning_content", "reasoning"):
            if model_extra.get(name) is not None:
                return model_extra[name]
    return None


class OpenAICompatibleChatModel(ChatOpenAI):
    """兼容主流 OpenAI Chat Completions 服务的模型适配器。"""

    provider: ModelProvider = "generic"
    reasoning_history: bool = False

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: dict | str | bool | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """已知不支持强制选工具的服务改用供应商默认策略。"""

        if (
            self.provider in _FORCED_TOOL_CHOICE_UNSUPPORTED
            and tool_choice not in (None, False, "auto", "none")
        ):
            tool_choice = None
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if not self.reasoning_history:
            return payload

        for message, message_payload in zip(
            messages,
            payload["messages"],
            strict=True,
        ):
            if not isinstance(message, AIMessage) or not message_payload.get(
                "tool_calls"
            ):
                continue
            reasoning_content = message.additional_kwargs.get(
                "reasoning_content"
            )
            if reasoning_content is not None:
                message_payload["reasoning_content"] = reasoning_content
            if message_payload.get("content") is None:
                message_payload["content"] = (
                    message.content if isinstance(message.content, str) else ""
                )
        return payload

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        choices = (
            response.get("choices", [])
            if isinstance(response, dict)
            else getattr(response, "choices", [])
        )
        for generation, choice in zip(result.generations, choices, strict=False):
            raw_message = (
                choice.get("message", {})
                if isinstance(choice, dict)
                else getattr(choice, "message", None)
            )
            reasoning_content = _reasoning_from_message(raw_message)
            if reasoning_content is not None:
                generation.message.additional_kwargs["reasoning_content"] = (
                    reasoning_content
                )
            generation.message.response_metadata["model_provider"] = self.provider
        if result.llm_output is not None:
            result.llm_output["model_provider"] = self.provider
        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        choices = chunk.get("choices")
        if not choices or generation_chunk is None:
            return generation_chunk

        if isinstance(generation_chunk.message, AIMessageChunk):
            delta = choices[0].get("delta") or {}
            reasoning_content = _reasoning_from_message(delta)
            if reasoning_content is not None:
                generation_chunk.message.additional_kwargs[
                    "reasoning_content"
                ] = reasoning_content
            generation_chunk.message.response_metadata["model_provider"] = (
                self.provider
            )
        return generation_chunk


def build_chat_model(
    *,
    model: str,
    api_key: str,
    base_url: str,
    reasoning_history_mode: ReasoningHistoryMode = "auto",
) -> OpenAICompatibleChatModel:
    """根据 OpenAI 兼容配置创建模型，并应用供应商差异。"""

    provider = detect_model_provider(model, base_url)
    reasoning_history = resolve_reasoning_history(
        provider,
        reasoning_history_mode,
    )
    extra_body = (
        {"preserve_thinking": True}
        if provider == "qwen" and reasoning_history
        else None
    )
    return OpenAICompatibleChatModel(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
        provider=provider,
        reasoning_history=reasoning_history,
        extra_body=extra_body,
        use_responses_api=False,
    )
