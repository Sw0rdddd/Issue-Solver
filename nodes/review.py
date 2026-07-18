from pathlib import Path
from typing import Any

from graph.state import ResolverState
from prompts.reviewer import build_review_input
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.issue_specification import IssueSpec
from schemas.review_result import ReviewResult
from services.artifacts import write_round_artifact


def _agent_report(result: ReviewResult | None) -> dict | None:
    if result is None:
        return None
    return result.model_dump(mode="json")


def build_review_node(review_agent: Any):
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

            response = review_agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": build_review_input(
                                repo_path=repo_path,
                                base_commit=base_commit,
                                issue=issue,
                                coding_task=coding_task,
                                coding_result=coding_result,
                                explore_reports=state.get("explore_reports", []),
                                current_summary=state.get("current_summary", ""),
                            ),
                        }
                    ]
                }
            )
            if not isinstance(response, dict):
                raise RuntimeError("Review Agent 未返回有效响应。")

            candidate = response.get("structured_response")
            if not isinstance(candidate, ReviewResult):
                raise RuntimeError("Review Agent 未返回有效的 ReviewResult。")
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
            error = f"Review 失败：{exc}"
            if run_dir:
                try:
                    write_round_artifact(
                        run_dir=Path(run_dir),
                        kind="failure_review",
                        stage="REVIEW",
                        repair_round=max(repair_round, 1),
                        payload={
                            "reason": error,
                            "agent_report": _agent_report(review_result),
                        },
                    )
                except Exception as log_exc:
                    error = f"{error}；记录失败产物失败：{log_exc}"

            return {
                "phase": "REVIEW",
                "status": "FAILED",
                "repair_round": max(repair_round, 1),
                "rollback_prompt_required": True,
                "rollback_reason": error,
                "error": error,
            }

    return review_node
