import shutil
import shlex
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO

from schemas.environment_info import EnvironmentInfo


def _display_width(value: str) -> int:
    width = 0
    for character in value:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
    return width


def _pad_display(value: str, width: int) -> str:
    return value + " " * max(width - _display_width(value), 0)


def _wrap_display(value: object, width: int) -> list[str]:
    text = str(value)
    if width < 1:
        return [text]
    lines: list[str] = []
    current = ""
    current_width = 0
    for character in text:
        if character == "\n":
            lines.append(current)
            current = ""
            current_width = 0
            continue
        character_width = _display_width(character)
        if current and current_width + character_width > width:
            lines.append(current)
            current = ""
            current_width = 0
        current += character
        current_width += character_width
    lines.append(current)
    return lines


def _compact_targeted_test_command(command: str) -> str:
    try:
        arguments = shlex.split(command, posix=True)
    except ValueError:
        return command
    if len(arguments) < 3 or arguments[:2] != ["pytest", "-q"]:
        return command

    targets = arguments[2:]
    files = list(
        dict.fromkeys(
            target.split("::", 1)[0].replace("\\", "/")
            for target in targets
        )
    )
    if len(files) == 1:
        scope = files[0]
    elif len(files) == 2:
        scope = " ".join(files)
    else:
        scope = f"{files[0]} 等 {len(files)} 个文件"

    count = f"（{len(targets)} 项）" if len(targets) > 1 else ""
    return f"pytest -q {scope}{count}"


