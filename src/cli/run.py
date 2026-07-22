import argparse
import sys
from pathlib import Path
from typing import Any, cast

from config import Setting
from graph.builder import build_graph
from nodes.finalize import rollback_state_to_base
from schemas.environment_info import EnvironmentInfo
from schemas.failure import (
    ClassifiedFailure,
    FailureInfo,
    failure_from_exception,
    make_failure,
)
from services.artifacts import write_round_artifact, write_run_artifact
from services.openai_compatible_model import build_chat_model
from services.python_environment import discover_python_environment
from services.repository import find_repo_root
from services.run_store import create_run_id
from services.token_usage import TokenUsageMonitor

from cli.report import RunReportSession
from cli.terminal import TerminalReporter


CONTROLLER_ROOT = Path(__file__).resolve().parents[2]


def _workflow_recursion_limit(
    max_cycles: int,
    max_explore_batches: int,
) -> int:
    """覆盖配置允许的完整外层工作流节点数。"""

    return 5 + max_cycles * (2 * max_explore_batches + 4)


def _resolve_run_root(
    repo_root: Path,
    configured_root: str | Path,
    base_root: Path | None = None,
) -> Path:
    root = Path(configured_root).expanduser()
    if not root.is_absolute():
        root = (base_root or CONTROLLER_ROOT) / root
    resolved = root.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        return resolved
    raise ClassifiedFailure(
        make_failure("SAFETY", "RUN_ROOT 必须位于目标 Git 仓库之外。")
    )


def _require_editable_installation() -> None:
    if not (CONTROLLER_ROOT / "pyproject.toml").is_file():
        raise ClassifiedFailure(
            make_failure(
                "ENVIRONMENT",
                "全局 issue-solver 仅支持可编辑安装。",
                "请执行：uv tool install --editable <issue-solver 项目路径>",
            )
        )


def _preflight_environment(repo_root: Path, run_dir: Path) -> EnvironmentInfo:
    environment = discover_python_environment(repo_root, run_dir)
    write_run_artifact(
        run_dir=run_dir,
        kind="environment_result",
        stage="INITIALIZE",
        payload=environment,
    )
    return environment


def _prompt_failure_rollback(
    result: dict[str, Any],
    reporter: TerminalReporter,
) -> bool:
    changed_files = list(result.get("changed_files") or [])
    if (
        result.get("status") != "FAILED"
        or not changed_files
        or result.get("rollback_required")
    ):
        return False

    interactive = sys.stdin.isatty()
    should_rollback = False
    decision = "KEEP_NON_INTERACTIVE"
    if interactive:
        reporter.prepare_for_prompt()
        while True:
            try:
                answer = input(
                    f"运行失败，当前存在 {len(changed_files)} 个修改文件，"
                    "是否回滚到 base commit？[y/N] "
                ).strip().lower()
            except EOFError:
                answer = ""
            if answer in {"", "n", "no"}:
                decision = "KEEP"
                break
            if answer in {"y", "yes"}:
                decision = "ROLLBACK"
                should_rollback = True
                break
            reporter.notice("请输入 Y 或 N。")

    rollback_success = False
    rollback_failure = None
    if should_rollback:
        failure = FailureInfo.model_validate(
            result.get("failure")
            or make_failure("INTERNAL", "运行失败但未提供失败信息。")
        )
        try:
            rolled_back = rollback_state_to_base(
                result,
                failure,
            )
            rollback_success = rolled_back["success"]
            rollback_failure = (
                FailureInfo.model_validate(rolled_back["failure"])
                if rolled_back["failure"] is not None
                else None
            )
        except Exception as exc:
            rollback_failure = failure_from_exception(exc, "SAFETY")
        if rollback_success:
            result["changed_files"] = []
            reporter.notice("✓ 已回滚到 base commit。")
        else:
            rollback_failure = rollback_failure or make_failure(
                "SAFETY",
                "回滚失败但未提供原因。",
            )
            result["rollback_failure"] = rollback_failure
            reporter.failure_notice("回滚失败", rollback_failure)

    try:
        write_round_artifact(
            run_dir=result["run_dir"],
            kind="rollback_decision",
            stage=str(result.get("phase") or "FINALIZE"),
            repair_round=max(result.get("repair_round", 1), 1),
            payload={
                "decision": decision,
                "interactive": interactive,
                "rollback_success": rollback_success,
                "rollback_failure": rollback_failure,
            },
        )
    except Exception as exc:
        result["rollback_failure"] = failure_from_exception(
            exc,
            "INTERNAL",
            prefix="记录回滚决策失败：",
        )
    return rollback_success


