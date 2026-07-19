import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from config import Setting
from graph.builder import build_graph
from nodes.finalize import rollback_state_to_base
from schemas.environment_info import EnvironmentInfo
from services.artifacts import write_round_artifact, write_run_artifact
from services.deepseek_model import ReasoningChatDeepSeek
from services.python_environment import discover_python_environment
from services.repository import find_repo_root
from services.run_store import create_run_id


CONTROLLER_ROOT = Path(__file__).resolve().parents[1]


def positive_int(value: str) -> int:
    """将命令行参数解析为正整数。"""

    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是整数。") from exc

    if number < 1:
        raise argparse.ArgumentTypeError("必须大于 0。")

    return number


def positive_float(value: str) -> float:
    """将命令行参数解析为正数。"""

    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是数字。") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0。")
    return number


def build_parser(*, global_mode: bool = False) -> argparse.ArgumentParser:
    """创建 issue-solver 命令行解析器。"""

    parser = argparse.ArgumentParser(
        prog="issue-solver" if global_mode else "python -m cli.commands",
        description="运行 issue-solver 最小工作流。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="解析 Issue 并探索目标仓库。",
    )
    run_parser.add_argument(
        "--repo",
        required=not global_mode,
        help=(
            "目标 Git 仓库路径。"
            if not global_mode
            else "目标 Git 仓库路径；省略时自动使用当前目录所在仓库。"
        ),
    )
    run_parser.add_argument(
        "--issue",
        required=True,
        help="Issue 文本、GitHub Issue URL 或本地 .md/.txt 绝对路径。",
    )
    run_parser.add_argument("--model", help="覆盖环境变量中的模型名称。")
    run_parser.add_argument(
        "--max-cycles",
        type=positive_int,
        help="覆盖 .env 中的 MAX_CYCLES。",
    )
    run_parser.add_argument(
        "--test-timeout",
        type=positive_float,
        help="覆盖 .env 中的 TEST_TIMEOUT，单位为秒。",
    )
    run_parser.add_argument(
        "--test-tail-lines",
        type=positive_int,
        help="覆盖 .env 中的 TEST_TAIL_LINES。",
    )
    run_parser.add_argument(
        "--run-root",
        help="覆盖 .env 中的 RUN_ROOT（相对路径基于 issue-solver 项目根目录）。",
    )
    run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="隐藏执行过程，只显示最终摘要。",
    )

    return parser


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


def _prompt_review_rollback(result: dict) -> None:
    if not result.get("rollback_prompt_required"):
        return

    interactive = sys.stdin.isatty()
    should_rollback = False
    decision = "KEEP_NON_INTERACTIVE"
    if interactive:
        while True:
            try:
                answer = input("Review 失败，是否回滚到 base commit？[y/N] ").strip().lower()
            except EOFError:
                answer = ""
            if answer in {"", "n", "no"}:
                decision = "KEEP"
                break
            if answer in {"y", "yes"}:
                decision = "ROLLBACK"
                should_rollback = True
                break
            print("请输入 Y 或 N。")

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
            print("已回滚到 base commit。")
        else:
            print(f"回滚失败：{rollback_error}", file=sys.stderr)
            result["error"] = f"{result.get('error', 'Review 失败。')}；回滚失败：{rollback_error}"

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
        result["error"] = f"{result.get('error', 'Review 失败。')}；记录回滚决策失败：{exc}"


