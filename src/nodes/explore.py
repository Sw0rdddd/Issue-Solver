from collections.abc import Callable
from typing import Any

from config import Setting
from graph.state import ResolverState
from prompts.explorer import build_explore_input
from schemas.explore_report import ExploreReport
from schemas.failure import ClassifiedFailure, failure_from_exception, make_failure
from services.agent_execution import invoke_tool_agent
from services.artifacts import write_stage_artifact


def build_explore_node(explore_agent_factory: Callable[[str], Any]):
    """创建调用 Explore Agent 的 LangGraph 节点。"""

    def explore_node(state: ResolverState) -> dict:
        """调查仓库并生成 ExploreReport。"""

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

            focus = state.get(
                "explore_focus",
                "定位与 Issue 相关的代码、潜在根因和测试位置",
            )
            explore_agent = explore_agent_factory(repo_path)

            user_message = build_explore_input(
                issue=issue,
                focus=focus,
                current_summary=state.get("current_summary", ""),
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

            repair_round = state.get(
                "repair_round",
                state.get("cycle", 0) + 1,
            )
            stage_call = state.get("explore_stage_call", 1)
            item_index = state.get(
                "explore_item_index",
                len(state.get("explore_reports", [])) + 1,
            )
            write_stage_artifact(
                run_dir=state["run_dir"],
                kind="explore",
                stage="EXPLORE",
                repair_round=repair_round,
                stage_call=stage_call,
                index=item_index,
                payload=report,
            )

            return {
                # 这里必须返回新增的一项，而不是全部历史报告
                "explore_reports": [report],
            }

        except Exception as exc:
            return {
                "explore_failures": [
                    failure_from_exception(
                        exc,
                        "INTERNAL",
                        prefix=(
                            "" if isinstance(exc, ClassifiedFailure)
                            else "仓库探索失败："
                        ),
                    )
                ],
            }

    return explore_node
