from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult

from services.token_usage import TOKEN_ROLES, TokenUsageMonitor


class UsageModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "usage-test"

    def _generate(
        self,
        messages: list[object],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="ok",
                        usage_metadata={
                            "input_tokens": 3,
                            "output_tokens": 2,
                            "total_tokens": 5,
                            "input_token_details": {"cache_read": 1},
                        },
                    )
                )
            ]
        )


def test_monitor_aggregates_role_totals_and_cache_reads() -> None:
    monitor = TokenUsageMonitor()
    monitor.record_usage(
        "Parser",
        {
            "input_tokens": 20,
            "output_tokens": 5,
            "total_tokens": 25,
            "input_token_details": {"cache_read": 4},
        },
    )
    monitor.record_usage(
        "Explorer",
        {
            "input_tokens": 60,
            "output_tokens": 15,
            "total_tokens": 75,
            "input_token_details": {"priority_cache_read": 12},
        },
    )

    summary = monitor.summary()

    assert summary.total_tokens == 100
    assert summary.input_tokens == 80
    assert summary.output_tokens == 20
    assert summary.cache_read_tokens == 16
    assert [usage.role for usage in summary.role_usages] == list(TOKEN_ROLES)
    assert summary.role_usages[0].percentage == 25.0
    assert summary.role_usages[2].percentage == 75.0
    assert summary.role_usages[-1].total_tokens == 0


def test_monitor_marks_cache_reads_unavailable_when_not_reported() -> None:
    monitor = TokenUsageMonitor()
    monitor.record_usage(
        "Reviewer",
        {
            "input_tokens": 20,
            "output_tokens": 5,
            "total_tokens": 25,
        },
    )

    assert monitor.summary().cache_read_tokens is None


def test_monitor_collects_usage_from_wrapped_composed_runnable() -> None:
    monitor = TokenUsageMonitor()
    runnable = monitor.with_role(
        UsageModel() | StrOutputParser(),
        "Explorer",
    )

    assert runnable.invoke([HumanMessage(content="test")]) == "ok"

    summary = monitor.summary()
    assert summary.total_tokens == 5
    assert summary.cache_read_tokens == 1
    assert summary.role_usages[2].total_tokens == 5