def run_command(args: argparse.Namespace, *, global_mode: bool = False) -> int:
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
    run_id = create_run_id()
    configured_run_root = args.run_root or setting.RUN_ROOT
    run_root = _resolve_run_root(
        repo_root,
        configured_run_root,
        CONTROLLER_ROOT,
    )
    run_dir = (
        run_root / repo_root.name / run_id
    )
    run_dir.mkdir(parents=True)

    try:
        environment = _preflight_environment(repo_root, run_dir)
    except Exception as exc:
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
        print(f"[失败] {error}", file=sys.stderr)
        print("未安装任何依赖、未调用 LLM、未修改工作区。", file=sys.stderr)
        print(f"运行 ID：{run_id}", file=sys.stderr)
        print(f"运行目录：{run_dir}", file=sys.stderr)
        return 1

    model_name = args.model or setting.MODEL_NAME
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
        "run_id": run_id,
        "phase": "INITIALIZE",
        "status": "RUNNING",
        "cycle": 0,
        "repo_path": str(repo_root),
        "run_dir": str(run_dir),
        "issue_input": args.issue,
        "max_cycles": args.max_cycles or setting.MAX_CYCLES,
        "test_timeout": args.test_timeout or setting.TEST_TIMEOUT,
        "test_tail_lines": args.test_tail_lines or setting.TEST_TAIL_LINES,
        "environment": environment,
        "explore_reports": [],
        "explore_errors": [],
        "test_results": [],
        "latest_test_results": [],
        "explore_stage_call": 0,
        "coding_stage_call": 0,
    }
    result = None
    repair_round = 1
    explore_stage_call = 0
    coding_stage_call = 0
    explore_total = 0
    explore_completed = 0

    if not args.quiet:
        print("[开始] 初始化仓库")

    for mode, event in graph.stream(
        initial_state,
        stream_mode=["updates", "values"],
    ):
        if mode == "values":
            result = event
            continue
        if args.quiet:
            continue

        for node, update in event.items():
            if node == "initialize":
                if update.get("status") == "FAILED":
                    print(f"[失败] 初始化仓库：{update.get('error', '未知错误')}")
                    continue

                project_type = update.get("project_type", "unknown")
                test_commands = update.get("test_commands", [])
                selected_environment = update.get("environment", environment)
                test_text = (
                    f"，测试命令 {'; '.join(test_commands)}"
                    if test_commands
                    else ""
                )
                print(f"[完成] 初始化仓库：{project_type}{test_text}")
                print(
                    f"[环境] {selected_environment.kind}："
                    f"{selected_environment.python_executable}"
                )
                print("[开始] 解析 Issue")

            elif node == "parse_issue":
                if update.get("status") == "FAILED":
                    print(f"[失败] 解析 Issue：{update.get('error', '未知错误')}")
                    continue

                issue = update.get("issue")
                print(f"[完成] Issue：{getattr(issue, 'title', '未知标题')}")
                print("[开始] Coordinator 决策")

            elif node == "coordinator":
                next_action = update.get("next_action")
                if update.get("status") == "FAILED" or next_action == "FAILED":
                    print(
                        f"[失败] Coordinator："
                        f"{update.get('error', update.get('current_summary', '未知错误'))}"
                    )
                elif next_action == "EXPLORE":
                    repair_round = update.get("repair_round", repair_round)
                    explore_stage_call = update.get(
                        "explore_stage_call",
                        explore_stage_call + 1,
                    )
                    focuses = update.get("explore_focuses", [])
                    explore_total = len(focuses)
                    explore_completed = 0
                    print(
                        f"[决策 r{repair_round:02d}/"
                        f"s{explore_stage_call:02d}] 并行探索，"
                        f"共 {explore_total} 个任务"
                    )
                    for index, focus in enumerate(focuses, start=1):
                        print(f"  [{index}] {focus}")
                elif next_action == "CODE":
                    repair_round = update.get("repair_round", repair_round)
                    coding_stage_call = update.get(
                        "coding_stage_call",
                        coding_stage_call + 1,
                    )
                    task = update.get("coding_task")
                    objective = getattr(task, "objective", "准备编码")
                    print(
                        f"[决策 r{repair_round:02d}/"
                        f"s{coding_stage_call:02d}] CODE：{objective}"
                    )
                    print(
                        f"[开始 r{repair_round:02d}/"
                        f"s{coding_stage_call:02d}] Coding Agent"
                    )
                elif next_action == "FINISH":
                    print("[决策] FINISH：工作流完成")

            elif node == "explore":
                explore_completed += 1
                progress = f"{explore_completed}/{explore_total or '?'}"
                errors = update.get("explore_errors", [])
                if errors:
                    print(
                        f"[探索 r{repair_round:02d}/"
                        f"s{explore_stage_call:02d}/"
                        f"i{explore_completed:02d} {progress}] "
                        f"失败：{'；'.join(errors)}"
                    )
                else:
                    reports = update.get("explore_reports", [])
                    focus = (
                        getattr(reports[0], "focus", "未知目标")
                        if reports
                        else "未知目标"
                    )
                    print(
                        f"[探索 r{repair_round:02d}/"
                        f"s{explore_stage_call:02d}/"
                        f"i{explore_completed:02d} {progress}] 完成：{focus}"
                    )

                if explore_total and explore_completed == explore_total:
                    print("[开始] Coordinator 汇总探索结果")

            elif node == "coding":
                coding_round = update.get("repair_round", repair_round)
                coding_call = update.get(
                    "coding_stage_call",
                    coding_stage_call,
                )
                iteration = update.get("coding_iteration", 0)
                coordinate = (
                    f"r{coding_round:02d}/s{coding_call:02d}/i{iteration:02d}"
                )
                if update.get("status") == "FAILED":
                    print(
                        f"[失败 {coordinate}] Coding："
                        f"{update.get('error', '未知错误')}"
                    )
                else:
                    coding_result = update.get("coding_result")
                    summary = getattr(coding_result, "summary", "修改完成")
                    print(f"[完成 {coordinate}] Coding：{summary}")
                    changed_files = update.get("changed_files", [])
                    if changed_files:
                        print(f"  修改文件：{', '.join(changed_files)}")
                    print(f"[开始 r{coding_round:02d}] Review Agent")

            elif node == "review":
                review_round = update.get("repair_round", repair_round)
                if update.get("status") == "FAILED":
                    print(
                        f"[失败 r{review_round:02d}] Review："
                        f"{update.get('error', '未知错误')}"
                    )
                else:
                    review_result = update.get("review_result")
                    verdict = getattr(review_result, "verdict", "UNKNOWN")
                    print(f"[完成 r{review_round:02d}] Review：{verdict}")
                    for issue in getattr(review_result, "issues", []):
                        print(f"  问题：{issue}")
                    print(f"[开始 r{review_round:02d}] 执行真实测试")

            elif node == "test":
                test_round = update.get("repair_round", repair_round)
                if update.get("status") == "FAILED":
                    print(
                        f"[失败 r{test_round:02d}] Test："
                        f"{update.get('error', '未知错误')}"
                    )
                else:
                    for index, test_result in enumerate(
                        update.get("latest_test_results", []),
                        start=1,
                    ):
                        resolved_command = " ".join(
                            getattr(
                                test_result,
                                "resolved_command",
                                [test_result.command],
                            )
                        )
                        print(
                            f"[测试 r{test_round:02d}/i{index:02d}] "
                            f"{test_result.status}：{resolved_command} "
                            f"({test_result.duration:.2f}s)"
                        )
                        print(f"  stdout：{test_result.stdout_path}")
                        print(f"  stderr：{test_result.stderr_path}")
                    print("[开始] Coordinator 综合 Review/Test")

            elif node == "finalize":
                if update.get("status") == "FINISHED":
                    print(f"[完成] 最终 Patch：{update.get('diff_path', '未知路径')}")
                elif update.get("changed_files") == []:
                    print("[完成] 失败修改已回滚到 base commit")
                else:
                    print(
                        f"[失败] Finalize："
                        f"{update.get('error', '运行失败，工作区已保留')}"
                    )

    if result is None:
        raise RuntimeError("工作流未返回最终状态。")

    if result.get("status") == "FAILED":
        _prompt_review_rollback(result)
        print(
            f"运行失败：{result.get('error', '工作流未提供失败原因。')}",
            file=sys.stderr,
        )
        print(f"运行 ID：{run_id}", file=sys.stderr)
        print(f"运行目录：{run_dir}", file=sys.stderr)
        return 1

    print(f"运行 ID：{run_id}")
    print(f"当前阶段：{result.get('phase', 'UNKNOWN')}")
    if result.get("next_action") and result.get("phase") != "REVIEW":
        print(f"下一动作：{result['next_action']}")
    print(f"运行目录：{run_dir}")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    global_mode: bool = False,
) -> int:
    """解析参数并返回适合命令行使用的退出码。"""

    parser = build_parser(global_mode=global_mode)
    args = parser.parse_args(argv)

    if args.command == "run":
        started = time.monotonic()
        try:
            return run_command(args, global_mode=global_mode)
        except Exception as exc:
            print(f"运行失败：{exc}", file=sys.stderr)
            return 1
        finally:
            print(f"最终耗时：{time.monotonic() - started:.2f} 秒")

    parser.error(f"未知命令：{args.command}")


def global_main() -> int:
    """供安装后的 issue-solver 控制台命令调用。"""

    return main(global_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
