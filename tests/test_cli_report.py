from io import StringIO
from pathlib import Path

from cli import report as report_module
from cli.report import RunReportSession
from cli.terminal import TerminalReporter


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
