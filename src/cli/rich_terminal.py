from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Literal, TextIO

from rich import box
from rich.cells import cell_len
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from cli.terminal import TerminalReporter
from config import Setting
from schemas.environment_info import EnvironmentInfo
from schemas.explore_execution import ExploreExecution
from schemas.failure import FailureInfo, make_failure
from services.report import ReportResult
from services.token_usage import TokenUsageSummary


SPINNER_REFRESH_PER_SECOND = 15
StepStatus = Literal["pending", "running", "success", "failure", "warning", "info"]

_TECHNICAL_ENVIRONMENT_FAILURES = (
    "无法检查 Git 工作区状态",
    "无法获取当前 Commit",
    "无法确认目标虚拟环境是否被 Git ignore",
)


def _is_actionable_warning(failure: FailureInfo) -> bool:
    if failure.type == "ENVIRONMENT":
        return not any(
            message in failure.message for message in _TECHNICAL_ENVIRONMENT_FAILURES
        )
    return failure.type == "SAFETY" and "Git 工作区存在未提交修改" in failure.message


@dataclass
class DisplayStep:
    key: str
    label: str
    status: StepStatus = "pending"
    duration: float | None = None
    detail: str | None = None
    detail_separator: str = " — "
    label_style: str | None = None
    single_line: bool = False
    children: list[DisplayStep] = field(default_factory=list)
    spinner: Spinner = field(
        default_factory=lambda: Spinner("dots", style="bold"),
        repr=False,
    )


@dataclass
class DisplayGroup:
    title: str
    kind: Literal[
        "setup",
        "parser",
        "coordinator",
        "explore",
        "code",
        "review",
        "tests",
        "finalize",
        "report",
        "terminal",
    ]
    stage_call: int = 0
    steps: list[DisplayStep] = field(default_factory=list)
    committed: bool = False


@dataclass
class DisplayRound:
    number: int
    groups: list[DisplayGroup] = field(default_factory=list)
    header_committed: bool = False


