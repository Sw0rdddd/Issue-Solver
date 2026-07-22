from pathlib import Path
from collections.abc import Callable
from typing import Any

from config import Setting
from graph.state import ResolverState
from prompts.reviewer import build_review_input
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.failure import ClassifiedFailure, failure_from_exception, make_failure
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from services.agent_execution import invoke_tool_agent
from services.artifacts import write_round_artifact


def _agent_report(result: ReviewResult | None) -> dict | None:
    if result is None:
        return None
    return result.model_dump(mode="json")


def build_review_node(review_agent_factory: Callable[[str, str], Any]):
    """创建只读审查累计代码修改的 LangGraph 节点。"""

    def review_node(state: ResolverState) -> dict:
        review_result: ReviewResult | None = None
        repair_round = state.get("repair_round", state.get("cycle", 0) + 1)
        run_dir = state.get("run_dir")

        try:
            if repair_round < 1:
                raise RuntimeError("repair_round 必须大于 0。")
            if not run_dir:
                raise RuntimeError("State 中缺少 run_dir。")

            issue = state.get("issue")
            if not isinstance(issue, IssueSpec):
                raise RuntimeError("State 中缺少规范化后的 Issue。")
            coding_task = state.get("coding_task")
            if not isinstance(coding_task, CodingTask):
                raise RuntimeError("State 中缺少有效的 CodingTask。")
            coding_result = state.get("coding_result")
            if not isinstance(coding_result, CodingResult):
                raise RuntimeError("State 中缺少有效的 CodingResult。")
            if not coding_result.success:
                raise RuntimeError("不能审查未完成的 CodingResult。")

            repo_path = state.get("repo_path")
            base_commit = state.get("base_commit")
            if not repo_path:
                raise RuntimeError("State 中缺少 repo_path。")
            if not base_commit:
                raise RuntimeError("State 中缺少 base_commit。")
            review_agent = review_agent_factory(repo_path, base_commit)

            try:
                response = invoke_tool_agent(
                    review_agent,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": build_review_input(
                                    issue=issue,
                                    coding_task=coding_task,
                                    coding_result=coding_result,
                                    explore_reports=state.get("explore_reports", []),
                                    current_summary=state.get("current_summary", ""),
                                ),
                            }
                        ]
                    },
                    agent_name="Review Agent",
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
                        prefix="Review Agent 调用失败：",
                    )
                ) from exc
            if not isinstance(response, dict):
                raise ClassifiedFailure(
                    make_failure("MODEL", "Review Agent 未返回有效响应。")
                )

            candidate = response.get("structured_response")
            if not isinstance(candidate, ReviewResult):
                raise ClassifiedFailure(
                    make_failure(
                        "MODEL",
                        "Review Agent 未返回有效的 ReviewResult。",
                    )
                )
            review_result = candidate

            write_round_artifact(
                run_dir=run_dir,
                kind="review_result",
                stage="REVIEW",
                repair_round=repair_round,
                payload=review_result,
            )
            return {
                "review_result": review_result,
                "repair_round": repair_round,
                "phase": "TEST",
            }

        except Exception as exc:
            failure = failure_from_exception(
                exc,
                "INTERNAL",
                prefix="Review 失败：",
            )
            if run_dir:
                try:
                    write_round_artifact(
                        run_dir=Path(run_dir),
                        kind="failure_review",
                        stage="REVIEW",
                        repair_round=max(repair_round, 1),
                        payload={
                            "failure": failure,
                            "agent_report": _agent_report(review_result),
                        },
                    )
                except Exception as log_exc:
                    failure = make_failure(
                        "INTERNAL",
                        f"{failure.message}；记录失败产物失败：{log_exc}",
                    )

            return {
                "phase": "REVIEW",
                "status": "FAILED",
                "repair_round": max(repair_round, 1),
                "failure": failure,
            }

    return review_node
