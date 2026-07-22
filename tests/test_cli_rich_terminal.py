import re
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from cli.rich_terminal import RichTerminalReporter, create_terminal_reporter
from cli.terminal import TerminalReporter
from schemas.environment_info import EnvironmentInfo
from schemas.explore_execution import ExploreExecution
from schemas.failure import make_failure
from services.report import ReportResult
from services.token_usage import TokenUsageSummary


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def token_usage() -> TokenUsageSummary:
    return TokenUsageSummary(
        total_tokens=984_801,
        input_tokens=900_000,
        output_tokens=84_801,
        cache_read_tokens=0,
        role_usages=(),
    )


def plain(output: StringIO) -> str:
    return Text.from_ansi(output.getvalue()).plain


def make_reporter(
    *,
    quiet: bool = False,
    width: int = 100,
) -> tuple[RichTerminalReporter, StringIO, StringIO, FakeClock]:
    output = StringIO()
    errors = StringIO()
    clock = FakeClock()
    reporter = RichTerminalReporter(
        quiet=quiet,
        stdout=output,
        stderr=errors,
        clock=clock,
        width=width,
        force_terminal=True,
    )
    return reporter, output, errors, clock


def begin(reporter: RichTerminalReporter) -> None:
    reporter.begin_run(
        model_name="deepseek-v4-flash",
        run_id="run_01KY_test",
        repo_path="E:/workspace/cachetools",
        run_dir="E:/runs/run_01KY_test",
        run_root=".issue-solver-runs",
    )


def explore_execution(
    *,
    item_index: int,
    focus: str,
    duration: float,
    stage_call: int = 1,
    title: str | None = None,
) -> ExploreExecution:
    return ExploreExecution(
        repair_round=1,
        stage_call=stage_call,
        item_index=item_index,
        focus=focus,
        title=title or focus,
        status="PASSED",
        duration=duration,
    )


def test_factory_selects_rich_only_for_interactive_output() -> None:
    output = StringIO()

    fallback = create_terminal_reporter(
        stdout=output,
        force_interactive=False,
    )
    interactive = create_terminal_reporter(
        stdout=StringIO(),
        stderr=StringIO(),
        force_interactive=True,
    )

    assert type(fallback) is TerminalReporter
    assert isinstance(interactive, RichTerminalReporter)


def test_noninteractive_factory_preserves_plain_output() -> None:
    output = StringIO()
    reporter = create_terminal_reporter(
        stdout=output,
        force_interactive=False,
        width=72,
    )

    reporter.begin_run(
        model_name="test-model",
        run_id="run_test",
        repo_path="E:/repo",
        run_dir="E:/runs/run_test",
    )

    assert output.getvalue().splitlines()[0] == ("issue-solver · test-model · run_test")
    assert "\x1b" not in output.getvalue()


def test_running_step_reuses_spinner_so_live_refresh_advances_frames() -> None:
    reporter, _, _, _ = make_reporter()
    reporter.start_timing("preflight")
    step = reporter._setup_steps["preflight"]

    first_renderable = reporter._status_renderable(step)
    second_renderable = reporter._status_renderable(step)

    assert first_renderable is step.spinner
    assert second_renderable is step.spinner
    assert step.spinner.render(0.0).plain != step.spinner.render(0.1).plain


def test_live_refresh_does_not_reprint_header_in_short_terminal() -> None:
    reporter, output, _, _ = make_reporter(width=88)
    reporter._console._height = 8
    begin(reporter)

    assert reporter._live is not None
    for _ in range(5):
        reporter._live.refresh()
    reporter._stop_live()

    assert plain(output).count("Issue Solver") == 1


