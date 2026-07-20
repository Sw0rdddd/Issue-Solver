import argparse
import sys
from pathlib import Path
from typing import Any

from config import Setting
from graph.builder import build_graph
from nodes.finalize import rollback_state_to_base
from schemas.environment_info import EnvironmentInfo
from services.artifacts import write_round_artifact, write_run_artifact
from services.deepseek_model import ReasoningChatDeepSeek
from services.python_environment import discover_python_environment
from services.repository import find_repo_root
from services.run_store import create_run_id

from cli.report import RunReportSession
from cli.terminal import TerminalReporter


CONTROLLER_ROOT = Path(__file__).resolve().parents[2]


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
    raise ValueError("RUN_ROOT 必须位于目标 Git 仓库之外。")


def _require_editable_installation() -> None:
    if not (CONTROLLER_ROOT / "pyproject.toml").is_file():
        raise RuntimeError(
            "全局 issue-solver 仅支持可编辑安装。请执行："
            "uv tool install --editable <issue-solver 项目路径>"
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


def _prompt_review_rollback(
    result: dict[str, Any],
    reporter: TerminalReporter,
) -> None:
    if not result.get("rollback_prompt_required"):
        return

    interactive = sys.stdin.isatty()
    should_rollback = False
    decision = "KEEP_NON_INTERACTIVE"
    if interactive:
        while True:
            try:
                answer = input(
                    "Review 失败，是否回滚到 base commit？[y/N] "
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
    rollback_error = None
    if should_rollback:
        try:
            rolled_back = rollback_state_to_base(
                result,
                result.get("rollback_reason", result.get("error", "Review 失败。")),
            )
            rollback_success = rolled_back["success"]
            rollback_error = rolled_back["error"]
        except Exception as exc:
            rollback_error = str(exc)
        if rollback_success:
            result["changed_files"] = []
            reporter.notice("✓ 已回滚到 base commit。")
        else:
            reporter.notice(f"✗ 回滚失败：{rollback_error}", error=True)
            result["error"] = (
                f"{result.get('error', 'Review 失败。')}；"
                f"回滚失败：{rollback_error}"
            )

    try:
        write_round_artifact(
            run_dir=result["run_dir"],
            kind="rollback_decision",
            stage="REVIEW",
            repair_round=max(result.get("repair_round", 1), 1),
            payload={
                "decision": decision,
                "interactive": interactive,
                "rollback_success": rollback_success,
                "rollback_error": rollback_error,
            },
        )
    except Exception as exc:
        result["error"] = (
            f"{result.get('error', 'Review 失败。')}；"
            f"记录回滚决策失败：{exc}"
        )


def _worktree_status(result: dict[str, Any]) -> str | None:
    if result.get("changed_files"):
        return "保留修改"
    if result.get("rollback_prompt_required") or result.get("rollback_required"):
        return "已回滚"
    return None


def run_command(
    args: argparse.Namespace,
    *,
    reporter: TerminalReporter,
    global_mode: bool = False,
) -> int:
    """运行当前已实现的最小 StateGraph。"""

    if global_mode:
        _require_editable_installation()

    repo_path = Path(args.repo).expanduser() if args.repo else Path.cwd()
    if not repo_path.is_dir():
        raise ValueError(f"仓库路径不存在或不是目录：{repo_path}")

    repo_root = find_repo_root(repo_path)
    if repo_root.resolve() == CONTROLLER_ROOT.resolve():
        raise ValueError("禁止将 issue-solver 控制程序仓库作为目标测试仓库。")

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
    )
    report_session = RunReportSession(
        run_dir=run_dir,
        model_name=model_name,
        reporter=reporter,
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
        reporter.preflight_failed()
        error = f"目标仓库环境预检失败：{exc}"
        try:
            write_run_artifact(
                run_dir=run_dir,
                kind="failure_environment",
                stage="INITIALIZE",
                payload={
                    "reason": error,
                    "llm_called": False,
                    "dependencies_installed": False,
                    "worktree_modified": False,
                },
            )
        except Exception as artifact_exc:
            error = f"{error}；记录环境失败日志失败：{artifact_exc}"
        reporter.error_block(
            "环境预检失败",
            [
                ("原因", error),
                ("安全状态", "未安装依赖、未调用 LLM、未修改工作区"),
            ],
        )
        reporter.set_outcome(
            success=False,
            phase="INITIALIZE",
            worktree_status="未修改",
        )
        report_session.finish(
            state={
                **report_state,
                "status": "FAILED",
                "error": error,
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
            raise ValueError("配置 MODEL_NAME 不能为空。")
        if not setting.API_KEY:
            raise ValueError("配置 API_KEY 不能为空。")
        if not setting.BASE_URL:
            raise ValueError("配置 BASE_URL 不能为空。")
        model = ReasoningChatDeepSeek(
            model=model_name,
            api_key=setting.API_KEY,
            base_url=setting.BASE_URL,
        )
        graph = build_graph(model).compile()

        initial_state = {
            **report_state,
            "max_cycles": args.max_cycles or setting.MAX_CYCLES,
            "test_timeout": args.test_timeout or setting.TEST_TIMEOUT,
            "test_tail_lines": args.test_tail_lines or setting.TEST_TAIL_LINES,
            "explore_reports": [],
            "explore_errors": [],
            "test_results": [],
            "latest_test_results": [],
            "explore_stage_call": 0,
            "coding_stage_call": 0,
        }
        result = None
        reporter.graph_started()

        for mode, event in graph.stream(
            initial_state,
            stream_mode=["updates", "values"],
        ):
            if mode == "values":
                result = event
                report_state = dict(event)
                continue
            for node, update in event.items():
                reporter.handle_update(node, update)

        if result is None:
            raise RuntimeError("工作流未返回最终状态。")

        if result.get("status") == "FAILED":
            _prompt_review_rollback(result, reporter)
            worktree_status = _worktree_status(result)
            reporter.error_block(
                "运行失败",
                [("原因", result.get("error", "工作流未提供失败原因。"))],
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
        report_session.finish(
            state={
                **report_state,
                "status": "FAILED",
                "error": str(exc),
            },
            model=model,
            worktree_status=_worktree_status(report_state) or "未知",
        )
        raise
