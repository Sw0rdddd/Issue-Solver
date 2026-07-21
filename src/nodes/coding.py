import subprocess
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from agents.coder import build_coding_agent
from config import Setting
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
from services.agent_execution import invoke_tool_agent
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


def _probe_coding_environment(
    context: CodingToolContext,
    coding_task: CodingTask,
) -> FailureInfo | None:
    """复核 Agent 声称的环境故障是否能由程序重现。"""

    try:
        if not context.repo_root.is_dir():
            raise OSError("目标仓库目录不存在。")
        list(context.repo_root.iterdir())

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=context.repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or "无法读取 Git HEAD。")
        if result.stdout.strip() != context.base_commit:
            raise OSError("目标仓库 HEAD 已变化。")

        for relative in coding_task.relevant_files:
            target = context.repo_root / relative
            if target.is_file():
                with target.open("rb") as stream:
                    stream.read(1)
    except (OSError, subprocess.SubprocessError) as exc:
        return make_failure(
            "ENVIRONMENT",
            f"程序复核 Coding 环境失败：{exc}",
        )
    return None


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
                response = invoke_tool_agent(
                    agent,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": user_message,
                            }
                        ]
                    },
                    agent_name="Coding Agent",
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
                agent_failure = coding_result.failure
                if agent_failure is None:
                    raise ClassifiedFailure(
                        make_failure(
                            "MODEL",
                            "Coding Agent 返回失败但未提供 failure。",
                        )
                    )
                if agent_failure.type == "ENVIRONMENT":
                    verified_failure = _probe_coding_environment(
                        context,
                        coding_task,
                    )
                    if verified_failure is not None:
                        raise ClassifiedFailure(verified_failure)
                    raise ClassifiedFailure(
                        make_failure(
                            "MODEL",
                            "Coding Agent 将无法复现的工具调用问题误报为环境故障。",
                            "检查 Agent 的仓库相对路径参数和工具调用记录后重试。",
                        )
                    )
                raise ClassifiedFailure(agent_failure)
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