def test_completed_setup_is_not_part_of_later_live_refreshes() -> None:
    reporter, output, _, _ = make_reporter()
    begin(reporter)
    reporter.start_timing("preflight")
    reporter.preflight_succeeded(
        EnvironmentInfo(
            kind="VENV",
            root_path="E:/workspace/cachetools/.venv",
            python_executable="E:/workspace/cachetools/.venv/Scripts/python.exe",
            pytest_version="pytest 9.1.1",
            source=".venv",
        )
    )
    reporter.graph_started()
    reporter.handle_update("initialize", {"status": "RUNNING"})

    checkpoint = len(output.getvalue())
    assert reporter._live is not None
    reporter._live.refresh()
    refreshed = plain(StringIO(output.getvalue()[checkpoint:]))

    assert "Issue Solver" not in refreshed
    assert "System · Setup" not in refreshed
    assert "Agent · PARSER" in refreshed


def test_explore_content_folds_at_cell_boundary_instead_of_word_boundary() -> None:
    reporter, _, _, _ = make_reporter()
    value = Text(
        "分析 tests/test_search.py 的测试结构，特别关注 "
        "test_search_ignores_case 的预期断言"
    )

    folded = reporter._fold_text(value, 48)
    lines = folded.plain.splitlines()

    assert len(lines) > 1
    assert cell_len(lines[0]) == 48
    assert all(cell_len(line) <= 48 for line in lines)
    assert "".join(lines) == value.plain


@pytest.mark.parametrize(
    ("requested_width", "expected_width"),
    [(64, 64), (100, 100), (160, 120)],
)
def test_rich_width_adapts_at_startup(
    requested_width: int,
    expected_width: int,
) -> None:
    reporter, _, _, _ = make_reporter(width=requested_width)

    assert reporter._console.width == expected_width


def test_explore_uses_coordinator_titles_instead_of_truncated_tasks() -> None:
    reporter, _, _, _ = make_reporter(quiet=True, width=64)
    focuses = [
        "定位 `search_items(items, query)` 函数的实现位置与比较逻辑",
        "探索 `tests/test_search.py` 的现有测试内容，确认回归范围",
    ]
    titles = [
        "定位 search_items 实现与所有调用路径和大小写比较逻辑",
        "检查 test_search 现有测试",
    ]

    reporter.start_timing("coordinator")
    reporter.handle_update(
        "coordinator",
        {
            "next_action": "EXPLORE",
            "repair_round": 1,
            "explore_stage_call": 1,
            "explore_focuses": focuses,
            "explore_titles": titles,
        },
    )
    reporter.handle_update(
        "explore",
        {
            "explore_executions": [
                explore_execution(
                    item_index=2,
                    focus=focuses[1],
                    title=titles[1],
                    duration=1.0,
                )
            ]
        },
    )

    group = reporter._explore_groups[(1, 1)]
    assert [step.label for step in group.steps] == titles
    assert all("`" not in step.label for step in group.steps)
    assert all(not step.label.endswith("…") for step in group.steps)

    narrow_output = StringIO()
    Console(
        file=narrow_output,
        width=reporter._console.width,
        color_system=None,
    ).print(reporter._render_steps(group.steps, tree=True))
    narrow_rendered = narrow_output.getvalue()
    assert "…" in narrow_rendered
    assert titles[0] not in narrow_rendered
    assert len(narrow_rendered.splitlines()) == 2

    wide, _, _, _ = make_reporter(quiet=True, width=120)
    wide.start_timing("coordinator")
    wide.handle_update(
        "coordinator",
        {
            "next_action": "EXPLORE",
            "repair_round": 1,
            "explore_stage_call": 1,
            "explore_focuses": focuses,
            "explore_titles": titles,
        },
    )
    wide_group = wide._explore_groups[(1, 1)]
    wide_output = StringIO()
    Console(
        file=wide_output,
        width=wide._console.width,
        color_system=None,
    ).print(wide._render_steps(wide_group.steps, tree=True))
    assert titles[0] in wide_output.getvalue()

    fallback, _, _, _ = make_reporter(quiet=True)
    fallback.start_timing("coordinator")
    fallback.handle_update(
        "coordinator",
        {
            "next_action": "EXPLORE",
            "repair_round": 1,
            "explore_stage_call": 1,
            "explore_focuses": focuses,
        },
    )
    assert [
        step.label for step in fallback._explore_groups[(1, 1)].steps
    ] == ["Explore task 01", "Explore task 02"]


