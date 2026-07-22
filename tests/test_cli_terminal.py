from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from cli.terminal import TerminalReporter
from schemas.environment_info import EnvironmentInfo
from schemas.failure import make_failure
from services.report import ReportResult
from services.token_usage import RoleTokenUsage, TokenUsageSummary


def token_usage(
    *,
    total: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> TokenUsageSummary:
    return TokenUsageSummary(
        total_tokens=total,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        role_usages=(
            RoleTokenUsage(
                role="Parser",
                total_tokens=total,
                percentage=100.0 if total else 0.0,
            ),
        ),
    )


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def environment() -> EnvironmentInfo:
    return EnvironmentInfo(
        kind="VENV",
        root_path="E:/repo/.venv",
        python_executable="E:/repo/.venv/Scripts/python.exe",
        pytest_version="pytest 9.1.1",
        source=".venv",
    )


def detail_value(rendered: str, label: str) -> str:
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("  │ ") or label not in line:
            continue
        value = line.split(label, 1)[1].lstrip()
        continuation = "  │ " + " " * 10
        for following in lines[index + 1 :]:
            if not following.startswith(continuation):
                break
            value += following[len(continuation) :]
        return value
    raise AssertionError(f"未找到详情字段：{label}")


def test_header_combines_program_model_and_run_id() -> None:
    output = StringIO()
    reporter = TerminalReporter(stdout=output, width=72)

    reporter.begin_run(
        model_name="deepseek-reasoner",
        run_id="run_test",
        repo_path="E:/repo",
        run_dir="E:/runs/run_test",
    )

    assert output.getvalue().splitlines()[0] == (
        "issue-solver · deepseek-reasoner · run_test"
    )
    assert "目标仓库  E:/repo" in output.getvalue()


def test_reporter_groups_rounds_and_records_stage_durations() -> None:
    output = StringIO()
    clock = FakeClock()
    reporter = TerminalReporter(stdout=output, clock=clock, width=72)
    reporter.begin_run(
        model_name="test-model",
        run_id="run_test",
        repo_path="E:/repo",
        run_dir="E:/runs/run_test",
    )

    reporter.start_timing("preflight")
    clock.advance(0.5)
    reporter.preflight_succeeded(environment())
    reporter.graph_started()
    clock.advance(0.2)
    reporter.handle_update(
        "initialize",
        {"project_type": "python", "test_commands": ["pytest -q"]},
    )
    clock.advance(2.0)
    reporter.handle_update(
        "parse_issue",
        {"issue": SimpleNamespace(title="搜索忽略大小写")},
    )
    clock.advance(1.0)
    reporter.handle_update(
        "coordinator",
        {
            "next_action": "EXPLORE",
            "repair_round": 1,
            "explore_stage_call": 1,
            "explore_focuses": ["定位入口", "定位测试"],
        },
    )
    clock.advance(3.0)
    reporter.handle_update(
        "explore",
        {"explore_reports": [SimpleNamespace(focus="定位入口")]},
    )
    clock.advance(2.0)
    reporter.handle_update(
        "explore",
        {"explore_reports": [SimpleNamespace(focus="定位测试")]},
    )

    text = output.getvalue()
    assert "✓ 环境预检 · 0.50 秒" in text
    assert detail_value(text, "解释器") == str(
        Path(".venv/Scripts/python.exe")
    )
    assert "✓ 初始化仓库 · 0.20 秒" in text
    assert "✓ 解析 Issue · 2.00 秒" in text
    assert text.count("◆ 修复轮次 r01") == 1
    assert "✓ EXPLORE · 1.00 秒" in text
    assert "✓ i01/02  定位入口" in text
    assert "✓ i02/02  定位测试" in text
    assert "✓ Explore s01 完成 · 5.00 秒" in text


def test_reporter_displays_review_test_and_finalize_results() -> None:
    output = StringIO()
    clock = FakeClock()
    reporter = TerminalReporter(stdout=output, clock=clock, width=72)
    reporter.begin_run(
        model_name="test-model",
        run_id="run_test",
        repo_path="E:/repo",
        run_dir="E:/runs/run_test",
    )

    reporter.start_timing("review")
    clock.advance(1.25)
    reporter.handle_update(
        "review",
        {"review_result": SimpleNamespace(verdict="APPROVE", issues=[])},
    )
    clock.advance(4.5)
    reporter.handle_update(
        "test",
        {
            "next_action": "FINISH",
            "latest_test_results": [
                SimpleNamespace(
                    status="PASSED",
                    duration=4.25,
                    command=(
                        "pytest -q "
                        "tests/test_search.py::test_ignores_case "
                        "tests/test_search.py::test_returns_matches "
                        "tests/test_search.py::test_preserves_order "
                        "tests/test_search.py::test_returns_empty"
                    ),
                    resolved_command=[
                        "E:/repo/.venv/Scripts/python.exe",
                        "-m",
                        "pytest",
                        "-q",
                        "--basetemp=E:/runs/run_test/basetemp",
                    ],
                    stdout_path="E:/runs/run_test/test.stdout.log",
                    stderr_path="E:/runs/run_test/test.stderr.log",
                ),
                SimpleNamespace(
                    status="PASSED",
                    duration=0.2,
                    command="pytest -q",
                    resolved_command=[
                        "E:/repo/.venv/Scripts/python.exe",
                        "-m",
                        "pytest",
                        "-q",
                    ],
                    stdout_path="E:/runs/run_test/regression.stdout.log",
                    stderr_path="E:/runs/run_test/regression.stderr.log",
                ),
            ]
        },
    )
    clock.advance(0.1)
    reporter.handle_update(
        "finalize",
        {"status": "FINISHED", "diff_path": "E:/runs/run_test/final.patch"},
    )

    text = output.getvalue()
    assert "✓ APPROVE · 1.25 秒" in text
    assert "✓ i01  定向测试 · PASSED · 4.25 秒" in text
    assert "│ 命令：pytest -q tests/test_search.py（4 项）" in text
    assert "✓ i02  全量回归 · PASSED · 0.20 秒" in text
    assert "│ 命令：pytest -q" in text
    assert ".venv/Scripts/python.exe" not in text
    assert "--basetemp" not in text
    assert "stdout" not in text
    assert "stderr" not in text
    assert "✓ Test 完成 · 4.50 秒" in text
    assert "Coordinator" not in text
    assert "✓ FINISH" not in text
    assert "✓ Finalize · 0.10 秒" in text
    assert "E:/runs/run_test/final.patch" in text


def test_reporter_distinguishes_finalize_workspace_outcomes() -> None:
    output = StringIO()
    reporter = TerminalReporter(stdout=output, width=72)

    reporter.handle_update(
        "finalize",
        {
            "status": "FAILED",
            "rollback_required": False,
            "rollback_success": False,
            "changed_files": [],
        },
    )
    reporter.handle_update(
        "finalize",
        {
            "status": "FAILED",
            "rollback_required": True,
            "rollback_success": True,
            "changed_files": [],
        },
    )
    reporter.handle_update(
        "finalize",
        {
            "status": "FAILED",
            "rollback_required": False,
            "rollback_success": False,
            "changed_files": ["tracked.txt"],
        },
    )

    text = output.getvalue()
    assert "未产生修改，无需回滚" in text
    assert "失败修改已回滚到 base commit" in text
    assert "失败修改已保留，等待回滚决定" in text


def test_reporter_displays_rollback_failure() -> None:
    output = StringIO()
    reporter = TerminalReporter(stdout=output, width=72)

    reporter.handle_update(
        "finalize",
        {
            "status": "FAILED",
            "rollback_required": True,
            "rollback_success": False,
            "failure": make_failure("LIMIT", "达到最大循环次数 5"),
            "rollback_failure": make_failure("SAFETY", "HEAD 已发生变化"),
            "changed_files": ["tracked.txt"],
        },
    )

    text = output.getvalue()
    assert "✗ Finalize 失败" in text
    assert "HEAD 已发生变化" in text
    assert "达到最大循环次数" not in text


def test_reporter_compacts_targets_from_multiple_test_files() -> None:
    output = StringIO()
    reporter = TerminalReporter(stdout=output, width=72)
    reporter.start_timing("test")

    reporter.handle_update(
        "test",
        {
            "latest_test_results": [
                SimpleNamespace(
                    status="FAILED",
                    duration=0.3,
                    command=(
                        "pytest -q "
                        "tests/test_a.py::test_one "
                        "tests/test_b.py::test_two "
                        "tests/test_c.py::test_three "
                        "tests/test_c.py::test_four"
                    ),
                )
            ]
        },
    )

    text = output.getvalue()
    assert "✗ i01  定向测试 · FAILED · 0.30 秒" in text
    assert "pytest -q tests/test_a.py 等 3 个文件（4 项）" in text


def test_summary_keeps_model_tokens_and_result_in_quiet_mode() -> None:
    output = StringIO()
    reporter = TerminalReporter(quiet=True, stdout=output, width=72)
    reporter.begin_run(
        model_name="test-model",
        run_id="run_test",
        repo_path="E:/repo",
        run_dir="E:/runs/run_test",
    )
    reporter.set_outcome(
        success=True,
        result={
            "phase": "FINALIZE",
            "repair_round": 1,
            "changed_files": ["src/search.py", "tests/test_search.py"],
            "latest_test_results": [SimpleNamespace(status="PASSED")],
        },
        worktree_status="保留修改",
    )

    reporter.summary(
        token_usage=token_usage(
            total=18742,
            input_tokens=14106,
            output_tokens=4636,
        ),
        total_duration=48.09,
    )

    text = output.getvalue()
    assert "issue-solver" not in text
    assert "运行摘要" in text
    assert "成功" in text
    assert "test-model" in text
    assert "run_test" in text
    assert "保留修改" in text
    assert "18,742" in text
    assert "14,106 / 4,636" in text
    assert "48.09 秒" in text
    assert text.index("最终耗时") < text.index("Token（总/输入/输出）")


def test_error_block_uses_stderr() -> None:
    output = StringIO()
    errors = StringIO()
    reporter = TerminalReporter(stdout=output, stderr=errors)

    reporter.error_block("运行失败", [("原因", "模型不可用")])

    assert output.getvalue() == ""
    assert errors.getvalue() == "✗ 运行失败\n  │ 原因：模型不可用\n"


def test_reporter_adds_one_blank_line_before_first_output() -> None:
    output = StringIO()
    errors = StringIO()
    reporter = TerminalReporter(
        stdout=output,
        stderr=errors,
        leading_blank=True,
    )

    reporter.error_block("运行失败", [("原因", "模型不可用")])

    assert output.getvalue() == ""
    assert errors.getvalue() == "\n✗ 运行失败\n  │ 原因：模型不可用\n"


def test_quiet_summary_does_not_duplicate_leading_blank() -> None:
    output = StringIO()
    reporter = TerminalReporter(
        quiet=True,
        stdout=output,
        leading_blank=True,
        width=72,
    )

    reporter.summary(token_usage=token_usage(), total_duration=0.04)

    assert output.getvalue().startswith("\n─")
    assert not output.getvalue().startswith("\n\n")


def test_wide_terminal_keeps_run_directory_on_one_line() -> None:
    output = StringIO()
    run_dir = (
        "E:\\Products\\own\\Issue-to-Solution\\.issue-solver-runs\\"
        "search-demo\\run_01KXZZQGVFZZXF12X4GENHPN6N"
    )
    reporter = TerminalReporter(quiet=True, stdout=output, width=165)

    reporter.begin_run(
        model_name="test-model",
        run_id="run_01KXZZQGVFZZXF12X4GENHPN6N",
        repo_path="E:/repo",
        run_dir=run_dir,
    )
    reporter.summary(token_usage=token_usage(), total_duration=1.0)

    assert reporter.width == 120
    assert f"运行目录  {run_dir}" in output.getvalue().splitlines()


def test_report_result_is_shown_in_progress_and_summary() -> None:
    output = StringIO()
    reporter = TerminalReporter(stdout=output, width=120)
    report_path = "E:/runs/run_test/report.md"

    reporter.start_timing("report")
    reporter.report_completed(
        ReportResult(
            path=report_path,
            fallback_used=False,
        )
    )
    reporter.summary(
        token_usage=token_usage(total=10, input_tokens=6, output_tokens=4),
        total_duration=1.0,
    )

    rendered = output.getvalue()
    assert "✓ Report" in rendered
    assert report_path not in rendered.split("运行摘要", maxsplit=1)[1]


def test_report_fallback_is_hidden_from_quiet_progress() -> None:
    output = StringIO()
    reporter = TerminalReporter(quiet=True, stdout=output, width=120)

    reporter.start_timing("report")
    reporter.report_completed(
        ReportResult(
            path="E:/runs/run_test/report.md",
            fallback_used=True,
            failure=make_failure("MODEL", "模型不可用"),
        )
    )
    reporter.summary(token_usage=token_usage(), total_duration=1.0)

    rendered = output.getvalue()
    assert "使用程序模板" not in rendered
    assert "E:/runs/run_test/report.md" not in rendered


def test_details_wrap_without_truncating_long_values() -> None:
    output = StringIO()
    reporter = TerminalReporter(stdout=output, width=48)
    long_path = "E:/very/long/project/path/that/must/remain/complete/output.log"

    reporter.begin_run(
        model_name="test-model",
        run_id="run_test",
        repo_path="E:/repo",
        run_dir="E:/runs/run_test",
    )
    reporter.start_timing("preflight")
    reporter.preflight_succeeded(
        EnvironmentInfo(
            kind="VENV",
            root_path="E:/repo/.venv",
            python_executable=long_path,
            pytest_version="pytest 9.1.1",
            source=".venv",
        )
    )

    rendered = output.getvalue()
    assert detail_value(rendered, "解释器") == long_path
