from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import Setting
from graph.state import ResolverState
from prompts.coordinator import (
    COORDINATOR_SYSTEM_PROMPT,
    build_coordinator_input,
)
from schemas.coordinator_decision import CoordinatorDecision


def _failed_update(
    message: str,
    *,
    rollback_required: bool = False,
) -> dict:
    update = {
        "phase": "COORDINATE",
        "status": "FAILED",
        "next_action": "FAILED",
        "current_summary": message,
        "explore_focuses": [],
        "error": message,
    }
    if rollback_required:
        update.update(
            {
                "rollback_required": True,
                "rollback_reason": message,
            }
        )
    return update


def _path_is_in_scope(path: str, scope: str) -> bool:
    normalized_path = path.replace("\\", "/").strip("/")
    normalized_scope = scope.replace("\\", "/").strip("/")
    return normalized_path == normalized_scope or normalized_path.startswith(
        normalized_scope + "/"
    )


def _next_stage_call(
    state: ResolverState,
    field: str,
    repair_round: int,
) -> int:
    if state.get("repair_round") != repair_round:
        return 1
    return int(state.get(field, 0)) + 1


def build_coordinator_node(coordinator_agent: Any):
    """创建调用 Coordinator Agent 的 LangGraph 节点。"""

    def coordinator_node(state: ResolverState) -> dict:
        try:
            issue = state.get("issue")
            if issue is None:
                raise RuntimeError("State 中缺少规范化后的 Issue。")

            explore_errors = state.get("explore_errors", [])
            if explore_errors:
                raise RuntimeError(
                    "并行仓库探索失败：" + "；".join(explore_errors)
                )

            cycle = state.get("cycle", 0)
            max_cycles = state.get("max_cycles", Setting().MAX_CYCLES)
            if max_cycles < 1:
                raise RuntimeError("max_cycles 必须大于 0。")
            if state.get("rollback_required"):
                return _failed_update(
                    state.get("rollback_reason", "工作区需要回滚。"),
                    rollback_required=True,
                )

            explore_reports = state.get("explore_reports", [])
            latest_test_results = state.get("latest_test_results")
            if latest_test_results is None:
                latest_test_results = state.get("test_results", [])
            if (
                cycle >= max_cycles
                and (
                    state.get("review_result") is None
                    or not latest_test_results
                )
            ):
                return _failed_update(
                    f"已达到最大循环次数 {max_cycles}。",
                    rollback_required=bool(state.get("changed_files")),
                )

            user_message = build_coordinator_input(
                issue=issue,
                current_summary=state.get("current_summary", ""),
                explore_reports=explore_reports,
                coding_result=state.get("coding_result"),
                review_result=state.get("review_result"),
                latest_test_results=latest_test_results,
                cycle=cycle,
                max_cycles=max_cycles,
            )

            decision = coordinator_agent.invoke(
                [
                    SystemMessage(content=COORDINATOR_SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]
            )

            if not isinstance(decision, CoordinatorDecision):
                raise RuntimeError(
                    "Coordinator Agent 未返回有效的 CoordinatorDecision。"
                )

            if not explore_reports and decision.next_action != "EXPLORE":
                raise RuntimeError("首次 Coordinator 决策必须为 EXPLORE。")

            if decision.next_action in {"EXPLORE", "CODE"} and cycle >= max_cycles:
                return _failed_update(
                    f"已达到最大循环次数 {max_cycles}。",
                    rollback_required=True,
                )

            if decision.next_action == "FINISH":
                review_result = state.get("review_result")
                if (
                    review_result is None
                    or review_result.verdict != "APPROVE"
                    or not latest_test_results
                    or any(
                        result.status != "PASSED"
                        for result in latest_test_results
                    )
                ):
                    raise RuntimeError(
                        "Review 和本轮全部测试均通过后才能 FINISH。"
                    )

            update = {
                "next_action": decision.next_action,
                "current_summary": decision.current_summary,
                "explore_focuses": [],
            }

            if decision.next_action == "EXPLORE":
                repair_round = cycle + 1
                is_new_round = state.get("repair_round") != repair_round
                explore_stage_call = _next_stage_call(
                    state,
                    "explore_stage_call",
                    repair_round,
                )
                update.update(
                    {
                        "phase": "EXPLORE",
                        "explore_focuses": decision.explore_focuses,
                        "repair_round": repair_round,
                        "explore_stage_call": explore_stage_call,
                    }
                )
                if is_new_round and state.get("coding_stage_call", 0):
                    update["coding_stage_call"] = 0
            elif decision.next_action == "CODE":
                repair_round = cycle + 1
                is_new_round = state.get("repair_round") != repair_round
                coding_task = decision.coding_task
                existing_files = state.get("changed_files", [])
                if coding_task is None:
                    raise RuntimeError("CODE 决策缺少 CodingTask。")
                missing_scope = [
                    path
                    for path in existing_files
                    if not any(
                        _path_is_in_scope(path, scope)
                        for scope in coding_task.allowed_scope
                    )
                ]
                if missing_scope:
                    raise RuntimeError(
                        "返工 CodingTask.allowed_scope 未覆盖累计修改："
                        + ", ".join(missing_scope)
                    )
                coding_stage_call = _next_stage_call(
                    state,
                    "coding_stage_call",
                    repair_round,
                )
                update.update(
                    {
                        "phase": "CODE",
                        "coding_task": coding_task,
                        "repair_round": repair_round,
                        "coding_stage_call": coding_stage_call,
                    }
                )
                if is_new_round and state.get("explore_stage_call", 0):
                    update["explore_stage_call"] = 0
            elif decision.next_action == "FINISH":
                update["phase"] = "FINALIZE"
            else:
                update.update(
                    {
                        "phase": "COORDINATE",
                        "status": "FAILED",
                        "error": decision.current_summary,
                    }
                )

            return update

        except Exception as exc:
            return _failed_update(f"Coordinator 决策失败：{exc}")

    return coordinator_node