def _worktree_status(
    result: dict[str, Any],
    *,
    rollback_succeeded: bool = False,
) -> str | None:
    if result.get("changed_files"):
        return "保留修改"
    if rollback_succeeded or result.get("rollback_required"):
        return "已回滚"
    return None


def run_command(
    args: argparse.Namespace,
    *,
    reporter: TerminalReporter,
    token_usage: TokenUsageMonitor,
    global_mode: bool = False,
) -> int:
    """运行当前已实现的最小 StateGraph。"""

    if global_mode:
        _require_editable_installation()

    repo_path = Path(args.repo).expanduser() if args.repo else Path.cwd()
    if not repo_path.is_dir():
        raise ClassifiedFailure(
            make_failure("INPUT", f"仓库路径不存在或不是目录：{repo_path}")
        )

    try:
        repo_root = find_repo_root(repo_path)
    except Exception as exc:
        raise ClassifiedFailure(
            failure_from_exception(exc, "INPUT")
        ) from exc
    if repo_root.resolve() == CONTROLLER_ROOT.resolve():
        raise ClassifiedFailure(
            make_failure(
                "SAFETY",
                "禁止将 issue-solver 控制程序仓库作为目标测试仓库。",
            )
        )

    setting = Setting()
    model_name = args.model or setting.MODEL_NAME
    run_id = create_run_id()
    configured_run_root = args.run_root or setting.RUN_ROOT
    run_root = _resolve_run_root(
        repo_root,
        configured_run_root,
        CONTROLLER_ROOT,
    )
    run_dir = run_root / repo_root.name / run_id
    run_dir.mkdir(parents=True)
    reporter.begin_run(
        model_name=model_name,
        run_id=run_id,
        repo_path=repo_root,
        run_dir=run_dir,
        run_root=configured_run_root,
    )
    report_session = RunReportSession(
        run_dir=run_dir,
        model_name=model_name,
        reporter=reporter,
        token_usage=token_usage,
    )
    report_state: dict[str, Any] = {
        "run_id": run_id,
        "phase": "INITIALIZE",
        "status": "RUNNING",
        "cycle": 0,
        "repo_path": str(repo_root),
        "run_dir": str(run_dir),
        "issue_input": args.issue,
    }

    reporter.start_timing("preflight")
    try:
        environment = _preflight_environment(repo_root, run_dir)
    except Exception as exc:
        failure = failure_from_exception(
            exc,
            "ENVIRONMENT",
            prefix="目标仓库环境预检失败：",
        )
        try:
            write_run_artifact(
                run_dir=run_dir,
                kind="failure_environment",
                stage="INITIALIZE",
                payload={
                    "failure": failure,
                    "llm_called": False,
                    "dependencies_installed": False,
                    "worktree_modified": False,
                },
            )
        except Exception as artifact_exc:
            failure = make_failure(
                "INTERNAL",
                f"{failure.message}；记录环境失败日志失败：{artifact_exc}",
            )
        reporter.preflight_failed(failure)
        reporter.error_block(
            "环境预检失败",
            reporter.failure_details(failure)
            + [("安全状态", "未安装依赖、未调用 LLM、未修改工作区")],
        )
        reporter.set_outcome(
            success=False,
            result={"failure": failure},
            phase="INITIALIZE",
            worktree_status="未修改",
        )
        report_session.finish(
            state={
                **report_state,
                "status": "FAILED",
                "failure": failure,
            },
            model=None,
            worktree_status="未修改",
        )
        return 1

    reporter.preflight_succeeded(environment)
    report_state["environment"] = environment
    model = None
    try:
        if not model_name:
            raise ClassifiedFailure(
                make_failure("ENVIRONMENT", "配置 MODEL_NAME 不能为空。")
            )
        if not setting.API_KEY:
            raise ClassifiedFailure(
                make_failure("ENVIRONMENT", "配置 API_KEY 不能为空。")
            )
        if not setting.BASE_URL:
            raise ClassifiedFailure(
                make_failure("ENVIRONMENT", "配置 BASE_URL 不能为空。")
            )
        model = build_chat_model(
            model=model_name,
            api_key=setting.API_KEY,
            base_url=setting.BASE_URL,
            reasoning_history_mode=setting.REASONING_HISTORY,
        )
        graph = build_graph(model, token_usage).compile()

        initial_state = {
            **report_state,
            "max_cycles": args.max_cycles or setting.MAX_CYCLES,
            "agent_recursion_limit": setting.AGENT_RECURSION_LIMIT,
            "max_explore_batches": setting.MAX_EXPLORE_BATCHES,
            "test_timeout": args.test_timeout or setting.TEST_TIMEOUT,
            "test_tail_lines": args.test_tail_lines or setting.TEST_TAIL_LINES,
            "explore_reports": [],
            "explore_executions": [],
            "explore_failures": [],
            "test_results": [],
            "latest_test_results": [],
            "explore_stage_call": 0,
            "coding_stage_call": 0,
        }
        result: dict[str, Any] | None = None
        reporter.graph_started()
        graph = graph.with_config(
            {
                "recursion_limit": _workflow_recursion_limit(
                    initial_state["max_cycles"],
                    initial_state["max_explore_batches"],
                )
            }
        )

        for mode, event in graph.stream(
            initial_state,
            stream_mode=["updates", "values"],
        ):
            if mode == "values":
                if not isinstance(event, dict):
                    raise TypeError("values 流事件必须是字典。")
                result = cast(dict[str, Any], event)
                report_state = dict(result)
                continue
            if not isinstance(event, dict):
                raise TypeError("updates 流事件必须是字典。")
            update_event = cast(dict[str, Any], event)
            for node, update in update_event.items():
                reporter.handle_update(node, update)

        if result is None:
            raise RuntimeError("工作流未返回最终状态。")

        if result.get("status") == "FAILED":
            rollback_succeeded = _prompt_failure_rollback(result, reporter)
            worktree_status = _worktree_status(
                result,
                rollback_succeeded=rollback_succeeded,
            )
            failure = FailureInfo.model_validate(
                result.get("failure")
                or make_failure("INTERNAL", "工作流未提供失败信息。")
            )
            reporter.error_block(
                "运行失败",
                reporter.failure_details(failure),
            )
            rollback_failure = result.get("rollback_failure")
            if rollback_failure is not None:
                reporter.failure_notice(
                    "回滚失败",
                    FailureInfo.model_validate(rollback_failure),
                )
            reporter.set_outcome(
                success=False,
                result=result,
                worktree_status=worktree_status,
            )
            report_session.finish(
                state=result,
                model=model,
                worktree_status=worktree_status,
            )
            return 1

        worktree_status = _worktree_status(result)
        reporter.set_outcome(
            success=True,
            result=result,
            worktree_status=worktree_status,
        )
        report_session.finish(
            state={**result, "status": "FINISHED"},
            model=model,
            worktree_status=worktree_status,
        )
        return 0

    except Exception as exc:
        failure = failure_from_exception(exc, "INTERNAL")
        report_session.finish(
            state={
                **report_state,
                "status": "FAILED",
                "failure": failure,
            },
            model=model,
            worktree_status=_worktree_status(report_state) or "未知",
        )
        raise
