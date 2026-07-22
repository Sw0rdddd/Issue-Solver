"""按工作流角色汇总模型 Token 用量。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable


TokenRole = Literal[
    "Parser",
    "Coordinator",
    "Explorer",
    "Coder",
    "Reviewer",
    "Reporter",
]
TOKEN_ROLES: tuple[TokenRole, ...] = (
    "Parser",
    "Coordinator",
    "Explorer",
    "Coder",
    "Reviewer",
    "Reporter",
)


@dataclass(frozen=True)
class RoleTokenUsage:
    """单个角色的累计 Token 与在本次运行中的占比。"""

    role: TokenRole
    total_tokens: int
    percentage: float


@dataclass(frozen=True)
class TokenUsageSummary:
    """可写入最终报告与终端摘要的 Token 汇总。"""

    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None
    role_usages: tuple[RoleTokenUsage, ...]


@dataclass
class _MutableRoleUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    cache_read_available: bool = True


def _token_count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _cache_read_tokens(details: Mapping[str, Any] | None) -> int | None:
    """读取 LangChain 标准字段及带服务等级前缀的缓存读取 Token。"""

    if details is None:
        return None
    cache_keys = [
        key
        for key in details
        if key == "cache_read" or key.endswith("_cache_read")
    ]
    if not cache_keys:
        return None
    return sum(_token_count(details[key]) for key in cache_keys)


class _RoleTokenUsageCallback(BaseCallbackHandler):
    """将单个绑定模型的所有调用归集到指定角色。"""

    def __init__(self, monitor: TokenUsageMonitor, role: TokenRole) -> None:
        self.monitor = monitor
        self.role: TokenRole = role

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if isinstance(message, AIMessage):
                    self.monitor.record_usage(
                        self.role,
                        message.usage_metadata,
                    )


class TokenUsageMonitor:
    """为各 Agent 绑定回调，并安全汇总其模型调用用量。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._usage = {role: _MutableRoleUsage() for role in TOKEN_ROLES}

    def with_role(
        self,
        runnable: Runnable,
        role: TokenRole,
    ) -> Runnable:
        """返回附带角色级用量回调的最终可执行对象。"""

        return runnable.with_config(
            {"callbacks": [_RoleTokenUsageCallback(self, role)]}
        )

    def record_usage(
        self,
        role: TokenRole,
        usage_metadata: Mapping[str, Any] | None,
    ) -> None:
        """记录一次模型响应；供回调及单元测试共用。"""

        with self._lock:
            usage = self._usage[role]
            usage.calls += 1
            if usage_metadata is None:
                usage.cache_read_available = False
                return

            usage.input_tokens += _token_count(
                usage_metadata.get("input_tokens")
            )
            usage.output_tokens += _token_count(
                usage_metadata.get("output_tokens")
            )
            usage.total_tokens += _token_count(
                usage_metadata.get("total_tokens")
            )
            details = usage_metadata.get("input_token_details")
            cache_read = _cache_read_tokens(
                details if isinstance(details, Mapping) else None
            )
            if cache_read is None:
                usage.cache_read_available = False
            else:
                usage.cache_read_tokens += cache_read

    def summary(self) -> TokenUsageSummary:
        """生成固定角色顺序的当前快照。"""

        with self._lock:
            usages = {
                role: _MutableRoleUsage(**vars(value))
                for role, value in self._usage.items()
            }

        total_tokens = sum(value.total_tokens for value in usages.values())
        cache_read_tokens = (
            sum(
                value.cache_read_tokens
                for value in usages.values()
                if value.calls
            )
            if all(
                value.cache_read_available
                for value in usages.values()
                if value.calls
            )
            else None
        )
        role_usages = tuple(
            RoleTokenUsage(
                role=role,
                total_tokens=usages[role].total_tokens,
                percentage=(
                    usages[role].total_tokens / total_tokens * 100
                    if total_tokens
                    else 0.0
                ),
            )
            for role in TOKEN_ROLES
        )
        return TokenUsageSummary(
            total_tokens=total_tokens,
            input_tokens=sum(value.input_tokens for value in usages.values()),
            output_tokens=sum(value.output_tokens for value in usages.values()),
            cache_read_tokens=cache_read_tokens,
            role_usages=role_usages,
        )