def test_rich_reporter_renders_requested_success_layout() -> None:
    reporter, output, errors, clock = make_reporter()
    begin(reporter)

    assert reporter._console.width == 100
    assert reporter._live is not None
    assert reporter._live.refresh_per_second == 15
    assert reporter._live.vertical_overflow == "crop"
    assert reporter._live.transient is True

    reporter.start_timing("preflight")
    clock.advance(0.42)
    reporter.preflight_succeeded(
        EnvironmentInfo(
            kind="VENV",
            root_path="E:/workspace/cachetools/.venv",
            python_executable=("E:/workspace/cachetools/.venv/Scripts/python.exe"),
            pytest_version="pytest 9.1.1",
            source=".venv",
        )
    )
    reporter.graph_started()
    clock.advance(0.18)
    reporter.handle_update(
        "initialize",
        {"project_type": "python", "test_commands": ["pytest -q"]},
    )
    clock.advance(2.31)
    reporter.handle_update(
        "parse_issue",
        {"issue": SimpleNamespace(title="搜索忽略大小写")},
    )
    clock.advance(0.1)
    reporter.handle_update(
        "coordinator",
        {
            "next_action": "EXPLORE",
            "repair_round": 1,
            "explore_stage_call": 1,
            "explore_focuses": [
                "Locate relevant implementation",
                "Analyze existing tests",
                "Inspect Git history",
            ],
            "explore_titles": [
                "Locate relevant implementation",
                "Analyze existing tests",
                "Inspect Git history",
            ],
        },
    )
    reporter.handle_update(
        "explore",
        {
            "explore_reports": [SimpleNamespace(focus="Analyze existing tests")],
            "explore_executions": [
                explore_execution(
                    item_index=2,
                    focus="Analyze existing tests",
                    duration=6.71,
                )
            ],
        },
    )
    reporter.handle_update(
        "explore",
        {
            "explore_reports": [
                SimpleNamespace(focus="Locate relevant implementation")
            ],
            "explore_executions": [
                explore_execution(
                    item_index=1,
                    focus="Locate relevant implementation",
                    duration=8.24,
                )
            ],
        },
    )
    reporter.handle_update(
        "explore",
        {
            "explore_reports": [SimpleNamespace(focus="Inspect Git history")],
            "explore_executions": [
                explore_execution(
                    item_index=3,
                    focus="Inspect Git history",
                    duration=7.35,
                )
            ],
        },
    )
    clock.advance(0.1)
    reporter.handle_update(
        "coordinator",
        {
            "next_action": "CODE",
            "repair_round": 1,
            "coding_stage_call": 1,
            "coding_task": SimpleNamespace(objective="修复搜索"),
        },
    )
    code_group = reporter._latest_code_group()
    assert code_group is not None
    assert [step.key for step in code_group.steps] == ["coding_task", "coding"]
    task_step = code_group.steps[0]
    assert task_step.label == "Read coding task"
    assert task_step.detail == "修复搜索"
    assert task_step.status == "success"
    assert task_step.duration is None
    assert next(step for step in code_group.steps if step.key == "coding").status == (
        "running"
    )
    clock.advance(0.5)
    reporter.handle_update(
        "coding",
        {
            "repair_round": 1,
            "coding_stage_call": 1,
            "coding_result": SimpleNamespace(summary="已修复大小写匹配"),
            "changed_files": ["src/cachetools/__init__.py"],
        },
    )
    review_group = reporter._latest_review_group()
    assert review_group is not None
    assert review_group.steps[0].status == "running"
    clock.advance(0.2)
    reporter.handle_update(
        "review",
        {"review_result": SimpleNamespace(verdict="APPROVE", issues=[])},
    )
    clock.advance(0.4)
    reporter.handle_update(
        "test",
        {
            "next_action": "FINISH",
            "latest_test_results": [
                SimpleNamespace(
                    status="PASSED",
                    duration=1.42,
                    command="pytest -q tests/test_search.py",
                ),
                SimpleNamespace(
                    status="PASSED",
                    duration=12.61,
                    command="pytest -q",
                ),
            ],
        },
    )
    assert reporter._finalize_group is not None
    assert reporter._finalize_group.steps[0].status == "running"
    clock.advance(0.3)
    reporter.handle_update(
        "finalize",
        {"status": "FINISHED", "diff_path": "E:/runs/run_test/diff.patch"},
    )
    reporter.set_outcome(
        success=True,
        result={
            "phase": "FINALIZE",
            "repair_round": 1,
            "changed_files": ["src/cachetools/__init__.py"],
            "latest_test_results": [
                SimpleNamespace(status="PASSED"),
                SimpleNamespace(status="PASSED"),
            ],
            "run_dir": "E:/runs/run_test",
            "diff_path": ".issue-solver-runs/run_test/diff.patch",
        },
        worktree_status="保留修改",
    )
    reporter.report_started(agent_expected=True)
    clock.advance(2.21)
    reporter.report_completed(
        ReportResult(path="E:/runs/run_test/report.md", fallback_used=False),
        agent_attempted=True,
    )
    assert reporter._finalize_group is not None
    assert [step.status for step in reporter._finalize_group.steps] == ["success"]
    assert round(reporter._finalize_group.steps[0].duration or 0.0, 2) == 0.3
    assert reporter._report_group is not None
    assert [step.status for step in reporter._report_group.steps] == ["success"]
    assert all(step.key != "report" for step in reporter._latest_test_group().steps)
    reporter.summary(token_usage=token_usage(), total_duration=218.62)

    rendered = plain(output)
    assert reporter._setup_steps["parse_issue"].detail == "搜索忽略大小写"
    assert all(
        step.label_style == "grey70" for step in reporter._explore_groups[(1, 1)].steps
    )
    patch_step = next(step for step in code_group.steps if step.key == "coding")
    changed_step = patch_step.children[0]
    assert changed_step.label == "Changed"
    assert changed_step.detail == "src/cachetools/__init__.py"
    assert [group.kind for group in reporter._rounds[0].groups] == [
        "coordinator",
        "explore",
        "coordinator",
        "code",
        "review",
        "tests",
    ]
    coordinator_steps = [
        group.steps[0]
        for group in reporter._rounds[0].groups
        if group.kind == "coordinator"
    ]
    assert [step.label for step in coordinator_steps] == [
        "Plan 3 exploration tasks",
        "Plan coding task",
    ]
    assert [round(step.duration or 0.0, 2) for step in coordinator_steps] == [0.1, 0.1]
    for expected in (
        "Issue Solver",
        "Model",
        "deepseek-v4-flash",
        "Repository",
        "E:/workspace/cachetools",
        "Run ID",
        "run_01KY_test",
        "System · Setup",
        "Agent · PARSER",
        "◆ Repair Round 01",
        "Check environment",
        "Initialize repository",
        "Parse issue",
        "搜索忽略大小写",
        "Agent · COORDINATOR",
        "Plan 3 exploration tasks",
        "Agent · 3 EXPLORERS",
        "Locate relevant implementation",
        "Analyze existing tests",
        "Inspect Git history",
        "8.24s",
        "6.71s",
        "7.35s",
        "Plan coding task",
        "Agent · CODER",
        "Read coding task — 修复搜索",
        "Generate and apply patch",
        "Changed:src/cachetools/__init__.py",
        "Agent · REVIEWER",
        "Review changes",
        "System · Tests",
        "Run targeted tests",
        "Run full regression",
        "1.42s",
        "12.61s",
        "System · Finalize",
        "Save final patch and artifacts",
        "Agent · REPORTER",
        "Generate report",
        "2.21s",
        "Success",
        "Changed files",
        "Repair rounds",
        "Tests",
        "2/2 passed",
        "Tokens",
        "900,000 input · 84,801 output",
        "Duration",
        "218.62s",
        "Run directory",
        ".issue-solver-runs/cachetools/run_01KY_test",
        "├──",
        "└──",
    ):
        assert expected in rendered
    assert "动态阶段看板" not in rendered
    assert "运行摘要" not in rendered
    assert re.search(r"(?m)^\s*\.\.\.\s*$", rendered) is None
    assert ".issue-solver-runs/run_test/diff.patch" not in rendered
    assert rendered.rfind("System · Setup") < rendered.rfind("Agent · PARSER")
    assert rendered.rfind("Agent · PARSER") < rendered.rfind("◆ Repair Round 01")
    assert rendered.rfind("System · Finalize") < rendered.rfind("Agent · REPORTER")
    assert re.search(r"\x1b\[[0-9;]*32m", output.getvalue())
    assert "\x1b[?25h" in output.getvalue()
    assert errors.getvalue() == ""