class RichTerminalReporter(TerminalReporter):
    """使用简约 Rich 树状视图渲染交互式终端输出。"""

    _STAGE_LABELS = {
        "preflight": "Check environment",
        "initialize": "Initialize repository",
        "parse_issue": "Parse issue",
        "coordinator": "Coordinator",
        "explore": "Explore",
        "coding": "Generate and apply patch",
        "review": "Review changes",
        "test": "Run tests",
        "finalize": "Finalize",
        "report": "Generate report",
    }

    def __init__(
        self,
        *,
        quiet: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        clock: Callable[[], float] = monotonic,
        width: int | None = None,
        leading_blank: bool = False,
        force_terminal: bool | None = None,
    ) -> None:
        output = stdout or sys.stdout
        errors = stderr or sys.stderr
        super().__init__(
            quiet=quiet,
            stdout=output,
            stderr=errors,
            clock=clock,
            width=width,
            leading_blank=False,
        )
        rich_width = self.width
        console_options = {
            "width": rich_width,
            "color_system": "auto",
            "force_terminal": force_terminal,
            "force_interactive": force_terminal,
            "legacy_windows": False if force_terminal else None,
            "markup": False,
            "highlight": False,
        }
        self._console = Console(file=output, **console_options)
        self._error_console = Console(file=errors, **console_options)
        self._leading_blank = leading_blank
        self._live: Live | None = None
        self._batch_depth = 0
        self._current_key: str | None = None
        self._current_label: str | None = None
        self._current_step: DisplayStep | None = None
        self._current_spinner = Spinner("dots", style="bold")
        self._durations: dict[str, float] = {}
        self._internal_errors: list[str] = []

        self._setup_steps = {
            key: DisplayStep(key, label)
            for key, label in (
                ("preflight", "Check environment"),
                ("initialize", "Initialize repository"),
                ("parse_issue", "Parse issue"),
            )
        }
        self._setup_group = DisplayGroup(
            title="System · Setup",
            kind="setup",
            steps=[
                self._setup_steps["preflight"],
                self._setup_steps["initialize"],
            ],
        )
        self._parser_group = DisplayGroup(
            title="Agent · PARSER",
            kind="parser",
            steps=[self._setup_steps["parse_issue"]],
        )
        self._rounds: list[DisplayRound] = []
        self._explore_groups: dict[tuple[int, int], DisplayGroup] = {}
        self._code_groups: dict[tuple[int, int], DisplayGroup] = {}
        self._review_groups: dict[tuple[int, int], DisplayGroup] = {}
        self._test_groups: dict[tuple[int, int], DisplayGroup] = {}
        self._finalize_group: DisplayGroup | None = None
        self._report_group: DisplayGroup | None = None
        self._report_agent_expected: bool | None = None
        self._terminal_steps: list[DisplayStep] = []
        self._terminal_group = DisplayGroup(
            title="",
            kind="terminal",
            steps=self._terminal_steps,
        )

    def _write(self, value: str = "", *, error: bool = False) -> None:
        if error and value:
            self._internal_errors.append(value)

    def _ensure_leading_blank(self, console: Console) -> None:
        if self._leading_blank:
            console.print()
            self._leading_blank = False

    def begin_run(
        self,
        *,
        model_name: str | None,
        run_id: str,
        repo_path: str | Path,
        run_dir: str | Path,
        run_root: str | Path | None = None,
    ) -> None:
        self.model_name = model_name or "未配置"
        self.run_id = run_id
        self.repo_path = str(repo_path)
        self.run_dir = str(run_dir)
        self.run_root_display = str(run_root) if run_root is not None else None
        if self.quiet:
            return
        self._ensure_leading_blank(self._console)
        self._console.print(self._render_header())
        self._live = Live(
            console=self._console,
            get_renderable=self._render_dashboard,
            refresh_per_second=SPINNER_REFRESH_PER_SECOND,
            transient=True,
            redirect_stdout=False,
            redirect_stderr=False,
            vertical_overflow="crop",
        )
        self._live.start(refresh=True)

    def _stage_label(self, key: str) -> str:
        if key == "explore" and self.explore_stage_call:
            return f"Explore s{self.explore_stage_call:02d}"
        if key == "coding" and self.coding_stage_call:
            return f"Coding s{self.coding_stage_call:02d}"
        return self._STAGE_LABELS.get(key, key)

    def start_timing(self, key: str) -> None:
        super().start_timing(key)
        self._current_key = key
        self._current_label = self._stage_label(key)
        self._current_spinner = Spinner("dots", style="bold")
        if key == "test":
            self._ensure_test_group()
        elif key == "finalize":
            self._ensure_finalize_group()
        self._sync_running_step()
        self._refresh()

    def _duration(self, key: str) -> float:
        duration = super()._duration(key)
        self._durations[key] = duration
        if self._current_key == key:
            self._current_key = None
            self._current_label = None
            self._current_step = None
        return duration

    def preflight_succeeded(self, environment: EnvironmentInfo) -> None:
        super().preflight_succeeded(environment)
        self._complete_setup("preflight", True)

    def preflight_failed(self, failure: FailureInfo | None = None) -> None:
        super().preflight_failed(failure)
        self._complete_setup("preflight", False, failure=failure)

    def _complete_setup(
        self,
        key: str,
        success: bool,
        *,
        failure: FailureInfo | None = None,
    ) -> None:
        step = self._setup_steps[key]
        if success:
            step.status = "success"
        elif failure is not None and _is_actionable_warning(failure):
            step.status = "warning"
        else:
            step.status = "failure"
        step.duration = self._durations.get(key, 0.0)
        self._sync_running_step()
        self._refresh()

    def handle_update(self, node: str, update: dict[str, Any]) -> None:
        self._batch_depth += 1
        try:
            super().handle_update(node, update)
            self._apply_update(node, update)
            self._sync_running_step()
        finally:
            self._batch_depth -= 1
        self._refresh()

    def _apply_update(self, node: str, update: dict[str, Any]) -> None:
        if node in {"initialize", "parse_issue"}:
            succeeded = update.get("status") != "FAILED"
            failure = None if succeeded else self._failure_info(update)
            self._complete_setup(node, succeeded, failure=failure)
            if node == "parse_issue" and update.get("status") != "FAILED":
                issue = update.get("issue")
                title = getattr(issue, "title", None)
                if title:
                    self._setup_steps["parse_issue"].detail = str(title)
                    self._setup_steps["parse_issue"].single_line = True
        elif node == "coordinator":
            self._apply_coordinator(update)
        elif node == "explore":
            self._apply_explore(update)
        elif node == "coding":
            self._apply_coding(update)
        elif node == "review":
            self._apply_review(update)
        elif node == "test":
            self._apply_test(update)
        elif node == "finalize":
            self._apply_finalize(update)

    def _apply_finalize(self, update: dict[str, Any]) -> None:
        step = self._ensure_finalize_group().steps[0]
        step.duration = self._durations.get("finalize", 0.0)
        if update.get("status") == "FINISHED":
            step.label = "Save final patch and artifacts"
            step.status = "success"
        elif update.get("rollback_success"):
            step.label = "Roll back changes and save artifacts"
            step.status = "success"
        elif not update.get("rollback_required"):
            step.label = (
                "Preserve changes and save artifacts"
                if update.get("changed_files")
                else "Save failure artifacts"
            )
            step.status = "success"
        else:
            step.label = "Finalize workflow"
            step.status = self._failure_status(update)
            step.detail = self._failure_message(update)

    def _ensure_round(self, number: int) -> DisplayRound:
        for display_round in self._rounds:
            if display_round.number == number:
                return display_round
        display_round = DisplayRound(number)
        self._rounds.append(display_round)
        self._rounds.sort(key=lambda item: item.number)
        return display_round

    def _apply_coordinator(self, update: dict[str, Any]) -> None:
        repair_round = update.get("repair_round", self.repair_round or 1)
        action = update.get("next_action")
        display_round = self._ensure_round(repair_round)
        coordinator_step = DisplayStep(
            key=f"coordinator:{len(display_round.groups) + 1}",
            label="Coordinate workflow",
            duration=self._durations.get("coordinator", 0.0),
        )
        if update.get("status") == "FAILED" or action == "FAILED":
            coordinator_step.status = self._failure_status(update)
            coordinator_step.detail = self._failure_message(update)
            display_round.groups.append(
                DisplayGroup(
                    title="Agent · COORDINATOR",
                    kind="coordinator",
                    steps=[coordinator_step],
                )
            )
            return
        if action == "EXPLORE":
            focuses = list(update.get("explore_focuses", []))
            titles = list(update.get("explore_titles", []))
            if len(titles) != len(focuses):
                titles = [
                    f"Explore task {index:02d}"
                    for index in range(1, len(focuses) + 1)
                ]
            stage_call = update.get("explore_stage_call", self.explore_stage_call or 1)
            coordinator_step.label = f"Plan {len(focuses)} exploration tasks"
            coordinator_step.status = "success"
            display_round.groups.append(
                DisplayGroup(
                    title="Agent · COORDINATOR",
                    kind="coordinator",
                    stage_call=stage_call,
                    steps=[coordinator_step],
                )
            )
            group = DisplayGroup(
                title="Agent · EXPLORERS",
                kind="explore",
                stage_call=stage_call,
                steps=[
                    DisplayStep(
                        key=f"explore:{repair_round}:{stage_call}:{index}",
                        label=title,
                        label_style="grey70",
                        single_line=True,
                    )
                    for index, title in enumerate(titles, start=1)
                ],
            )
            display_round.groups.append(group)
            self._explore_groups[(repair_round, stage_call)] = group
        elif action == "CODE":
            stage_call = update.get("coding_stage_call", self.coding_stage_call or 1)
            task = update.get("coding_task")
            task_name = " ".join(
                str(getattr(task, "objective", "准备编码")).split()
            )
            coordinator_step.label = "Plan coding task"
            coordinator_step.status = "success"
            display_round.groups.append(
                DisplayGroup(
                    title="Agent · COORDINATOR",
                    kind="coordinator",
                    stage_call=stage_call,
                    steps=[coordinator_step],
                )
            )
            group = DisplayGroup(
                title="Agent · CODER",
                kind="code",
                stage_call=stage_call,
                steps=[
                    DisplayStep(
                        "coding_task",
                        "Read coding task",
                        status="success",
                        detail=task_name,
                        single_line=True,
                    ),
                    DisplayStep("coding", "Generate and apply patch"),
                ],
            )
            display_round.groups.append(group)
            self._code_groups[(repair_round, stage_call)] = group
        elif action == "FINISH":
            coordinator_step.label = "Complete repair"
            coordinator_step.status = "success"
            display_round.groups.append(
                DisplayGroup(
                    title="Agent · COORDINATOR",
                    kind="coordinator",
                    steps=[coordinator_step],
                )
            )

    def _apply_explore(self, update: dict[str, Any]) -> None:
        for value in update.get("explore_executions", []):
            execution = ExploreExecution.model_validate(value)
            group = self._explore_groups.get(
                (execution.repair_round, execution.stage_call)
            )
            if group is None:
                group = DisplayGroup(
                    title="Agent · EXPLORERS",
                    kind="explore",
                    stage_call=execution.stage_call,
                )
                self._ensure_round(execution.repair_round).groups.append(group)
                self._explore_groups[(execution.repair_round, execution.stage_call)] = (
                    group
                )
            while len(group.steps) < execution.item_index:
                index = len(group.steps) + 1
                group.steps.append(
                    DisplayStep(
                        f"explore:{execution.repair_round}:"
                        f"{execution.stage_call}:{index}",
                        f"Explore task {index}",
                        label_style="grey70",
                        single_line=True,
                    )
                )
            step = group.steps[execution.item_index - 1]
            step.label = execution.title or f"Explore task {execution.item_index:02d}"
            step.label_style = "grey70"
            step.single_line = True
            step.status = (
                "success"
                if execution.status == "PASSED"
                else (
                    "warning"
                    if execution.failure is not None
                    and _is_actionable_warning(execution.failure)
                    else "failure"
                )
            )
            step.duration = execution.duration
            if execution.failure is not None:
                step.detail = execution.failure.message

    def _latest_code_group(self) -> DisplayGroup | None:
        return next(
            (
                group
                for display_round in reversed(self._rounds)
                for group in reversed(display_round.groups)
                if group.kind == "code"
            ),
            None,
        )

    def _latest_review_group(self) -> DisplayGroup | None:
        return next(
            (
                group
                for display_round in reversed(self._rounds)
                for group in reversed(display_round.groups)
                if group.kind == "review"
            ),
            None,
        )

    def _apply_coding(self, update: dict[str, Any]) -> None:
        group = self._latest_code_group()
        if group is None:
            return
        patch_step = next(step for step in group.steps if step.key == "coding")
        patch_step.duration = self._durations.get("coding", 0.0)
        if update.get("status") == "FAILED":
            patch_step.status = self._failure_status(update)
            patch_step.detail = self._failure_message(update)
            return
        patch_step.status = "success"
        changed_files = list(update.get("changed_files", []))
        for path in changed_files:
            patch_step.children.append(
                DisplayStep(
                    key=f"changed:{path}",
                    label="Changed",
                    status="info",
                    detail=str(path),
                    detail_separator=":",
                    label_style="",
                ),
            )
        repair_round = update.get(
            "repair_round",
            self.visible_round or self.repair_round or 1,
        )
        stage_call = update.get(
            "coding_stage_call",
            self.coding_stage_call or group.stage_call or 1,
        )
        review_group = DisplayGroup(
            title="Agent · REVIEWER",
            kind="review",
            stage_call=stage_call,
            steps=[DisplayStep("review", "Review changes")],
        )
        self._ensure_round(repair_round).groups.append(review_group)
        self._review_groups[(repair_round, stage_call)] = review_group

    def _apply_review(self, update: dict[str, Any]) -> None:
        group = self._latest_review_group()
        if group is None:
            return
        step = next(item for item in group.steps if item.key == "review")
        step.duration = self._durations.get("review", 0.0)
        if update.get("status") == "FAILED":
            step.status = self._failure_status(update)
            step.detail = self._failure_message(update)
            return
        result = update.get("review_result")
        verdict = getattr(result, "verdict", "UNKNOWN")
        step.status = "success" if verdict == "APPROVE" else "failure"
        step.label = "Review changes"

    def _ensure_test_group(self) -> DisplayGroup:
        repair_round = self.visible_round or self.repair_round or 1
        stage_call = self.coding_stage_call or 1
        coordinates = (repair_round, stage_call)
        group = self._test_groups.get(coordinates)
        if group is not None:
            return group
        group = DisplayGroup(
            title="System · Tests",
            kind="tests",
            stage_call=stage_call,
            steps=[
                DisplayStep("targeted_test", "Run targeted tests"),
                DisplayStep("full_test", "Run full regression"),
            ],
        )
        self._ensure_round(repair_round).groups.append(group)
        self._test_groups[coordinates] = group
        return group

    def _find_latest_test_group(self) -> DisplayGroup | None:
        return next(
            (
                item
                for display_round in reversed(self._rounds)
                for item in reversed(display_round.groups)
                if item.kind == "tests"
            ),
            None,
        )

    def _latest_test_group(self) -> DisplayGroup:
        return self._find_latest_test_group() or self._ensure_test_group()

    def _ensure_finalize_group(self) -> DisplayGroup:
        if self._finalize_group is None:
            self._finalize_group = DisplayGroup(
                title="System · Finalize",
                kind="finalize",
                steps=[DisplayStep("finalize", "Finalize workflow")],
            )
        return self._finalize_group

    def _ensure_report_group(
        self,
        *,
        agent_expected: bool | None = None,
    ) -> DisplayGroup:
        if agent_expected is not None:
            self._report_agent_expected = agent_expected
        uses_agent = self._report_agent_expected is not False
        title = "Agent · REPORTER" if uses_agent else "System · Report"
        label = "Generate report" if uses_agent else "Generate fallback report"
        if self._report_group is None:
            self._report_group = DisplayGroup(
                title=title,
                kind="report",
                steps=[DisplayStep("report", label)],
            )
        else:
            self._report_group.title = title
            self._report_group.steps[0].label = label
        return self._report_group

    def _apply_test(self, update: dict[str, Any]) -> None:
        group = self._latest_test_group()
        if update.get("status") == "FAILED":
            group.steps[0].status = self._failure_status(update)
            group.steps[0].detail = self._failure_message(update)
            group.steps[0].duration = self._durations.get("test", 0.0)
            group.steps[1].status = "info"
            group.steps[1].detail = "Skipped"
            return
        results = list(update.get("latest_test_results", []))
        test_steps = group.steps
        for index, result in enumerate(results):
            if index >= len(test_steps):
                step = DisplayStep(f"test:{index}", f"Test {index + 1}")
                group.steps.append(step)
                test_steps.append(step)
            step = test_steps[index]
            step.status = "success" if result.status == "PASSED" else "failure"
            step.duration = result.duration
        for step in test_steps[len(results) :]:
            step.status = "info"
            step.detail = "Skipped"

    @staticmethod
    def _failure_info(update: dict[str, Any]) -> FailureInfo:
        return FailureInfo.model_validate(
            update.get("rollback_failure")
            or update.get("failure")
            or make_failure("INTERNAL", "未知错误。")
        )

    @classmethod
    def _failure_message(cls, update: dict[str, Any]) -> str:
        return cls._failure_info(update).message

    @classmethod
    def _failure_status(
        cls,
        update: dict[str, Any],
    ) -> Literal["failure", "warning"]:
        return (
            "warning"
            if _is_actionable_warning(cls._failure_info(update))
            else "failure"
        )

    def _append_terminal_failure(
        self,
        label: str,
        reason: str,
        *,
        status: Literal["failure", "warning"] = "failure",
    ) -> None:
        self._terminal_steps.append(
            DisplayStep(
                key=f"terminal:{len(self._terminal_steps)}",
                label=f"{label} — {reason}",
                status=status,
                duration=self._durations.get(label.lower()),
            )
        )
        self._terminal_group.committed = False

    def _sync_running_step(self) -> None:
        self._current_step = None
        key = self._current_key
        if key in self._setup_steps:
            step = self._setup_steps[key]
            if step.status == "pending":
                step.status = "running"
            self._current_step = step
            return
        if key == "explore":
            group = self._explore_groups.get(
                (
                    self.visible_round or self.repair_round or 1,
                    self.explore_stage_call or 1,
                )
            )
            if group is not None:
                for step in group.steps:
                    if step.status == "pending":
                        step.status = "running"
            return
        if key in {"coding", "review"}:
            group = (
                self._latest_code_group()
                if key == "coding"
                else self._latest_review_group()
            )
            if group is not None:
                step = next(
                    (item for item in group.steps if item.key == key),
                    None,
                )
                if step is not None:
                    if step.status == "pending":
                        step.status = "running"
                    self._current_step = step
            return
        if key == "test":
            group = self._latest_test_group()
            step = next(
                (
                    item
                    for item in group.steps
                    if item.key != "report" and item.status == "pending"
                ),
                None,
            )
            if step is not None:
                step.status = "running"
                self._current_step = step
            return
        if key == "finalize":
            step = self._ensure_finalize_group().steps[0]
            if step.status == "pending":
                step.status = "running"
            self._current_step = step
            return
        if key == "report":
            step = self._ensure_report_group().steps[0]
            if step.status == "pending":
                step.status = "running"
            self._current_step = step

    def prepare_for_prompt(self) -> None:
        self._mark_current_failure("运行失败，等待回滚决定")
        self._stop_live()

    def _mark_current_failure(
        self,
        reason: str,
        *,
        status: Literal["failure", "warning"] = "failure",
    ) -> None:
        if self._current_step is not None:
            self._current_step.status = status
            self._current_step.label = f"{self._current_step.label} — {reason}"
        elif self._current_label:
            self._append_terminal_failure(
                self._current_label,
                reason,
                status=status,
            )
        self._current_key = None
        self._current_label = None
        self._current_step = None
        self._refresh()

    def error_block(self, title: str, details: Iterable[tuple[str, object]]) -> None:
        detail_items = list(details)
        reason = next(
            (str(value) for label, value in detail_items if label == "原因"),
            title,
        )
        failure = self._failure_from_details(detail_items)
        status = (
            "warning"
            if failure is not None and _is_actionable_warning(failure)
            else "failure"
        )
        self._mark_current_failure(reason, status=status)
        self._stop_live()
        self._ensure_leading_blank(self._error_console)
        grid = Table.grid(padding=(0, 1))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(ratio=1, overflow="fold")
        for label, value in detail_items:
            grid.add_row(Text(f"{label}："), Text(str(value)))
        marker = "⚠" if status == "warning" else "✗"
        marker_style = "bold yellow" if status == "warning" else "bold red"
        panel_title = Text(" ")
        panel_title.append(marker, style=marker_style)
        panel_title.append(f" {title} ", style="bold")
        self._error_console.print(
            Panel(
                grid,
                title=panel_title,
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    @staticmethod
    def _failure_from_details(
        details: list[tuple[str, object]],
    ) -> FailureInfo | None:
        values = {label: value for label, value in details}
        if not {"类型", "原因", "建议"} <= values.keys():
            return None
        try:
            return FailureInfo.model_validate(
                {
                    "type": str(values["类型"]),
                    "message": str(values["原因"]),
                    "suggestion": str(values["建议"]),
                }
            )
        except ValueError:
            return None

    def notice(self, value: str, *, error: bool = False) -> None:
        console = self._error_console if error else self._console
        self._ensure_leading_blank(console)
        rendered = Text()
        if value.startswith(("✓", "✗", "⚠")):
            marker = value[0]
            style = {
                "✓": "bold green",
                "✗": "bold red",
                "⚠": "bold yellow",
            }[marker]
            rendered.append(marker, style=style)
            rendered.append(value[1:], style="bold")
        else:
            rendered.append(value)
        console.print(rendered)

    def report_started(self, *, agent_expected: bool) -> None:
        self._report_agent_expected = agent_expected
        self._ensure_report_group(agent_expected=agent_expected)
        super().report_started(agent_expected=agent_expected)

    def report_completed(
        self,
        result: ReportResult,
        *,
        agent_attempted: bool | None = None,
    ) -> None:
        super().report_completed(result, agent_attempted=agent_attempted)
        if agent_attempted is None:
            agent_attempted = not result.fallback_used
        group = self._ensure_report_group(agent_expected=agent_attempted)
        report_step = group.steps[0]
        report_step.duration = self._durations.get("report", 0.0)
        if result.path is None:
            report_step.status = "failure"
            report_step.detail = (
                result.failure.message if result.failure else "报告保存失败"
            )
        elif result.fallback_used:
            report_step.status = "warning"
            report_step.detail = "Fallback template"
        else:
            report_step.status = "success"
        self._refresh()

    def _refresh(self) -> None:
        if self._batch_depth or self._live is None:
            return
        self._commit_ready_groups()
        self._live.refresh()

    def _stop_live(self) -> bool:
        was_live = self._live is not None
        groups = self._uncommitted_visible_groups()
        committed = self._mark_groups_committed(groups) if groups else None
        if self._live is not None:
            self._live.refresh()
            self._live.stop()
            self._live = None
        if committed is not None:
            self._console.print(committed)
        return was_live

    @staticmethod
    def _status_renderable(step: DisplayStep) -> RenderableType:
        if step.status == "running":
            return step.spinner
        marker = {
            "pending": "·",
            "success": "✓",
            "failure": "✗",
            "warning": "⚠",
            "info": "",
        }[step.status]
        style = {
            "success": "bold green",
            "failure": "bold red",
            "warning": "bold yellow",
        }.get(step.status, "bold")
        if step.status in {"pending", "info"}:
            style = "dim"
        return Text(marker, style=style)

    def _render_steps(
        self,
        steps: list[DisplayStep],
        *,
        tree: bool,
    ) -> RenderableType:
        table = Table.grid(expand=True, padding=0)
        table.add_column(width=6 if tree else 2, no_wrap=True)
        table.add_column(width=2, no_wrap=True)
        table.add_column(ratio=1, overflow="fold")
        table.add_column(width=10, justify="right", no_wrap=True)
        content_width = max(
            self._console.width - (6 if tree else 2) - 2 - 10,
            1,
        )
        for index, step in enumerate(steps):
            prefix = "  "
            if tree:
                prefix = "  └──" if index == len(steps) - 1 else "  ├──"
            duration = f"{step.duration:.2f}s" if step.duration is not None else ""
            label_style = step.label_style
            if label_style is None:
                label_style = "dim" if step.status == "info" else ""
            label = Text(step.label, style=label_style)
            if step.detail is not None:
                label.append(step.detail_separator, style="dim")
                label.append(step.detail, style="grey70")
            if step.single_line:
                label.truncate(content_width, overflow="ellipsis")
            else:
                label = self._fold_text(label, content_width)
            table.add_row(
                Text(prefix, style="dim"),
                self._status_renderable(step),
                label,
                Text(duration, style="dim"),
            )
        return table

    def _render_code_steps(self, steps: list[DisplayStep]) -> RenderableType:
        renderables: list[RenderableType] = []
        for index, step in enumerate(steps):
            is_last = index == len(steps) - 1
            prefix = "  └──" if is_last else "  ├──"
            renderables.append(
                self._render_step_row(step, prefix=prefix, prefix_width=6)
            )
            for child_index, child in enumerate(step.children):
                child_is_last = child_index == len(step.children) - 1
                branch = "└──" if child_is_last else "├──"
                continuation = "    " if is_last else "│   "
                renderables.append(
                    self._render_step_row(
                        child,
                        prefix=f"  {continuation}{branch}",
                        prefix_width=10,
                        show_marker=False,
                    )
                )
        return Group(*renderables)

    def _render_step_row(
        self,
        step: DisplayStep,
        *,
        prefix: str,
        prefix_width: int,
        show_marker: bool = True,
    ) -> RenderableType:
        marker_width = 2 if show_marker else 0
        content_width = max(
            self._console.width - prefix_width - marker_width - 10,
            1,
        )
        label_style = step.label_style
        if label_style is None:
            label_style = "dim" if step.status == "info" else ""
        label = Text(step.label, style=label_style)
        if step.detail is not None:
            label.append(step.detail_separator, style="dim")
            label.append(step.detail, style="grey70")
        if step.single_line:
            label.truncate(content_width, overflow="ellipsis")
        else:
            label = self._fold_text(label, content_width)
        duration = f"{step.duration:.2f}s" if step.duration is not None else ""

        table = Table.grid(expand=True, padding=0)
        table.add_column(width=prefix_width, no_wrap=True)
        if show_marker:
            table.add_column(width=marker_width, no_wrap=True)
        table.add_column(ratio=1, overflow="fold")
        table.add_column(width=10, justify="right", no_wrap=True)
        row: list[RenderableType] = [Text(prefix, style="dim")]
        if show_marker:
            row.append(self._status_renderable(step))
        row.extend([label, Text(duration, style="dim")])
        table.add_row(*row)
        return table

    @staticmethod
    def _fold_text(value: Text, width: int) -> Text:
        """按终端单元格宽度折行，避免长标识符整体移到下一行。"""

        offsets: list[int] = []
        current_width = 0
        for index, character in enumerate(value.plain):
            if character == "\n":
                current_width = 0
                continue
            character_width = cell_len(character)
            if current_width and current_width + character_width > width:
                offsets.append(index)
                current_width = 0
            current_width += character_width
        if not offsets:
            return value

        wrapped = Text()
        for index, line in enumerate(value.divide(offsets)):
            if index:
                wrapped.append("\n")
            wrapped.append_text(line)
        return wrapped

    def _render_header(self) -> RenderableType:
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(width=12, no_wrap=True, style="dim")
        grid.add_column(ratio=1, overflow="fold")
        grid.add_row("Model", Text(self.model_name, style="bold"))
        grid.add_row("Repository", Text(self.repo_path or "未设置"))
        grid.add_row("Run ID", Text(self.run_id or "未分配"))
        return Panel(
            grid,
            title=Text(" Issue Solver ", style="bold"),
            box=box.ROUNDED,
            padding=(0, 1),
            expand=True,
        )

    def _ordered_groups(self) -> list[tuple[DisplayRound | None, DisplayGroup]]:
        groups: list[tuple[DisplayRound | None, DisplayGroup]] = [
            (None, self._setup_group),
            (None, self._parser_group),
        ]
        groups.extend(
            (display_round, group)
            for display_round in self._rounds
            for group in display_round.groups
        )
        if self._terminal_steps:
            groups.append((None, self._terminal_group))
        if self._finalize_group is not None:
            groups.append((None, self._finalize_group))
        if self._report_group is not None:
            groups.append((None, self._report_group))
        return groups

    @staticmethod
    def _group_started(group: DisplayGroup) -> bool:
        return any(step.status != "pending" for step in group.steps)

    @staticmethod
    def _group_completed(group: DisplayGroup) -> bool:
        return bool(group.steps) and all(
            step.status not in {"pending", "running"} for step in group.steps
        )

    @staticmethod
    def _group_title(group: DisplayGroup) -> str:
        if group.kind != "explore":
            return group.title
        role = "EXPLORER" if len(group.steps) == 1 else "EXPLORERS"
        return f"Agent · {len(group.steps)} {role}"

    def _render_group_block(
        self,
        display_round: DisplayRound | None,
        group: DisplayGroup,
        *,
        include_round_header: bool,
    ) -> RenderableType:
        renderables: list[RenderableType] = [Text("")]
        if display_round is not None and include_round_header:
            renderables.extend(
                [
                    Text(
                        f"◆ Repair Round {display_round.number:02d}",
                        style="bold",
                    ),
                    Text(""),
                ]
            )
        if group.kind == "terminal":
            renderables.append(self._render_steps(group.steps, tree=False))
        else:
            renderables.extend(
                [
                    Text(f"  {self._group_title(group)}", style="bold"),
                    (
                        self._render_code_steps(group.steps)
                        if group.kind == "code"
                        else self._render_steps(group.steps, tree=True)
                    ),
                ]
            )
        return Group(*renderables)

    def _render_group_blocks(
        self,
        groups: list[tuple[DisplayRound | None, DisplayGroup]],
    ) -> RenderableType:
        renderables: list[RenderableType] = []
        rendered_rounds: set[int] = set()
        for display_round, group in groups:
            include_round_header = (
                display_round is not None
                and not display_round.header_committed
                and display_round.number not in rendered_rounds
            )
            renderables.append(
                self._render_group_block(
                    display_round,
                    group,
                    include_round_header=include_round_header,
                )
            )
            if include_round_header and display_round is not None:
                rendered_rounds.add(display_round.number)
        return Group(*renderables)

    def _mark_groups_committed(
        self,
        groups: list[tuple[DisplayRound | None, DisplayGroup]],
    ) -> RenderableType:
        renderables: list[RenderableType] = []
        for display_round, group in groups:
            include_round_header = (
                display_round is not None and not display_round.header_committed
            )
            renderables.append(
                self._render_group_block(
                    display_round,
                    group,
                    include_round_header=include_round_header,
                )
            )
            group.committed = True
            if include_round_header and display_round is not None:
                display_round.header_committed = True
        return Group(*renderables)

    def _commit_ready_groups(self) -> None:
        ordered = self._ordered_groups()
        started_indices = [
            index
            for index, (_, group) in enumerate(ordered)
            if not group.committed and self._group_started(group)
        ]
        last_started = max(started_indices, default=-1)
        ready: list[tuple[DisplayRound | None, DisplayGroup]] = []
        for index, item in enumerate(ordered):
            _, group = item
            if group.committed:
                continue
            if not self._group_completed(group):
                break
            if index < last_started or self._current_label is not None:
                ready.append(item)
                continue
            break
        if not ready:
            return

        committed = self._mark_groups_committed(ready)
        if self._live is not None:
            self._live.refresh()
        self._console.print(committed)

    def _uncommitted_visible_groups(
        self,
    ) -> list[tuple[DisplayRound | None, DisplayGroup]]:
        if self.quiet:
            return []
        return [
            item
            for item in self._ordered_groups()
            if not item[1].committed
            and (
                item[1] is self._setup_group
                or self._group_started(item[1])
            )
        ]

    def _render_dashboard(self) -> RenderableType:
        groups = self._uncommitted_visible_groups()
        renderables: list[RenderableType] = []
        if groups:
            renderables.append(self._render_group_blocks(groups))
        has_running_step = any(
            step.status == "running" for _, group in groups for step in group.steps
        )
        if self._current_label and not has_running_step:
            current = Table.grid(expand=True)
            current.add_column(width=4)
            current.add_column(width=2)
            current.add_column(ratio=1)
            current.add_row("  ", self._current_spinner, self._current_label)
            renderables.append(current)
        return Group(*renderables)

    def _render_summary(
        self,
        token_usage: TokenUsageSummary,
        total_duration: float,
    ) -> RenderableType:
        if self.status == "成功":
            title = "Success"
            rows: list[tuple[str, object]] = [
                ("Changed files", len(self.changed_files)),
                ("Repair rounds", self.repair_round),
                ("Tests", self._result_test_summary()),
                (
                    "Tokens",
                    f"{token_usage.input_tokens:,} input · "
                    f"{token_usage.output_tokens:,} output",
                ),
                ("Duration", f"{total_duration:.2f}s"),
                ("Run directory", self._display_run_directory()),
            ]
        else:
            title = "Failed"
            rows = [
                ("Failed phase", self.phase or "UNKNOWN"),
                (
                    "Reason",
                    self.failure.message if self.failure is not None else "未知错误",
                ),
                ("Worktree", self.worktree_status or "未知"),
                ("Repair rounds", self.repair_round),
                ("Tests", self._result_test_summary()),
                (
                    "Tokens",
                    f"{token_usage.input_tokens:,} input · "
                    f"{token_usage.output_tokens:,} output",
                ),
                ("Duration", f"{total_duration:.2f}s"),
            ]
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(width=16, no_wrap=True, style="dim")
        grid.add_column(ratio=1, overflow="fold")
        for label, value in rows:
            grid.add_row(label, Text(str(value)))
        return Panel(
            grid,
            title=Text(f" {title} ", style="bold"),
            box=box.ROUNDED,
            padding=(0, 1),
            expand=True,
        )

    def _display_run_directory(self) -> str:
        if not self.repo_path or not self.run_id:
            return "未生成"
        configured_root = Path(self.run_root_display or Setting().RUN_ROOT).as_posix()
        root = configured_root.rstrip("/") or configured_root
        return f"{root}/{Path(self.repo_path).name}/{self.run_id}"

    def _result_test_summary(self) -> str:
        if not self.test_results:
            return "not run"
        passed = sum(result.status == "PASSED" for result in self.test_results)
        return f"{passed}/{len(self.test_results)} passed"

    def summary(
        self,
        *,
        token_usage: TokenUsageSummary,
        total_duration: float,
    ) -> None:
        self._stop_live()
        super().summary(token_usage=token_usage, total_duration=total_duration)
        if self._leading_blank:
            self._ensure_leading_blank(self._console)
        else:
            self._console.print()
        self._console.print(self._render_summary(token_usage, total_duration))
        for error in self._internal_errors:
            self._error_console.print(Text(error, style="bold"))


def create_terminal_reporter(
    *,
    quiet: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    clock: Callable[[], float] = monotonic,
    width: int | None = None,
    leading_blank: bool = False,
    force_interactive: bool | None = None,
) -> TerminalReporter:
    """根据输出环境选择 Rich 树状视图或稳定纯文本。"""

    output = stdout or sys.stdout
    probe = Console(
        file=output,
        force_terminal=force_interactive,
        force_interactive=force_interactive,
        color_system=None,
    )
    if probe.is_interactive:
        return RichTerminalReporter(
            quiet=quiet,
            stdout=output,
            stderr=stderr,
            clock=clock,
            width=width,
            leading_blank=leading_blank,
            force_terminal=force_interactive,
        )
    return TerminalReporter(
        quiet=quiet,
        stdout=output,
        stderr=stderr,
        clock=clock,
        width=width,
        leading_blank=leading_blank,
    )
