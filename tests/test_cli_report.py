from io import StringIO
from pathlib import Path
from unittest.mock import Mock

from cli import report as report_module
from cli.report import RunReportSession
from cli.terminal import TerminalReporter
from services.report import ReportResult


def test_report_session_uses_template_when_agent_build_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_to_build(model: object) -> None:
        raise RuntimeError("无法构建 Reporter")

    monkeypatch.setattr(
        report_module,
        "build_report_agent",
        fail_to_build,
    )
    output = StringIO()
    session = RunReportSession(
        run_dir=tmp_path,
        model_name="test-model",
        reporter=TerminalReporter(stdout=output),
    )

    result = session.finish(
        state={
            "run_id": "run_test",
            "status": "FAILED",
            "phase": "TEST",
            "issue_input": "修复问题",
        },
        model=object(),
        worktree_status="未修改",
    )

    assert result.path == str(tmp_path / "report.md")
    assert result.fallback_used is True
    assert "无法构建 Reporter" in result.failure.message
    assert "使用程序模板" in output.getvalue()


def test_report_session_only_writes_once(tmp_path: Path) -> None:
    session = RunReportSession(
        run_dir=tmp_path,
        model_name=None,
        reporter=TerminalReporter(quiet=True, stdout=StringIO()),
    )
    state = {
        "run_id": "run_test",
        "status": "FAILED",
        "phase": "INITIALIZE",
        "issue_input": "修复问题",
    }

    first = session.finish(
        state=state,
        model=None,
        worktree_status="未修改",
    )
    second = session.finish(
        state=state,
        model=None,
        worktree_status="未修改",
    )

    assert first.path == str(tmp_path / "report.md")
    assert second.path is None
    assert second.failure.message == "本次运行的报告已经生成。"
    assert (tmp_path / "report.md").is_file()


def test_report_session_uses_non_thinking_model(monkeypatch, tmp_path: Path) -> None:
    model = object()
    non_thinking_model = object()
    report_agent = object()
    instrumented_agent = object()
    received_models: list[object] = []
    token_usage = Mock()
    token_usage.with_role.return_value = instrumented_agent

    monkeypatch.setattr(
        report_module,
        "build_non_thinking_model",
        lambda value: non_thinking_model,
    )
    monkeypatch.setattr(
        report_module,
        "build_report_agent",
        lambda value: received_models.append(value) or report_agent,
    )
    session = RunReportSession(
        run_dir=tmp_path,
        model_name="test-model",
        reporter=TerminalReporter(quiet=True, stdout=StringIO()),
        token_usage=token_usage,
    )

    session.finish(
        state={
            "run_id": "run_test",
            "status": "FAILED",
            "phase": "TEST",
            "issue_input": "修复问题",
        },
        model=model,
        worktree_status="未修改",
    )

    assert received_models == [non_thinking_model]
    token_usage.with_role.assert_called_once_with(report_agent, "Reporter")


def test_report_session_reports_actual_executor(monkeypatch, tmp_path: Path) -> None:
    result = ReportResult(path=str(tmp_path / "report.md"), fallback_used=False)
    monkeypatch.setattr(
        report_module,
        "build_non_thinking_model",
        lambda value: value,
    )
    monkeypatch.setattr(
        report_module,
        "build_report_agent",
        lambda value: value,
    )
    monkeypatch.setattr(
        report_module,
        "create_run_report",
        Mock(return_value=result),
    )
    state = {"status": "FINISHED", "phase": "FINALIZE"}

    agent_reporter = Mock(spec=TerminalReporter)
    RunReportSession(
        run_dir=tmp_path,
        model_name="test-model",
        reporter=agent_reporter,
    ).finish(
        state=state,
        model=object(),
        worktree_status="保留修改",
    )

    agent_reporter.report_started.assert_called_once_with(agent_expected=True)
    agent_reporter.report_completed.assert_called_once_with(
        result,
        agent_attempted=True,
    )

    system_reporter = Mock(spec=TerminalReporter)
    RunReportSession(
        run_dir=tmp_path,
        model_name=None,
        reporter=system_reporter,
    ).finish(
        state=state,
        model=None,
        worktree_status="未修改",
    )

    system_reporter.report_started.assert_called_once_with(agent_expected=False)
    system_reporter.report_completed.assert_called_once_with(
        result,
        agent_attempted=False,
    )