def test_report_result_uses_executor_role_for_fallback_and_failure() -> None:
    fallback_reporter, _, _, fallback_clock = make_reporter(quiet=True)
    fallback_reporter.report_started(agent_expected=True)
    fallback_clock.advance(1.5)
    fallback_reporter.report_completed(
        ReportResult(path="E:/runs/report.md", fallback_used=True),
        agent_attempted=True,
    )

    assert fallback_reporter._report_group is not None
    assert fallback_reporter._report_group.title == "Agent · REPORTER"
    fallback_step = fallback_reporter._report_group.steps[0]
    assert fallback_step.status == "warning"
    assert fallback_step.detail == "Fallback template"
    assert fallback_step.duration == 1.5

    template_reporter, _, _, _ = make_reporter(quiet=True)
    template_reporter.report_started(agent_expected=False)
    template_reporter.report_completed(
        ReportResult(path="E:/runs/report.md", fallback_used=True),
        agent_attempted=False,
    )
    assert template_reporter._report_group is not None
    assert template_reporter._report_group.title == "System · Report"
    assert template_reporter._report_group.steps[0].label == (
        "Generate fallback report"
    )
    assert template_reporter._report_group.steps[0].status == "warning"

    failed_reporter, _, _, _ = make_reporter(quiet=True)
    failed_reporter.report_started(agent_expected=True)
    failure = make_failure("INTERNAL", "无法保存报告。")
    failed_reporter.report_completed(
        ReportResult(path=None, fallback_used=False, failure=failure),
        agent_attempted=True,
    )

    assert failed_reporter._report_group is not None
    failed_step = failed_reporter._report_group.steps[0]
    assert failed_step.status == "failure"
    assert failed_step.detail == "无法保存报告。"


