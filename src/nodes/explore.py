from typing import Any

from graph.state import ResolverState
from prompts.explorer import build_explore_input
from schemas.explore_report import ExploreReport
from services.artifacts import write_stage_artifact


def build_explore_node(explore_agent: Any):
    """创建调用 Explore Agent 的 LangGraph 节点。"""

    def explore_node(state: ResolverState) -> dict:
        """调查仓库并生成 ExploreReport。"""

        try:
            issue = state.get("issue")

            if issue is None:
                raise RuntimeError("State 中缺少规范化后的 Issue。")

            focus = state.get(
                "explore_focus",
                "定位与 Issue 相关的代码、潜在根因和测试位置",
            )

            user_message = build_explore_input(
                repo_path=state["repo_path"],
                issue=issue,
                focus=focus,
                current_summary=state.get("current_summary", ""),
            )

            result = explore_agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": user_message,
                        }
                    ]
                }
            )

            report = result.get("structured_response")

            if not isinstance(report, ExploreReport):
                raise RuntimeError(
                    "Explore Agent 未返回有效的 ExploreReport。"
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
                "explore_errors": [f"仓库探索失败：{exc}"],
            }

    return explore_node
