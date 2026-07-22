from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import Setting
from graph.state import ResolverState
from prompts.coordinator import (
    COORDINATOR_SYSTEM_PROMPT,
    build_coordinator_input,
)
from schemas.coordinator_decision import CoordinatorDecision
from schemas.evidence_digest import EvidenceDigest
from schemas.failure import (
    ClassifiedFailure,
    FailureInfo,
    failure_from_exception,
    make_failure,
)


def _failed_update(
    failure: FailureInfo,
    *,
    rollback_required: bool = False,
) -> dict:
    update = {
        "phase": "COORDINATE",
        "status": "FAILED",
        "next_action": "FAILED",
        "current_summary": failure.message,
        "explore_focuses": [],
        "explore_titles": [],
        "failure": failure,
    }
    if rollback_required:
        update.update(
            {
                "rollback_required": True,
            }
        )
    return update


def _normalize_explore_titles(
    focuses: list[str],
    titles: list[str],
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for index, _focus in enumerate(focuses, start=1):
        raw_title = titles[index - 1] if index <= len(titles) else ""
        title = " ".join(raw_title.replace("`", "").split())
        fallback = f"Explore task {index:02d}"
        if not title or title in seen:
            title = fallback
        if title in seen:
            title = f"{fallback}-{index}"
        normalized.append(title)
        seen.add(title)
    return normalized


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
                raise ClassifiedFailure(
                    make_failure("INTERNAL", "State 中缺少规范化后的 Issue。")
                )

            explore_failures = state.get("explore_failures", [])
            if explore_failures:
                first = explore_failures[0]
                raise ClassifiedFailure(
                    first.model_copy(
                        update={
                            "message": "并行仓库探索失败："
                            + "；".join(
                                failure.message for failure in explore_failures
                            )
                        }
                    )
                )

            cycle = state.get("cycle", 0)
            max_cycles = state.get("max_cycles", Setting().MAX_CYCLES)
            max_explore_batches = state.get(
                "max_explore_batches",
                Setting().MAX_EXPLORE_BATCHES,
            )
            if max_cycles < 1:
                raise ClassifiedFailure(
                    make_failure("INPUT", "max_cycles 必须大于 0。")
                )
            if max_explore_batches < 1:
                raise ClassifiedFailure(
                    make_failure(
                        "INPUT",
                        "max_explore_batches 必须大于 0。",
                    )
                )
            if state.get("rollback_required"):
                return _failed_update(
                    state.get("failure")
                    or make_failure("SAFETY", "工作区需要回滚。"),
                    rollback_required=True,
                )

            explore_reports = state.get("explore_reports", [])
            evidence_digest = state.get("evidence_digest")
            if evidence_digest is not None and not isinstance(
                evidence_digest,
                EvidenceDigest,
            ):
                evidence_digest = EvidenceDigest.model_validate(
                    evidence_digest
                )
            summarized_count = (
                evidence_digest.source_report_count
                if evidence_digest is not None
                else 0
            )
            if summarized_count > len(explore_reports):
                raise ClassifiedFailure(
                    make_failure(
                        "INTERNAL",
                        "EvidenceDigest 覆盖的报告数量超过当前 ExploreReport 数量。",
                    )
                )
            new_explore_reports = explore_reports[summarized_count:]
            repair_round = cycle + 1
            explore_batches_used = (
                state.get("explore_stage_call", 0)
                if state.get("repair_round") == repair_round
                else 0
            )
            force_code = bool(explore_reports) and (
                explore_batches_used >= max_explore_batches
            )
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
                    make_failure(
                        "LIMIT",
                        f"已达到最大循环次数 {max_cycles}。",
                    )
                )

            user_message = build_coordinator_input(
                issue=issue,
                current_summary=state.get("current_summary", ""),
                repository_profile=state.get("repository_profile"),
                evidence_digest=evidence_digest,
                new_explore_reports=new_explore_reports,
                coding_result=state.get("coding_result"),
                review_result=state.get("review_result"),
                latest_test_results=latest_test_results,
                cycle=cycle,
                max_cycles=max_cycles,
                explore_batches_used=explore_batches_used,
                max_explore_batches=max_explore_batches,
                force_code=force_code,
            )

            try:
                decision = coordinator_agent.invoke(
                    [
                        SystemMessage(content=COORDINATOR_SYSTEM_PROMPT),
                        HumanMessage(content=user_message),
                    ]
                )
                if force_code and (
                    not isinstance(decision, CoordinatorDecision)
                    or decision.next_action != "CODE"
                ):
                    decision = coordinator_agent.invoke(
                        [
                            SystemMessage(content=COORDINATOR_SYSTEM_PROMPT),
                            HumanMessage(
                                content=(
                                    user_message
                                    + "\n\n纠正要求：探索预算已耗尽，"
                                    "禁止继续 EXPLORE；必须根据已有证据"
                                    "选择 CODE 并返回完整 CodingTask。"
                                )
                            ),
                        ]
                    )
            except Exception as exc:
                raise ClassifiedFailure(
                    failure_from_exception(
                        exc,
                        "MODEL",
                        prefix="Coordinator 决策失败：",
                    )
                ) from exc

            if not isinstance(decision, CoordinatorDecision):
                raise ClassifiedFailure(
                    make_failure(
                        "MODEL",
                        "Coordinator Agent 未返回有效的 CoordinatorDecision。",
                    )
                )

            if new_explore_reports:
                if decision.evidence_digest is None:
                    raise ClassifiedFailure(
                        make_failure(
                            "MODEL",
                            "Coordinator 未为新增 ExploreReport 返回 EvidenceDigest。",
                        )
                    )
                if (
                    decision.evidence_digest.source_report_count
                    != len(explore_reports)
                ):
                    raise ClassifiedFailure(
                        make_failure(
                            "MODEL",
                            "EvidenceDigest.source_report_count 与当前 ExploreReport 数量不一致。",
                        )
                    )
            elif decision.evidence_digest is not None:
                raise ClassifiedFailure(
                    make_failure(
                        "MODEL",
                        "没有新增 ExploreReport 时不得重复生成 EvidenceDigest。",
                    )
                )

            if force_code and decision.next_action != "CODE":
                raise ClassifiedFailure(
                    make_failure(
                        "MODEL",
                        "探索预算耗尽后 Coordinator 未返回有效的 CODE 决策。",
                    )
                )

            if not explore_reports and decision.next_action != "EXPLORE":
                raise ClassifiedFailure(
                    make_failure("MODEL", "首次 Coordinator 决策必须为 EXPLORE。")
                )

            if decision.next_action in {"EXPLORE", "CODE"} and cycle >= max_cycles:
                return _failed_update(
                    make_failure(
                        "LIMIT",
                        f"已达到最大循环次数 {max_cycles}。",
                    )
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
                    raise ClassifiedFailure(
                        make_failure(
                            "MODEL",
                            "Review 和本轮全部测试均通过后才能 FINISH。",
                        )
                    )

            update = {
                "next_action": decision.next_action,
                "current_summary": decision.current_summary,
                "explore_focuses": [],
                "explore_titles": [],
            }
            if new_explore_reports:
                update["evidence_digest"] = decision.evidence_digest

            if decision.next_action == "EXPLORE":
                explore_titles = _normalize_explore_titles(
                    decision.explore_focuses,
                    decision.explore_titles,
                )
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
                        "explore_titles": explore_titles,
                        "repair_round": repair_round,
                        "explore_stage_call": explore_stage_call,
                    }
                )
                if is_new_round and state.get("coding_stage_call", 0):
                    update["coding_stage_call"] = 0
            elif decision.next_action == "CODE":
                is_new_round = state.get("repair_round") != repair_round
                coding_task = decision.coding_task
                existing_files = state.get("changed_files", [])
                if coding_task is None:
                    raise ClassifiedFailure(
                        make_failure("MODEL", "CODE 决策缺少 CodingTask。")
                    )
                acceptance_criteria = [
                    criterion.strip()
                    for criterion in issue.acceptance_criteria
                    if criterion.strip()
                ]
                if not acceptance_criteria:
                    raise ClassifiedFailure(
                        make_failure(
                            "INPUT",
                            "Issue 缺少可以安全确定的验收条件。",
                            "请补充明确的期望行为后重试。",
                        )
                    )
                coding_task = coding_task.model_copy(
                    update={"acceptance_criteria": acceptance_criteria}
                )
                missing_scope = [
                    path
                    for path in existing_files
                    if not any(
                        _path_is_in_scope(path, scope)
                        for scope in coding_task.allowed_scope
                    )
                ]
                if missing_scope:
                    raise ClassifiedFailure(
                        make_failure(
                            "MODEL",
                            "返工 CodingTask.allowed_scope 未覆盖累计修改："
                            + ", ".join(missing_scope),
                        )
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
                        "failure": decision.failure,
                    }
                )

            return update

        except Exception as exc:
            return _failed_update(
                failure_from_exception(
                    exc,
                    "INTERNAL",
                    prefix=(
                        "" if isinstance(exc, ClassifiedFailure)
                        else "Coordinator 决策失败："
                    ),
                )
            )

    return coordinator_node