def test_rich_quiet_mode_skips_live_dashboard_but_keeps_result_panel() -> None:
    reporter, output, _, _ = make_reporter(quiet=True)
    begin(reporter)

    reporter.set_outcome(success=True, result={"phase": "FINALIZE"})
    reporter.summary(token_usage=token_usage(), total_duration=0.2)

    rendered = plain(output)
    assert "Issue Solver" not in rendered
    assert "Repair Round" not in rendered
    assert "Success" in rendered
    assert "Tokens" in rendered
    assert "not run" in rendered
    assert "deepseek-v4-flash" not in rendered
    assert "\x1b[?25l" not in output.getvalue()
    assert not re.search(r"\x1b\[[0-9;]*32m", output.getvalue())


def test_prepare_for_prompt_stops_live_with_failed_current_stage() -> None:
    reporter, output, _, _ = make_reporter()
    begin(reporter)
    reporter.start_timing("coding")

    reporter.prepare_for_prompt()

    rendered = plain(output)
    assert "运行失败，等待回滚决定" in rendered
    assert "\x1b[?25h" in output.getvalue()


def test_error_block_stops_dashboard_and_keeps_details_on_stderr() -> None:
    reporter, output, errors, _ = make_reporter()
    begin(reporter)
    reporter.start_timing("review")

    reporter.error_block("运行失败", [("原因", "模型不可用")])

    assert "模型不可用" in plain(output)
    assert "运行失败" in plain(errors)
    assert "模型不可用" in plain(errors)
    assert "\x1b[?25h" in output.getvalue()
    assert re.search(r"\x1b\[[0-9;]*31m", errors.getvalue())


