from typing import Any

from langgraph.errors import GraphRecursionError

from schemas.failure import ClassifiedFailure, make_failure


def invoke_tool_agent(
    agent: Any,
    payload: Any,
    *,
    agent_name: str,
    recursion_limit: int,
) -> Any:
    """使用独立递归上限调用工具型 Agent。"""

    try:
        return agent.invoke(
            payload,
            config={"recursion_limit": recursion_limit},
        )
    except GraphRecursionError as exc:
        raise ClassifiedFailure(
            make_failure(
                "LIMIT",
                f"{agent_name} 达到最大执行步数 {recursion_limit}。",
            )
        ) from exc
