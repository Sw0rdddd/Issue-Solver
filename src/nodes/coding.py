from pathlib import Path

from langchain_core.language_models import BaseChatModel

from agents.coder import build_coding_agent
from graph.state import ResolverState
from prompts.coder import build_coding_input
from schemas.coding_result import CodingResult
from schemas.coding_task import CodingTask
from schemas.failure import (
    ClassifiedFailure,
    FailureInfo,
    failure_from_exception,
    make_failure,
)
from schemas.issue_specification import IssueSpec
from services.artifacts import write_stage_artifact
from tools.coding import (
    CodingToolContext,
    get_coding_iteration_count,
    inspect_coding_changes,
    rollback_to_base,
)


def _agent_report(result: CodingResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "summary": result.summary,
        "remaining_risks": result.remaining_risks,
    }


def build_coding_node(model: BaseChatModel):
    """创建串行执行 Coding Agent 并确定性检查结果的节点。"""

    def coding_node(state: ResolverState) -> dict:
        context: CodingToolContext | None = None
        coding_result: CodingResult | None = None
        cycle = state.get("cycle", 0)
        repair_round = cycle + 1
        stage_call = state.get("coding_stage_call", 1)
        iteration = 0
        rollback_failure: FailureInfo | None = None

        try:
            if cycle < 0:
                raise RuntimeError("cycle 不能小于 0。")
            if stage_call < 1:
                raise RuntimeError("coding_stage_call 必须大于 0。")

            issue = state.get("issue")
            if not isinstance(issue, IssueSpec):
                raise RuntimeError("State 中缺少规范化后的 Issue。")

            coding_task = state.get("coding_task")
            if not isinstance(coding_task, CodingTask):
                raise RuntimeError("State 中缺少有效的 CodingTask。")

            repo_path = state.get("repo_path")
            base_commit = state.get("base_commit")
            run_dir = state.get("run_dir")
            if not repo_path:
                raise RuntimeError("State 中缺少 repo_path。")
            if not base_commit:
                raise RuntimeError("State 中缺少 base_commit。")
            if not run_dir:
                raise RuntimeError("State 中缺少 run_dir。")

            write_stage_artifact(
                run_dir=run_dir,
                kind="coding_task",
                stage="CODING",
                repair_round=repair_round,
                stage_call=stage_call,
                index=0,
                payload=coding_task,
            )

            try:
                context = CodingToolContext.create(
                    repo_root=repo_path,
                    base_commit=base_commit,
                    run_dir=run_dir,
                    allowed_paths=coding_task.allowed_scope,
                    repair_round=repair_round,
                    stage_call=stage_call,
                    allow_existing_changes=bool(state.get("changed_files")),
                )
            except ClassifiedFailure:
                raise
            except ValueError as exc:
                raise ClassifiedFailure(
                    make_failure("SAFETY", str(exc))
                ) from exc
            agent = build_coding_agent(model, context)
            user_message = build_coding_input(
                repo_path=repo_path,
                issue=issue,
                coding_task=coding_task,
                explore_reports=state.get("explore_reports", []),
                current_summary=state.get("current_summary", ""),
            )
            try:
                response = agent.invoke(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": user_message,
                            }
                        ]
                    }
                )
            except Exception as exc:
                raise ClassifiedFailure(
                    failure_from_exception(
                        exc,
                        "MODEL",
                        prefix="Coding Agent 调用失败：",
                    )
                ) from exc
            if not isinstance(response, dict):
                raise ClassifiedFailure(
                    make_failure("MODEL", "Coding Agent 未返回有效响应。")
                )

            coding_result = response.get("structured_response")
            if not isinstance(coding_result, CodingResult):
                raise ClassifiedFailure(
                    make_failure(
                        "MODEL",
                        "Coding Agent 未返回有效的 CodingResult。",
                    )
                )

            iteration = get_coding_iteration_count(context)
            if not coding_result.success:
                raise ClassifiedFailure(coding_result.failure)
            if coding_result.diff_path is not None:
                raise ClassifiedFailure(
                    make_failure(
                        "MODEL",
                        "Coding 阶段的 diff_path 必须为 null。",
                    )
                )

            inspection = inspect_coding_changes(context)
            if not inspection["success"]:
                inspection_failure = inspection["failure"]
                if inspection_failure is None:
                    inspection_failure = make_failure(
                        "INTERNAL",
                        "Coding 修改检查失败但工具未提供原因。",
                    )
                else:
                    inspection_failure = FailureInfo.model_validate(
                        inspection_failure
                    )
                raise ClassifiedFailure(inspection_failure)
            actual_files = inspection["data"]["changed_files"]
            diff = inspection["data"]["diff"]
            if not diff.strip():
                raise ClassifiedFailure(
                    make_failure("SOLUTION", "Coding Agent 未产生代码修改。")
                )
            if coding_result.changed_files != actual_files:
                raise ClassifiedFailure(
                    make_failure(
                        "SOLUTION",
                        "CodingResult.changed_files 与实际修改不一致。",
                    )
                )

            write_stage_artifact(
                run_dir=run_dir,
                kind="coding_result",
                stage="CODING",
                repair_round=repair_round,
                stage_call=stage_call,
                index=iteration,
                payload=coding_result,
            )
            return {
                "coding_result": coding_result,
                "changed_files": actual_files,
                "coding_iteration": iteration,
                "repair_round": repair_round,
                "coding_stage_call": stage_call,
                "phase": "REVIEW",
            }

        except Exception as exc:
            failure = failure_from_exception(
                exc,
                "INTERNAL",
                prefix="Coding 失败：",
            )
            if context is not None:
                iteration = get_coding_iteration_count(context)
                rollback = rollback_to_base(
                    context,
                    failure,
                    agent_report=_agent_report(coding_result),
                )
                if not rollback["success"]:
                    rollback_failure = FailureInfo.model_validate(
                        rollback["failure"]
                        or make_failure(
                            "SAFETY",
                            "Coding 失败后的回滚未提供失败原因。",
                        )
                    )
            else:
                run_dir = state.get("run_dir")
                if run_dir:
                    try:
                        write_stage_artifact(
                            run_dir=Path(run_dir),
                            kind="failure_coding",
                            stage="CODING",
                            repair_round=repair_round,
                            stage_call=max(stage_call, 1),
                            index=iteration,
                            payload={
                                "failure": failure,
                                "agent_report": _agent_report(coding_result),
                                "base_commit": state.get("base_commit"),
                                "changed_files_before_rollback": [],
                                "rollback_success": False,
                                "rollback_failure": None,
                            },
                        )
                    except Exception as log_exc:
                        failure = make_failure(
                            "INTERNAL",
                            f"{failure.message}；记录失败产物失败：{log_exc}",
                        )

            update = {
                "phase": "CODE",
                "status": "FAILED",
                "changed_files": [],
                "coding_iteration": iteration,
                "repair_round": repair_round,
                "coding_stage_call": stage_call,
                "failure": failure,
            }
            if rollback_failure is not None:
                update["rollback_failure"] = rollback_failure
            return update

    return coding_node
