from collections.abc import Callable
from time import monotonic
from typing import Any

from config import Setting
from graph.state import ResolverState
from prompts.explorer import build_explore_input
from schemas.explore_execution import ExploreExecution
from schemas.explore_report import ExploreReport
from schemas.failure import ClassifiedFailure, failure_from_exception, make_failure
from services.agent_execution import invoke_tool_agent
from services.artifacts import write_stage_artifact


def build_explore_node(
    explore_agent_factory: Callable[[str], Any],
    clock: Callable[[], float] = monotonic,
):
    """创建调用 Explore Agent 的 LangGraph 节点。"""

    def explore_node(state: ResolverState) -> dict:
        """调查仓库并生成 ExploreReport。"""

        started = clock()
        focus = state.get(
            "explore_focus",
            "定位与 Issue 相关的代码、潜在根因和测试位置",
        )
        repair_round = state.get(
            "repair_round",
            state.get("cycle", 0) + 1,
        )
        stage_call = state.get("explore_stage_call", 1)
        item_index = state.get(
            "explore_item_index",
            len(state.get("explore_reports", [])) + 1,
        )
        title = state.get("explore_title", f"Explore task {item_index:02d}")
        try:
            issue = state.get("issue")

            if issue is None:
                raise ClassifiedFailure(
                    make_failure("INTERNAL", "State 中缺少规范化后的 Issue。")
                )
            repo_path = state.get("repo_path")
            if not repo_path:
                raise ClassifiedFailure(
                    make_failure("INTERNAL", "State 中缺少 repo_path。")
                )

            explore_agent = explore_agent_factory(repo_path)

            user_message = build_explore_input(
                issue=issue,
                focus=focus,
                evidence_digest=state.get("evidence_digest"),
            )

            try:
                result = invoke_tool_agent(
                    explore_agent,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": user_message,
                            }
                        ]
                    },
                    agent_name="Explore Agent",
                    recursion_limit=state.get(
                        "agent_recursion_limit",
                        Setting().AGENT_RECURSION_LIMIT,
                    ),
                )
            except Exception as exc:
                raise ClassifiedFailure(
                    failure_from_exception(
                        exc,
                        "MODEL",
                        prefix="仓库探索失败：",
                    )
                ) from exc

            report = result.get("structured_response")

            if not isinstance(report, ExploreReport):
                raise ClassifiedFailure(
                    make_failure(
                        "MODEL",
                        "Explore Agent 未返回有效的 ExploreReport。",
                    )
                )

            write_stage_artifact(
                run_dir=state["run_dir"],
                kind="explore",
                stage="EXPLORE",
                repair_round=repair_round,
                stage_call=stage_call,
                index=item_index,
                payload=report,
                metadata={"title": title, "task": focus},
            )

            return {
                # 这里必须返回新增的一项，而不是全部历史报告
                "explore_reports": [report],
                "explore_executions": [
                    ExploreExecution(
                        repair_round=repair_round,
                        stage_call=stage_call,
                        item_index=item_index,
                        focus=focus,
                        title=title,
                        status="PASSED",
                        duration=max(clock() - started, 0.0),
                    )
                ],
            }

        except Exception as exc:
            failure = failure_from_exception(
                exc,
                "INTERNAL",
                prefix=("" if isinstance(exc, ClassifiedFailure) else "仓库探索失败："),
            )
            return {
                "explore_failures": [failure],
                "explore_executions": [
                    ExploreExecution(
                        repair_round=repair_round,
                        stage_call=stage_call,
                        item_index=item_index,
                        focus=focus,
                        title=title,
                        status="FAILED",
                        duration=max(clock() - started, 0.0),
                        failure=failure,
                    )
                ],
            }

    return explore_node