def test_actionable_environment_failure_uses_yellow_warning_marker() -> None:
    reporter, output, errors, clock = make_reporter()
    begin(reporter)
    failure = make_failure("ENVIRONMENT", "目标虚拟环境未安装 pytest。")
    reporter.start_timing("preflight")
    clock.advance(0.5)

    reporter.preflight_failed(failure)
    reporter.error_block("环境预检失败", reporter.failure_details(failure))

    assert reporter._setup_steps["preflight"].status == "warning"
    assert "⚠" in plain(output)
    assert "⚠" in plain(errors)
    assert re.search(r"\x1b\[[0-9;]*33m", errors.getvalue())
    assert not re.search(r"\x1b\[[0-9;]*31m", errors.getvalue())


def test_dirty_worktree_is_warning_but_git_inspection_error_is_failure() -> None:
    dirty_reporter, _, _, _ = make_reporter(quiet=True)
    dirty_reporter.graph_started()
    dirty_reporter.handle_update(
        "initialize",
        {
            "status": "FAILED",
            "failure": make_failure("SAFETY", "Git 工作区存在未提交修改。"),
        },
    )
    assert dirty_reporter._setup_steps["initialize"].status == "warning"

    error_reporter, _, _, _ = make_reporter(quiet=True)
    error_reporter.graph_started()
    error_reporter.handle_update(
        "initialize",
        {
            "status": "FAILED",
            "failure": make_failure("ENVIRONMENT", "无法检查 Git 工作区状态。"),
        },
    )
    assert error_reporter._setup_steps["initialize"].status == "failure"


def test_rich_reporter_keeps_repeated_explore_stages() -> None:
    reporter, output, _, _ = make_reporter()
    begin(reporter)
    reporter.start_timing("coordinator")
    reporter.handle_update(
        "coordinator",
        {
            "next_action": "EXPLORE",
            "repair_round": 1,
            "explore_stage_call": 1,
            "explore_focuses": ["定位入口"],
            "explore_titles": ["定位入口"],
        },
    )
    reporter.handle_update(
        "explore",
        {
            "explore_reports": [SimpleNamespace(focus="定位入口")],
            "explore_executions": [
                explore_execution(
                    item_index=1,
                    focus="定位入口",
                    duration=0.5,
                )
            ],
        },
    )
    reporter.handle_update(
        "coordinator",
        {
            "next_action": "EXPLORE",
            "repair_round": 1,
            "explore_stage_call": 2,
            "explore_focuses": ["补充根因"],
            "explore_titles": ["补充根因"],
        },
    )
    reporter.handle_update(
        "explore",
        {
            "explore_reports": [SimpleNamespace(focus="补充根因")],
            "explore_executions": [
                explore_execution(
                    item_index=1,
                    focus="补充根因",
                    duration=0.7,
                    stage_call=2,
                )
            ],
        },
    )
    reporter.summary(token_usage=token_usage(), total_duration=1.0)

    rendered = plain(output)
    assert "Agent · 1 EXPLORER" in rendered
    assert rendered.count("Agent · 1 EXPLORER") >= 2
    assert rendered.count("Agent · COORDINATOR") >= 2
    assert "Plan 1 exploration tasks" in rendered
    assert "定位入口" in rendered
    assert "补充根因" in rendered