class TerminalReporter:
    """将工作流事件渲染为稳定、无 ANSI 的分层终端文本。"""

    def __init__(
        self,
        *,
        quiet: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
        width: int | None = None,
        leading_blank: bool = False,
    ) -> None:
        detected_width = width or shutil.get_terminal_size(fallback=(72, 24)).columns
        self.width = max(48, min(detected_width, 88))
        self.quiet = quiet
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.clock = clock
        self.leading_blank = leading_blank
        self.started: dict[str, float] = {}

        self.model_name = "未配置"
        self.run_id: str | None = None
        self.repo_path: str | None = None
        self.run_dir: str | None = None
        self.status = "失败"
        self.phase: str | None = None
        self.next_action: str | None = None
        self.repair_round = 0
        self.changed_files: list[str] = []
        self.test_results: list[Any] = []
        self.worktree_status: str | None = None

        self.visible_round = 0
        self.explore_total = 0
        self.explore_completed = 0
        self.explore_stage_call = 0
        self.coding_stage_call = 0

    def _write(self, value: str = "", *, error: bool = False) -> None:
        target = self.stderr if error else self.stdout
        if self.leading_blank:
            print(file=target)
            self.leading_blank = False
            if not value:
                return
        print(value, file=target)

    def _progress(self, value: str = "") -> None:
        if not self.quiet:
            self._write(value)

    def _rule(self, title: str | None = None) -> str:
        if not title:
            return "─" * self.width
        label = f" {title} "
        remaining = max(self.width - _display_width(label), 0)
        left = remaining // 2
        return "─" * left + label + "─" * (remaining - left)

    def _detail(self, label: str, value: object, *, indent: int = 2) -> None:
        if self.quiet:
            return
        prefix = " " * indent + "│ " + _pad_display(label, 10)
        continuation = " " * indent + "│ " + " " * 10
        available = max(self.width - _display_width(prefix), 1)
        lines = _wrap_display(value, available)
        self._write(f"{prefix}{lines[0]}")
        for line in lines[1:]:
            self._write(f"{continuation}{line}")

    def _compact_detail(
        self,
        label: str,
        value: object,
        *,
        indent: int = 2,
    ) -> None:
        if self.quiet:
            return
        label_text = f"{label}："
        prefix = " " * indent + "│ " + label_text
        continuation = (
            " " * indent + "│ " + " " * _display_width(label_text)
        )
        available = max(self.width - _display_width(prefix), 1)
        lines = _wrap_display(value, available)
        self._write(f"{prefix}{lines[0]}")
        for line in lines[1:]:
            self._write(f"{continuation}{line}")

    def _repo_relative_path(self, value: str | Path) -> str:
        if not self.repo_path:
            return str(value)
        try:
            return str(Path(value).relative_to(Path(self.repo_path)))
        except ValueError:
            return str(value)

    def _duration(self, key: str) -> float:
        started = self.started.pop(key, None)
        if started is None:
            return 0.0
        return max(self.clock() - started, 0.0)

    def start_timing(self, key: str) -> None:
        self.started[key] = self.clock()

    def begin_run(
        self,
        *,
        model_name: str | None,
        run_id: str,
        repo_path: str | Path,
        run_dir: str | Path,
    ) -> None:
        self.model_name = model_name or "未配置"
        self.run_id = run_id
        self.repo_path = str(repo_path)
        self.run_dir = str(run_dir)
        if self.quiet:
            return
        self._write(f"issue-solver · {self.model_name} · {run_id}")
        self._write(self._rule())
        self._write(f"目标仓库  {self.repo_path}")
        self._write()

    def preflight_succeeded(self, environment: EnvironmentInfo) -> None:
        elapsed = self._duration("preflight")
        self._progress(f"✓ 环境预检 · {elapsed:.2f} 秒")
        self._detail("环境", environment.kind)
        self._detail(
            "解释器",
            self._repo_relative_path(environment.python_executable),
        )
        self._progress()

    def preflight_failed(self) -> None:
        elapsed = self._duration("preflight")
        self._progress(f"✗ 环境预检失败 · {elapsed:.2f} 秒")

    def graph_started(self) -> None:
        self.start_timing("initialize")

    def _ensure_round(self, repair_round: int) -> None:
        if repair_round <= 0 or repair_round == self.visible_round:
            return
        self.visible_round = repair_round
        self.repair_round = max(self.repair_round, repair_round)
        self._progress()
        self._progress(f"◆ 修复轮次 r{repair_round:02d}")
        self._progress()

    def _coordinator_result(
        self,
        action: str,
        elapsed: float,
        *,
        repair_round: int,
    ) -> None:
        self._ensure_round(repair_round)
        self._progress("  → Coordinator")
        self._progress(f"  ✓ {action} · {elapsed:.2f} 秒")

    def handle_update(
        self,
        node: str,
        update: dict[str, Any],
    ) -> None:
        if node == "initialize":
            self._handle_initialize(update)
        elif node == "parse_issue":
            self._handle_parse_issue(update)
        elif node == "coordinator":
            self._handle_coordinator(update)
        elif node == "explore":
            self._handle_explore(update)
        elif node == "coding":
            self._handle_coding(update)
        elif node == "review":
            self._handle_review(update)
        elif node == "test":
            self._handle_test(update)
        elif node == "finalize":
            self._handle_finalize(update)

    def _handle_initialize(self, update: dict[str, Any]) -> None:
        elapsed = self._duration("initialize")
        if update.get("status") == "FAILED":
            self._progress(f"✗ 初始化仓库失败 · {elapsed:.2f} 秒")
            self._detail("原因", update.get("error", "未知错误"))
            return

        self._progress(f"✓ 初始化仓库 · {elapsed:.2f} 秒")
        self._detail("项目类型", update.get("project_type", "unknown"))
        test_commands = update.get("test_commands", [])
        if test_commands:
            self._detail("全量测试", "; ".join(test_commands))
        self._progress()
        self.start_timing("parse_issue")

    def _handle_parse_issue(self, update: dict[str, Any]) -> None:
        elapsed = self._duration("parse_issue")
        if update.get("status") == "FAILED":
            self._progress(f"✗ 解析 Issue 失败 · {elapsed:.2f} 秒")
            self._detail("原因", update.get("error", "未知错误"))
            return

        issue = update.get("issue")
        self._progress(f"✓ 解析 Issue · {elapsed:.2f} 秒")
        self._detail("标题", getattr(issue, "title", "未知标题"))
        self.start_timing("coordinator")

    def _handle_coordinator(self, update: dict[str, Any]) -> None:
        elapsed = self._duration("coordinator")
        action = update.get("next_action")
        repair_round = update.get("repair_round", self.repair_round or 1)
        if update.get("status") == "FAILED" or action == "FAILED":
            self._ensure_round(repair_round)
            self._progress(f"  ✗ Coordinator 失败 · {elapsed:.2f} 秒")
            self._detail(
                "原因",
                update.get("error", update.get("current_summary", "未知错误")),
                indent=4,
            )
            self.start_timing("finalize")
            return

        if action == "EXPLORE":
            self._coordinator_result("EXPLORE", elapsed, repair_round=repair_round)
            focuses = update.get("explore_focuses", [])
            self._detail("探索任务", len(focuses), indent=4)
            self.explore_total = len(focuses)
            self.explore_completed = 0
            self.explore_stage_call = update.get(
                "explore_stage_call",
                self.explore_stage_call + 1,
            )
            self._progress()
            self._progress(f"  → Explore s{self.explore_stage_call:02d}")
            self.start_timing("explore")
        elif action == "CODE":
            self._coordinator_result("CODE", elapsed, repair_round=repair_round)
            task = update.get("coding_task")
            self._detail(
                "目标",
                getattr(task, "objective", "准备编码"),
                indent=4,
            )
            self.coding_stage_call = update.get(
                "coding_stage_call",
                self.coding_stage_call + 1,
            )
            self._progress()
            self._progress(f"  → Coding s{self.coding_stage_call:02d}")
            self.start_timing("coding")
        elif action == "FINISH":
            self._coordinator_result("FINISH", elapsed, repair_round=repair_round)
            self.start_timing("finalize")

    def _handle_explore(self, update: dict[str, Any]) -> None:
        self.explore_completed += 1
        total = f"{self.explore_total:02d}" if self.explore_total else "?"
        progress = f"i{self.explore_completed:02d}/{total}"
        errors = update.get("explore_errors", [])
        if errors:
            self._progress(f"    ✗ {progress}  {'；'.join(errors)}")
        else:
            reports = update.get("explore_reports", [])
            focus = (
                getattr(reports[0], "focus", "未知目标")
                if reports
                else "未知目标"
            )
            self._progress(f"    ✓ {progress}  {focus}")

        if self.explore_total and self.explore_completed == self.explore_total:
            elapsed = self._duration("explore")
            self._progress(
                f"  ✓ Explore s{self.explore_stage_call:02d} 完成 · {elapsed:.2f} 秒"
            )
            self.start_timing("coordinator")

    def _handle_coding(self, update: dict[str, Any]) -> None:
        elapsed = self._duration("coding")
        repair_round = update.get("repair_round", self.repair_round or 1)
        self._ensure_round(repair_round)
        stage_call = update.get("coding_stage_call", self.coding_stage_call or 1)
        iteration = update.get("coding_iteration", 0)
        if update.get("status") == "FAILED":
            self._progress(
                f"  ✗ Coding s{stage_call:02d}/i{iteration:02d} 失败 · {elapsed:.2f} 秒"
            )
            self._detail("原因", update.get("error", "未知错误"), indent=4)
            return

        result = update.get("coding_result")
        self._progress(f"  ✓ Coding s{stage_call:02d} 完成 · {elapsed:.2f} 秒")
        self._detail("摘要", getattr(result, "summary", "修改完成"), indent=4)
        changed_files = update.get("changed_files", [])
        if changed_files:
            self._detail("修改文件", len(changed_files), indent=4)
            for path in changed_files:
                self._progress(f"      │ {path}")
        self._progress()
        self._progress("  → Review")
        self.start_timing("review")

    def _handle_review(self, update: dict[str, Any]) -> None:
        elapsed = self._duration("review")
        if update.get("status") == "FAILED":
            self._progress(f"  ✗ Review 失败 · {elapsed:.2f} 秒")
            self._detail("原因", update.get("error", "未知错误"), indent=4)
            return

        result = update.get("review_result")
        verdict = getattr(result, "verdict", "UNKNOWN")
        marker = "✓" if verdict == "APPROVE" else "✗"
        self._progress(f"  {marker} {verdict} · {elapsed:.2f} 秒")
        for issue in getattr(result, "issues", []):
            self._detail("问题", issue, indent=4)
        self._progress()
        self._progress("  → Test")
        self.start_timing("test")

    def _handle_test(self, update: dict[str, Any]) -> None:
        elapsed = self._duration("test")
        if update.get("status") == "FAILED":
            self._progress(f"  ✗ Test 失败 · {elapsed:.2f} 秒")
            self._detail("原因", update.get("error", "未知错误"), indent=4)
            return

        results = update.get("latest_test_results", [])
        for index, result in enumerate(results, start=1):
            marker = "✓" if result.status == "PASSED" else "✗"
            test_type = "定向测试" if index == 1 else "全量回归"
            self._progress(
                f"    {marker} i{index:02d}  {test_type} · "
                f"{result.status} · {result.duration:.2f} 秒"
            )
            command = (
                _compact_targeted_test_command(result.command)
                if index == 1
                else result.command
            )
            self._compact_detail("命令", command, indent=6)
        all_passed = results and all(item.status == "PASSED" for item in results)
        marker = "✓" if all_passed else "✗"
        self._progress(f"  {marker} Test 完成 · {elapsed:.2f} 秒")
        self.start_timing("coordinator")

    def _handle_finalize(self, update: dict[str, Any]) -> None:
        elapsed = self._duration("finalize")
        self._progress()
        if update.get("status") == "FINISHED":
            self._progress(f"✓ Finalize · {elapsed:.2f} 秒")
            self._detail("最终 Patch", update.get("diff_path", "未知路径"))
        elif update.get("changed_files") == []:
            self._progress(f"✓ Finalize · {elapsed:.2f} 秒")
            self._detail("工作区", "失败修改已回滚到 base commit")
        else:
            self._progress(f"✗ Finalize 失败 · {elapsed:.2f} 秒")
            self._detail("原因", update.get("error", "运行失败，工作区已保留"))

    def error_block(self, title: str, details: Iterable[tuple[str, object]]) -> None:
        self._write(f"✗ {title}", error=True)
        for label, value in details:
            self._write(f"  │ {label}：{value}", error=True)

    def notice(self, value: str, *, error: bool = False) -> None:
        self._write(value, error=error)

    def set_outcome(
        self,
        *,
        success: bool,
        result: dict[str, Any] | None = None,
        phase: str | None = None,
        worktree_status: str | None = None,
    ) -> None:
        state = result or {}
        self.status = "成功" if success else "失败"
        self.phase = phase or state.get("phase") or self.phase
        self.next_action = state.get("next_action", self.next_action)
        self.repair_round = max(
            self.repair_round,
            state.get("repair_round", state.get("cycle", 0)),
        )
        self.changed_files = list(state.get("changed_files", self.changed_files))
        self.test_results = list(
            state.get("latest_test_results", self.test_results)
        )
        self.run_dir = str(state.get("run_dir", self.run_dir or "")) or None
        self.worktree_status = worktree_status

    def summary(self, *, total_tokens: int, total_duration: float) -> None:
        self._write()
        self._write(self._rule("运行摘要"))
        self._summary_item("状态", self.status)
        self._summary_item("模型", self.model_name)
        if self.run_id:
            self._summary_item("运行 ID", self.run_id)
        if self.repair_round:
            self._summary_item("修复轮次", self.repair_round)
        if self.phase:
            self._summary_item("当前阶段", self.phase)
        if self.next_action and self.phase != "REVIEW":
            self._summary_item("下一动作", self.next_action)
        if self.changed_files:
            self._summary_item("修改文件", len(self.changed_files))
        if self.test_results:
            passed = sum(item.status == "PASSED" for item in self.test_results)
            self._summary_item(
                "测试结果",
                f"{passed}/{len(self.test_results)} PASSED",
            )
        if self.worktree_status:
            self._summary_item("工作区", self.worktree_status)
        self._summary_item("总 Token", f"{total_tokens:,}")
        self._summary_item("最终耗时", f"{total_duration:.2f} 秒")
        if self.run_dir:
            self._summary_item("运行目录", self.run_dir)
        self._write(self._rule())

    def _summary_item(self, label: str, value: object) -> None:
        prefix = _pad_display(label, 10)
        available = max(self.width - _display_width(prefix), 1)
        lines = _wrap_display(value, available)
        self._write(f"{prefix}{lines[0]}")
        for line in lines[1:]:
            self._write(f"{' ' * 10}{line}")