def test_coordinator_finish_and_failure_are_independent_timed_groups() -> None:
    finished, _, _, finished_clock = make_reporter(quiet=True)
    finished.start_timing("coordinator")
    finished_clock.advance(0.8)
    finished.handle_update(
        "coordinator",
        {"next_action": "FINISH", "repair_round": 1},
    )

    finish_group = finished._rounds[0].groups[0]
    assert finish_group.title == "Agent · COORDINATOR"
    assert finish_group.kind == "coordinator"
    assert finish_group.steps[0].label == "Complete repair"
    assert finish_group.steps[0].status == "success"
    assert finish_group.steps[0].duration == 0.8

    failed, _, _, failed_clock = make_reporter(quiet=True)
    failed.start_timing("coordinator")
    failed_clock.advance(0.6)
    failed.handle_update(
        "coordinator",
        {
            "status": "FAILED",
            "next_action": "FAILED",
            "repair_round": 2,
            "failure": make_failure("INTERNAL", "调度模型不可用。"),
        },
    )

    failure_group = failed._rounds[0].groups[0]
    failure_step = failure_group.steps[0]
    assert failure_group.title == "Agent · COORDINATOR"
    assert failure_step.label == "Coordinate workflow"
    assert failure_step.status == "failure"
    assert failure_step.detail == "调度模型不可用。"
    assert failure_step.duration == 0.6


def test_failed_coding_does_not_create_reviewer_group() -> None:
    reporter, _, _, clock = make_reporter(quiet=True)
    reporter.start_timing("coordinator")
    reporter.handle_update(
        "coordinator",
        {
            "next_action": "CODE",
            "repair_round": 1,
            "coding_stage_call": 1,
            "coding_task": SimpleNamespace(objective="修复搜索"),
        },
    )
    clock.advance(1.2)

    reporter.handle_update(
        "coding",
        {
            "status": "FAILED",
            "repair_round": 1,
            "coding_stage_call": 1,
            "failure": make_failure("SOLUTION", "无法生成有效补丁。"),
        },
    )

    assert reporter._latest_review_group() is None
    assert [group.kind for group in reporter._rounds[0].groups] == [
        "coordinator",
        "code",
    ]
    code_group = reporter._latest_code_group()
    assert code_group is not None
    code_step = next(step for step in code_group.steps if step.key == "coding")
    assert code_step.status == "failure"
    assert code_step.detail == "无法生成有效补丁。"


def test_rich_summary_uses_configured_run_root() -> None:
    reporter, output, _, _ = make_reporter(quiet=True, width=48)
    begin(reporter)
    reporter.set_outcome(
        success=True,
        result={
            "phase": "FINALIZE",
            "run_dir": "E:/very/long/project/path/cachetools/run_01KY_test",
        },
    )

    reporter.summary(token_usage=token_usage(), total_duration=1.0)

    rendered = plain(output)
    assert reporter._display_run_directory() == (
        ".issue-solver-runs/cachetools/run_01KY_test"
    )
    assert "E:/very/long" not in rendered


def test_rich_failed_summary_is_concise_and_keeps_chinese_reason() -> None:
    reporter, output, _, _ = make_reporter(quiet=True)
    begin(reporter)
    reporter.set_outcome(
        success=False,
        result={
            "phase": "REVIEW",
            "repair_round": 2,
            "failure": make_failure("SOLUTION", "补丁仍然破坏兼容性。"),
        },
        worktree_status="已回滚",
    )

    reporter.summary(token_usage=token_usage(), total_duration=9.5)

    rendered = plain(output)
    for expected in (
        "Failed",
        "Failed phase",
        "REVIEW",
        "Reason",
        "补丁仍然破坏兼容性。",
        "Worktree",
        "已回滚",
        "Repair rounds",
        "Tests",
        "not run",
        "Tokens",
        "900,000 input · 84,801 output",
        "Duration",
        "9.50s",
    ):
        assert expected in rendered
    assert "Patch" not in rendered
    assert not re.search(r"\x1b\[[0-9;]*31m", output.getvalue())


def test_successful_nonfinished_finalize_is_not_rendered_as_failure() -> None:
    reporter, output, _, _ = make_reporter()
    begin(reporter)

    reporter.handle_update(
        "finalize",
        {
            "status": "FAILED",
            "rollback_required": False,
            "changed_files": ["src/search.py"],
        },
    )
    reporter.summary(token_usage=token_usage(), total_duration=1.0)

    assert "Finalize —" not in plain(output)
    assert reporter._finalize_group is not None
    step = reporter._finalize_group.steps[0]
    assert step.status == "success"
    assert step.label == "Preserve changes and save artifacts"
